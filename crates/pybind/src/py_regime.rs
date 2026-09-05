//! 市场状态检测的 Python 门面：PyRegimeDetector。
//! 阈值经构造参数传入（config/regime.yaml 由 Python 读取后传），红线 4：零硬编码路径。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use meridian_quant_engine::market_regime::{RegimeDetector, RegimeThresholds, TrendVolDetector};

use crate::conversions::bars_from_py;

/// 规则市场状态检测器（trend_vol_v1）：
/// 趋势（MA 快慢线）× 波动（ATR 占比）× 急跌（窗内回撤）→ Bull/Bear/Sideways/HighVol/Crisis。
/// 检测输入为标的自身K线（一期代理；接指数数据源后换输入不换代码）。
#[pyclass]
pub struct PyRegimeDetector {
    detector: TrendVolDetector,
}

#[pymethods]
impl PyRegimeDetector {
    #[new]
    #[pyo3(signature = (
        trend_ma_fast = 20, trend_ma_slow = 60, trend_band = 0.03,
        drawdown_window = 20, crisis_drawdown = 0.10, atr_period = 14,
        atr_pct_crisis = 0.035, atr_pct_high_vol = 0.025,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        trend_ma_fast: usize,
        trend_ma_slow: usize,
        trend_band: f64,
        drawdown_window: usize,
        crisis_drawdown: f64,
        atr_period: usize,
        atr_pct_crisis: f64,
        atr_pct_high_vol: f64,
    ) -> PyResult<Self> {
        if trend_ma_fast == 0 || trend_ma_slow == 0 || trend_ma_fast > trend_ma_slow {
            return Err(PyValueError::new_err(format!(
                "均线窗口非法: fast={trend_ma_fast}, slow={trend_ma_slow}（需 0 < fast ≤ slow）"
            )));
        }
        if !(0.0..=1.0).contains(&trend_band)
            || !(0.0..=1.0).contains(&crisis_drawdown)
            || !(0.0..=1.0).contains(&atr_pct_crisis)
            || !(0.0..=1.0).contains(&atr_pct_high_vol)
        {
            return Err(PyValueError::new_err(
                "阈值需在 [0,1] 内（比例语义）: trend_band/crisis_drawdown/atr_pct_*",
            ));
        }
        Ok(Self {
            detector: TrendVolDetector::new(RegimeThresholds {
                trend_ma_fast,
                trend_ma_slow,
                trend_band,
                drawdown_window,
                crisis_drawdown,
                atr_period,
                atr_pct_crisis,
                atr_pct_high_vol,
            }),
        })
    }

    /// 检测市场状态 → {regime, confidence, basis: [判定依据...], detector}。
    /// K线窗口不足（< 慢线窗口）时返回 regime="unknown"、confidence=0。
    #[allow(clippy::too_many_arguments)]
    fn detect(
        &self,
        dates: Vec<String>,
        opens: Vec<f64>,
        highs: Vec<f64>,
        lows: Vec<f64>,
        closes: Vec<f64>,
        volumes: Vec<f64>,
        amounts: Vec<f64>,
    ) -> PyResult<Py<PyAny>> {
        let bars = bars_from_py(dates, opens, highs, lows, closes, volumes, amounts)?;
        let state = self.detector.detect(&bars);

        Python::attach(|py| {
            let d = PyDict::new(py);
            d.set_item("regime", state.regime.as_str())?;
            d.set_item("confidence", state.confidence)?;
            d.set_item("basis", state.basis)?;
            d.set_item("detector", self.detector.name())?;
            Ok(d.into())
        })
    }
}
