//! 回测的 Python 门面：PyBacktester。
//! 撮合/统计在 meridian-backtest；action→目标仓位的策略语义在 Python/config 侧。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use meridian_backtest::{run_backtest, BacktestConfig};

use crate::conversions::bars_from_py;

/// 事件驱动回测器（日频）：T 日收盘信号 → T+1 开盘调仓，逐日收盘记账。
#[pyclass]
pub struct PyBacktester;

#[pymethods]
impl PyBacktester {
    #[new]
    fn new() -> Self {
        Self
    }

    /// 运行回测 → {total_return, annual_return, max_drawdown, sharpe, win_rate,
    /// profit_loss_ratio, trade_count, final_equity, equity_curve, trades}。
    ///
    /// target_weights[i] = 第 i 根收盘的目标仓位（[0,1]，NaN=维持现状）。
    #[pyo3(signature = (dates, opens, highs, lows, closes, volumes, amounts,
                        target_weights, initial_cash = 1_000_000.0,
                        commission_rate = 0.0003, min_commission = 5.0,
                        slippage_rate = 0.001, trading_days_per_year = 252.0))]
    #[allow(clippy::too_many_arguments)]
    fn simulate(
        &self,
        dates: Vec<String>,
        opens: Vec<f64>,
        highs: Vec<f64>,
        lows: Vec<f64>,
        closes: Vec<f64>,
        volumes: Vec<f64>,
        amounts: Vec<f64>,
        target_weights: Vec<f64>,
        initial_cash: f64,
        commission_rate: f64,
        min_commission: f64,
        slippage_rate: f64,
        trading_days_per_year: f64,
    ) -> PyResult<Py<PyAny>> {
        let bars = bars_from_py(dates, opens, highs, lows, closes, volumes, amounts)?;
        let cfg = BacktestConfig {
            initial_cash,
            commission_rate,
            min_commission,
            slippage_rate,
            trading_days_per_year,
        };
        let r = run_backtest(&bars, &target_weights, &cfg)
            .map_err(|e| PyValueError::new_err(format!("{e}")))?;

        Python::attach(|py| {
            let d = PyDict::new(py);
            d.set_item("total_return", r.total_return)?;
            d.set_item("annual_return", r.annual_return)?;
            d.set_item("max_drawdown", r.max_drawdown)?;
            d.set_item("sharpe", r.sharpe)?;
            d.set_item("win_rate", r.win_rate)?;
            d.set_item(
                "profit_loss_ratio",
                if r.profit_loss_ratio.is_finite() { r.profit_loss_ratio } else { -1.0 },
            )?; // -1 表示无穷（无亏损交易），Python 侧转回文案
            d.set_item("trade_count", r.trade_count)?;
            d.set_item("final_equity", r.final_equity)?;
            let curve = PyList::empty(py);
            for (dt, e) in &r.equity_curve {
                let pair = PyList::empty(py);
                pair.append(dt.format("%Y-%m-%d").to_string())?;
                pair.append(*e)?;
                curve.append(pair)?;
            }
            d.set_item("equity_curve", curve)?;
            let trade_list = PyList::empty(py);
            for t in &r.trades {
                let td = PyDict::new(py);
                td.set_item("date_in", t.date_in.format("%Y-%m-%d").to_string())?;
                td.set_item("price_in", t.price_in)?;
                td.set_item("date_out", t.date_out.format("%Y-%m-%d").to_string())?;
                td.set_item("price_out", t.price_out)?;
                td.set_item("shares", t.shares)?;
                td.set_item("pnl", t.pnl)?;
                td.set_item("pnl_pct", t.pnl_pct)?;
                trade_list.append(td)?;
            }
            d.set_item("trades", trade_list)?;
            Ok(d.into())
        })
    }
}
