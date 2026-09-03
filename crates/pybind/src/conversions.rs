//! Rust ↔ Python 类型转换：K 线进、评分结果出、指标末值快照供 Python 模型消费。

use chrono::NaiveDate;
use meridian_core::{Bar, CompositeScore, Factor, IndicatorSnapshot, ModelOutput};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// 从 7 条平行 Python 序列构造升序 Bar 序列。
/// 日期格式 YYYY-MM-DD；各序列长度必须一致。
pub fn bars_from_py(
    dates: Vec<String>,
    opens: Vec<f64>,
    highs: Vec<f64>,
    lows: Vec<f64>,
    closes: Vec<f64>,
    volumes: Vec<f64>,
    amounts: Vec<f64>,
) -> PyResult<Vec<Bar>> {
    let n = dates.len();
    let lens = [
        ("open", opens.len()),
        ("high", highs.len()),
        ("low", lows.len()),
        ("close", closes.len()),
        ("volume", volumes.len()),
        ("amount", amounts.len()),
    ];
    if let Some((name, len)) = lens.iter().find(|(_, l)| *l != n) {
        return Err(PyValueError::new_err(format!(
            "K线序列长度不一致: dates={n}, {name}={len}"
        )));
    }

    let mut bars = Vec::with_capacity(n);
    for i in 0..n {
        let date = NaiveDate::parse_from_str(&dates[i], "%Y-%m-%d").map_err(|e| {
            PyValueError::new_err(format!("日期格式应为 YYYY-MM-DD，得到 {}: {e}", dates[i]))
        })?;
        let bar = Bar::new(
            date, opens[i], highs[i], lows[i], closes[i], volumes[i], amounts[i],
        )
        .map_err(|e| PyValueError::new_err(format!("第 {i} 根K线非法: {e}")))?;
        bars.push(bar);
    }
    Ok(bars)
}

/// 指标快照各序列的末值（跳过末尾 None）→ Python 字典。
/// 这是 Python 模型（哑模型 / 未来 AI 特征消费）拿到的特征视图。
pub fn snapshot_last_values(ind: &IndicatorSnapshot) -> PyResult<Py<PyAny>> {
    Python::attach(|py| {
        let dict = pyo3::types::PyDict::new(py);
        let fields: [(&str, &[Option<f64>]); 21] = [
            ("sma20", &ind.sma20),
            ("sma60", &ind.sma60),
            ("ema12", &ind.ema12),
            ("ema26", &ind.ema26),
            ("macd_dif", &ind.macd_dif),
            ("macd_dea", &ind.macd_dea),
            ("macd_hist", &ind.macd_hist),
            ("rsi14", &ind.rsi14),
            ("atr14", &ind.atr14),
            ("adx14", &ind.adx14),
            ("plus_di14", &ind.plus_di14),
            ("minus_di14", &ind.minus_di14),
            ("boll_upper", &ind.boll_upper),
            ("boll_mid", &ind.boll_mid),
            ("boll_lower", &ind.boll_lower),
            ("vol_ma20", &ind.vol_ma20),
            ("ret_5d", &ind.ret_5d),
            ("ret_20d", &ind.ret_20d),
            ("annual_vol_20", &ind.annual_vol_20),
            ("high_20d", &ind.high_20d),
            ("low_20d", &ind.low_20d),
        ];
        for (name, series) in fields {
            dict.set_item(name, IndicatorSnapshot::last(series))?;
        }
        // 纯 f64 序列（总有值）
        dict.set_item("drawdown", ind.drawdown.last().copied())?;
        dict.set_item("obv", ind.obv.last().copied())?;
        Ok(dict.into_any().unbind())
    })
}

/// CompositeScore → Python 字典（与 Rust serde 结构同形，Markdown/落库直接消费）。
pub fn composite_to_py(score: &CompositeScore) -> PyResult<Py<PyAny>> {
    Python::attach(|py| {
        let root = pyo3::types::PyDict::new(py);

        let opp = pyo3::types::PyDict::new(py);
        opp.set_item("score", score.opportunity.score)?;
        opp.set_item("factors", factors_to_py(py, &score.opportunity.factors)?)?;
        root.set_item("opportunity", opp)?;

        let risk = pyo3::types::PyDict::new(py);
        risk.set_item("score", score.risk.score)?;
        risk.set_item("factors", factors_to_py(py, &score.risk.factors)?)?;
        root.set_item("risk", risk)?;

        let action = pyo3::types::PyDict::new(py);
        action.set_item("action", score.action.action.as_str())?;
        action.set_item("description", score.action.action.description())?;
        action.set_item("position_hint", score.action.position_hint)?;
        action.set_item("rule_triggers", score.action.rule_triggers.clone())?;
        root.set_item("action", action)?;

        root.set_item("model_version", score.model_version.clone())?;
        root.set_item("config_fingerprint", score.config_fingerprint.clone())?;
        Ok(root.into_any().unbind())
    })
}

fn factors_to_py(py: Python<'_>, factors: &[Factor]) -> PyResult<Py<PyAny>> {
    let list = pyo3::types::PyList::empty(py);
    for f in factors {
        let d = pyo3::types::PyDict::new(py);
        d.set_item("name", f.name.clone())?;
        d.set_item("value", f.value)?;
        d.set_item("contribution", f.contribution)?;
        d.set_item("description", f.description.clone())?;
        list.append(d)?;
    }
    Ok(list.into_any().unbind())
}

/// Python 回调返回的 dict（或任何映射）→ ModelOutput（协议见 py_model.rs 模块注释）。
/// 非有限值 / 越界值统一收敛（与 Rust `ModelOutput::normalized` 同口径）。
pub fn model_output_from_py(value: Bound<'_, PyAny>) -> PyResult<ModelOutput> {
    let obj = value;
    let score: f64 = obj.get_item("score")?.extract()?;
    let direction: String = obj
        .get_item("direction")
        .ok()
        .and_then(|v| v.extract::<String>().ok())
        .unwrap_or_else(|| "neutral".to_string());
    let confidence: f64 = obj
        .get_item("confidence")
        .ok()
        .and_then(|v| v.extract::<f64>().ok())
        .unwrap_or(0.5);

    let score = if score.is_finite() { score.clamp(0.0, 100.0) } else { 50.0 };
    let confidence = if confidence.is_finite() {
        confidence.clamp(0.0, 1.0)
    } else {
        0.0
    };
    let direction = direction
        .parse::<meridian_core::Direction>()
        .map_err(|e| PyValueError::new_err(format!("{e}")))?;

    Ok(ModelOutput {
        score,
        direction,
        confidence,
        factors: Vec::new(),
    })
}
