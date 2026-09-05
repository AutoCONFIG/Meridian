//! PyAnalysisModel：Python 模型 → Rust `AnalysisModel` trait 桥接。
//!
//! Python 模型协议（哑模型 / Phase 4 AI 预测模型统一遵守）：
//! - Python 侧实现 `analyze(payload: dict) -> dict`（或直接为可调用对象）
//! - payload = {"asset": {...}, "regime": {"regime", "confidence"},
//!              "bars_count": int, "last_close": float|None, "indicators": {末值快照}}
//! - 返回 {"score": 0-100, "direction": "up|down|neutral", "confidence": 0-1}
//!
//! 架构约束：Python 模型只能经 CompositeEngine 的机会/风险通道参与评分，
//! action 建议仍由 action_rules 规则匹配生成——AI 无法绕过风控。

use meridian_core::{
    AnalysisContext, AnalysisModel, MeridianError, ModelCategory, ModelOutput, Result,
};
use pyo3::prelude::*;

use crate::conversions::snapshot_last_values;

pub struct PyAnalysisModel {
    name: String,
    version: String,
    category: ModelCategory,
    /// Python 侧 analyze 回调（callable）
    callback: Py<PyAny>,
}

impl PyAnalysisModel {
    pub fn new(name: String, version: String, category: ModelCategory, callback: Py<PyAny>) -> Self {
        Self {
            name,
            version,
            category,
            callback,
        }
    }
}

impl AnalysisModel for PyAnalysisModel {
    fn name(&self) -> &str {
        &self.name
    }

    fn version(&self) -> &str {
        &self.version
    }

    fn category(&self) -> ModelCategory {
        self.category
    }

    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput> {
        let payload = build_payload(ctx)
            .map_err(|e| MeridianError::Bridge(format!("构造 Python 模型 payload 失败: {e}")))?;
        let output = Python::attach(|py| {
            let cb = self.callback.bind(py);
            // 优先调用对象的 .analyze(payload) 方法；无该方法则把对象当可调用对象
            let call = match cb.getattr("analyze") {
                Ok(method) => method.call1((payload,)),
                Err(_) => cb.call1((payload,)),
            };
            let returned = call
                .map_err(|e| MeridianError::Bridge(format!("Python 模型 {} 抛出异常: {e}", self.name)))?;
            crate::conversions::model_output_from_py(returned)
                .map_err(|e| MeridianError::Bridge(format!("Python 模型 {} 返回格式错误: {e}", self.name)))
        })?;
        Ok(output)
    }
}

/// AnalysisContext → Python 模型的输入 payload。
fn build_payload(ctx: &AnalysisContext) -> pyo3::PyResult<Py<PyAny>> {
    pyo3::Python::attach(|py| {
        let dict = pyo3::types::PyDict::new(py);

        let asset = pyo3::types::PyDict::new(py);
        asset.set_item("symbol", ctx.asset.symbol.clone())?;
        asset.set_item("name", ctx.asset.name.clone())?;
        asset.set_item("market", ctx.asset.market.as_str())?;
        asset.set_item("asset_type", ctx.asset.asset_type.as_str())?;
        asset.set_item("frequency", ctx.asset.frequency.as_str())?;
        dict.set_item("asset", asset)?;

        let regime = pyo3::types::PyDict::new(py);
        regime.set_item("regime", ctx.regime.regime.as_str())?;
        regime.set_item("confidence", ctx.regime.confidence)?;
        dict.set_item("regime", regime)?;

        dict.set_item("bars_count", ctx.bars.len())?;
        dict.set_item("last_close", ctx.bars.last().map(|b| b.close))?;
        dict.set_item("indicators", snapshot_last_values(ctx.indicators)?)?;
        // 收盘序列（时间序，供预测模型做回归/序列特征；与 indicators 末值快照互补）
        dict.set_item(
            "closes",
            ctx.bars.iter().map(|b| b.close).collect::<Vec<f64>>(),
        )?;

        Ok(dict.into_any().unbind())
    })
}
