//! PyEngine：CompositeEngine 的 Python 门面；PyDb：MeridianDb 的 Python 门面。
//!
//! Python 使用方式：
//! ```python
//! import meridian_core as mc
//! engine = mc.PyEngine("config/scoring/stock.yaml")
//! engine.add_builtin_models()                    # 4 个 Rust 规则模型
//! engine.add_python_model("py_dummy_v1", my_model, "opportunity")
//! result = engine.evaluate(symbol=..., ...)       # 三层评分 dict
//! ```

use meridian_core::{Asset, AssetType, Channel, Frequency, Market, ModelCategory, Regime};
use meridian_quant_engine::{CompositeEngine, RegisteredModel, ScoringConfig};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::conversions::{bars_from_py, composite_to_py};
use crate::py_model::PyAnalysisModel;

fn parse_market(s: &str) -> PyResult<Market> {
    s.parse::<Market>()
        .map_err(|e| PyValueError::new_err(format!("{e}")))
}

fn parse_asset_type(s: &str) -> PyResult<AssetType> {
    s.parse::<AssetType>()
        .map_err(|e| PyValueError::new_err(format!("{e}")))
}

fn parse_frequency(s: &str) -> PyResult<Frequency> {
    s.parse::<Frequency>()
        .map_err(|e| PyValueError::new_err(format!("{e}")))
}

/// asset 字段组 → Asset（evaluate / insert_bars 共用）。
#[allow(clippy::too_many_arguments)]
fn asset_from_parts(
    symbol: &str,
    name: &str,
    market: &str,
    asset_type: &str,
    frequency: &str,
) -> PyResult<Asset> {
    Ok(Asset::new(
        symbol,
        name,
        parse_market(market)?,
        parse_asset_type(asset_type)?,
        parse_frequency(frequency)?,
    ))
}

#[pyclass]
pub struct PyEngine {
    engine: CompositeEngine,
    models: Vec<RegisteredModel>,
}

#[pymethods]
impl PyEngine {
    /// 从评分配置文件（config/scoring/{asset_type}.yaml）构建引擎。
    #[new]
    fn new(scoring_yaml_path: &str) -> PyResult<Self> {
        let config = ScoringConfig::from_yaml_file(scoring_yaml_path)
            .map_err(|e| PyValueError::new_err(format!("加载评分配置失败: {e}")))?;
        Ok(Self {
            engine: CompositeEngine::new(config),
            models: Vec::new(),
        })
    }

    /// 从 YAML 字符串构建（测试用）。
    #[staticmethod]
    fn from_yaml_str(yaml: &str) -> PyResult<Self> {
        let config = ScoringConfig::from_yaml_str(yaml)
            .map_err(|e| PyValueError::new_err(format!("解析评分配置失败: {e}")))?;
        Ok(Self {
            engine: CompositeEngine::new(config),
            models: Vec::new(),
        })
    }

    /// 注册 4 个内置规则模型：trend/momentum/capital → 机会通道，risk → 风险通道。
    fn add_builtin_models(&mut self) {
        use meridian_quant_engine::{CapitalModel, MomentumModel, RiskModel, TrendModel};
        self.models.push(RegisteredModel::new(
            Box::new(TrendModel::new()),
            Channel::Opportunity,
        ));
        self.models.push(RegisteredModel::new(
            Box::new(MomentumModel::new()),
            Channel::Opportunity,
        ));
        self.models.push(RegisteredModel::new(
            Box::new(CapitalModel::new()),
            Channel::Opportunity,
        ));
        self.models
            .push(RegisteredModel::new(Box::new(RiskModel::new()), Channel::Risk));
    }

    /// 注册 Python 模型：callback 需实现 analyze(payload: dict) -> dict（见 py_model.rs）。
    /// channel = "opportunity" | "risk"；category = "rule" | "ai_prediction"（默认 ai_prediction）。
    #[pyo3(signature = (name, callback, channel, version="0.1.0", category="ai_prediction"))]
    fn add_python_model(
        &mut self,
        name: &str,
        callback: Py<PyAny>,
        channel: &str,
        version: &str,
        category: &str,
    ) -> PyResult<()> {
        let channel = match channel.trim().to_ascii_lowercase().as_str() {
            "opportunity" => Channel::Opportunity,
            "risk" => Channel::Risk,
            other => {
                return Err(PyValueError::new_err(format!(
                    "未知评分通道: {other}（应为 opportunity / risk）"
                )));
            }
        };
        let category = match category.trim().to_ascii_lowercase().as_str() {
            "rule" => ModelCategory::Rule,
            "ai_prediction" | "ai" => ModelCategory::AiPrediction,
            other => {
                return Err(PyValueError::new_err(format!(
                    "未知模型类别: {other}（应为 rule / ai_prediction）"
                )));
            }
        };
        let model = PyAnalysisModel::new(name.to_string(), version.to_string(), category, callback);
        self.models.push(RegisteredModel::new(Box::new(model), channel));
        Ok(())
    }

    /// 已注册模型名列表。
    fn registered_models(&self) -> Vec<String> {
        self.models
            .iter()
            .map(|m| m.model.name().to_string())
            .collect()
    }

    /// 生效配置指纹（sha256 前 16 位）。
    fn config_fingerprint(&self) -> String {
        self.engine.config().fingerprint()
    }

