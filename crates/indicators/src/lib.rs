//! Meridian 指标计算层：全部为纯函数。
//! 约定：窗口不足返回 None（不用 NaN）；只使用 t 及之前数据（防未来函数泄露）。

mod bar_series;
mod snapshot;
mod stat;
mod trend;

pub use bar_series::{AdxSeries, adx, atr, obv, true_ranges};
pub use snapshot::build_snapshot;
pub use stat::{
    annualized_volatility, drawdown_series, max_drawdown, return_over, rsi, simple_returns,
};
pub use trend::{
    BollingerBands, MacdSeries, bollinger, ema, macd, rolling_max, rolling_max_prev, rolling_min,
    rolling_min_prev, sma,
};

/// 测试辅助：构造规则K线序列（crate 内共享）。
#[cfg(test)]
pub(crate) mod testutil {
    use chrono::NaiveDate;
    use meridian_core::Bar;

    pub(crate) fn date(i: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(2020, 1, 1).unwrap() + chrono::Duration::days(i as i64)
    }

    /// 严格上升K线：close_i = start + step·i，open = 前一收盘，
    /// high = close+0.5，low = open−0.5，volume 递增。
    pub(crate) fn up_bars(n: usize, start: f64, step: f64) -> Vec<Bar> {
        let mut bars = Vec::with_capacity(n);
        for i in 0..n {
            let close = start + step * i as f64;
            let open = if i == 0 {
                close - step
            } else {
                start + step * (i as f64 - 1.0)
            };
            let volume = 1000.0 + i as f64;
            bars.push(
                Bar::new(
                    date(i as u32),
                    open,
                    close + 0.5,
                    open - 0.5,
                    close,
                    volume,
                    volume * close,
                )
                .unwrap(),
            );
        }
        bars
    }

    /// 二次曲线上升K线：close_i = start + accel·i²（用于构造加速上涨，MACD 类测试）。
    pub(crate) fn quadratic_bars(n: usize, start: f64, accel: f64) -> Vec<Bar> {
        let mut bars = Vec::with_capacity(n);
        let mut prev_close = start - accel;
        for i in 0..n {
            let close = start + accel * (i * i) as f64;
            let volume = 1000.0 + i as f64;
            bars.push(
                Bar::new(
                    date(i as u32),
                    prev_close,
                    close + 0.5,
                    prev_close - 0.5,
                    close,
                    volume,
                    volume * close,
                )
                .unwrap(),
            );
            prev_close = close;
        }
        bars
    }
}