    /// 三层评分：拉数后由 Python 传入 K 线，Rust 内部算指标 → 合成 → dict。
    #[pyo3(signature = (symbol, name, market, asset_type, frequency,
                        dates, opens, highs, lows, closes, volumes, amounts,
                        regime="unknown"))]
    #[allow(clippy::too_many_arguments)]
    fn evaluate(
        &self,
        symbol: &str,
        name: &str,
        market: &str,
        asset_type: &str,
        frequency: &str,
        dates: Vec<String>,
        opens: Vec<f64>,
        highs: Vec<f64>,
        lows: Vec<f64>,
        closes: Vec<f64>,
        volumes: Vec<f64>,
        amounts: Vec<f64>,
        regime: &str,
    ) -> PyResult<Py<PyAny>> {
        let asset = asset_from_parts(symbol, name, market, asset_type, frequency)?;
        let bars = bars_from_py(dates, opens, highs, lows, closes, volumes, amounts)?;

        let regime_parsed = regime
            .parse::<Regime>()
            .map_err(|e| PyValueError::new_err(format!("{e}")))?;
        let ctx = meridian_core::AnalysisContext {
            asset: &asset,
            regime: meridian_core::RegimeState {
                regime: regime_parsed,
                confidence: if regime_parsed == Regime::Unknown { 0.0 } else { 0.9 },
            },
            bars: &bars,
            indicators: &meridian_indicators::build_snapshot(&bars),
        };

        let score = self
            .engine
            .evaluate(&self.models, &ctx)
            .map_err(|e| PyValueError::new_err(format!("评分失败: {e}")))?;
        composite_to_py(&score)
    }
}

/// DuckDB 存储门面（热数据：bars）。Connection 非 Sync，用 Mutex 满足 pyclass 约束。
#[pyclass]
pub struct PyDb {
    db: std::sync::Mutex<meridian_storage::MeridianDb>,
}

#[pymethods]
impl PyDb {
    #[staticmethod]
    fn open(path: &str) -> PyResult<Self> {
        let db = meridian_storage::MeridianDb::open(path)
            .map_err(|e| PyValueError::new_err(format!("{e}")))?;
        Ok(Self {
            db: std::sync::Mutex::new(db),
        })
    }

    #[staticmethod]
    fn open_in_memory() -> PyResult<Self> {
        let db = meridian_storage::MeridianDb::open_in_memory()
            .map_err(|e| PyValueError::new_err(format!("{e}")))?;
        Ok(Self {
            db: std::sync::Mutex::new(db),
        })
    }

    /// 批量写入 K 线（UPSERT）。返回写入行数。
    #[allow(clippy::too_many_arguments)]
    fn insert_bars(
        &self,
        symbol: &str,
        name: &str,
        market: &str,
        asset_type: &str,
        frequency: &str,
        dates: Vec<String>,
        opens: Vec<f64>,
        highs: Vec<f64>,
        lows: Vec<f64>,
        closes: Vec<f64>,
        volumes: Vec<f64>,
        amounts: Vec<f64>,
    ) -> PyResult<usize> {
        let asset = asset_from_parts(symbol, name, market, asset_type, frequency)?;
        let bars = bars_from_py(dates, opens, highs, lows, closes, volumes, amounts)?;
        self.db
            .lock()
            .expect("PyDb 锁中毒")
            .insert_bars(&asset, &bars)
            .map_err(|e| PyValueError::new_err(format!("{e}")))
    }

    /// 读取 K 线 → list[dict]（升序），便于 pandas 消费。
    #[allow(clippy::too_many_arguments)]
    fn read_bars(
        &self,
        symbol: &str,
        name: &str,
        market: &str,
        asset_type: &str,
        frequency: &str,
        start: &str,
        end: &str,
    ) -> PyResult<Vec<Py<PyAny>>> {
        let asset = asset_from_parts(symbol, name, market, asset_type, frequency)?;
        let start = chrono::NaiveDate::parse_from_str(start, "%Y-%m-%d")
            .map_err(|e| PyValueError::new_err(format!("start 日期非法: {e}")))?;
        let end = chrono::NaiveDate::parse_from_str(end, "%Y-%m-%d")
            .map_err(|e| PyValueError::new_err(format!("end 日期非法: {e}")))?;

        let bars = self
            .db
            .lock()
            .expect("PyDb 锁中毒")
            .read_bars(&asset, start, end)
            .map_err(|e| PyValueError::new_err(format!("{e}")))?;

        Ok(bars
            .into_iter()
            .map(|b| {
                pyo3::Python::attach(|py| {
                    let d = pyo3::types::PyDict::new(py);
                    d.set_item("date", b.date.format("%Y-%m-%d").to_string())?;
                    d.set_item("open", b.open)?;
                    d.set_item("high", b.high)?;
                    d.set_item("low", b.low)?;
                    d.set_item("close", b.close)?;
                    d.set_item("volume", b.volume)?;
                    d.set_item("amount", b.amount)?;
                    Ok(d.into_any().unbind())
                })
            })
            .collect::<PyResult<Vec<_>>>()?)
    }

    /// 某标的K线行数（测试/巡检用）。
    #[pyo3(signature = (symbol, market, frequency))]
    fn bar_count(&self, symbol: &str, market: &str, frequency: &str) -> PyResult<i64> {
        let m = parse_market(market)?;
        let freq = parse_frequency(frequency)?;
        self.db
            .lock()
            .expect("PyDb 锁中毒")
            .conn()
            .query_row(
                "SELECT count(*) FROM bars WHERE market = ?1 AND symbol = ?2 AND frequency = ?3",
                [m.as_str(), symbol, freq.as_str()],
                |r| r.get(0),
            )
            .map_err(|e| PyValueError::new_err(format!("查询失败: {e}")))
    }
}
