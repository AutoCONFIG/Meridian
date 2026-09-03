//! 指标快照构建：一次计算全部指标并与 bars 对齐。

use meridian_core::{Bar, IndicatorSnapshot};

use crate::{bar_series, stat, trend};

/// 从升序日频 K 线构建指标快照。所有序列与 `bars` 等长对齐；
/// 窗口不足处为 None（不用 NaN）。
pub fn build_snapshot(bars: &[Bar]) -> IndicatorSnapshot {
    let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let highs: Vec<f64> = bars.iter().map(|b| b.high).collect();
    let lows: Vec<f64> = bars.iter().map(|b| b.low).collect();
    let volumes: Vec<f64> = bars.iter().map(|b| b.volume).collect();

    let adx_s = bar_series::adx(bars, 14);
    let boll = trend::bollinger(&closes, 20, 2.0);
    let macd_s = trend::macd(&closes, 12, 26, 9);

    IndicatorSnapshot {
        sma20: trend::sma(&closes, 20),
        sma60: trend::sma(&closes, 60),
        ema12: trend::ema(&closes, 12),
        ema26: trend::ema(&closes, 26),
        macd_dif: macd_s.dif,
        macd_dea: macd_s.dea,
        macd_hist: macd_s.hist,
        rsi14: stat::rsi(&closes, 14),
        atr14: bar_series::atr(bars, 14),
        adx14: adx_s.adx,
        plus_di14: adx_s.plus_di,
        minus_di14: adx_s.minus_di,
        boll_upper: boll.upper,
        boll_mid: boll.mid,
        boll_lower: boll.lower,
        obv: bar_series::obv(bars),
        vol_ma20: trend::sma(&volumes, 20),
        ret_5d: stat::return_over(&closes, 5),
        ret_20d: stat::return_over(&closes, 20),
        annual_vol_20: stat::annualized_volatility(&closes, 20, 252.0),
        drawdown: stat::drawdown_series(&closes),
        high_20d: trend::rolling_max_prev(&highs, 20),
        low_20d: trend::rolling_min_prev(&lows, 20),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil;

    fn approx(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-6, "left={a} right={b}");
    }

    #[test]
    fn snapshot_on_uptrend_is_consistent() {
        let bars = testutil::up_bars(130, 100.0, 1.0);
        let snap = build_snapshot(&bars);
        let n = bars.len();

        // 等长对齐
        assert_eq!(snap.len(), n);
        assert_eq!(snap.drawdown.len(), n);
        assert_eq!(snap.obv.len(), n);

        // sma20 末值 = 最后20个收盘均值
        let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
        let expect_sma20 = closes[n - 20..].iter().sum::<f64>() / 20.0;
        approx(snap.sma20[n - 1].unwrap(), expect_sma20);

        // 严格上涨：RSI 顶格、无回撤、OBV 持续增加、量价齐升
        assert_eq!(snap.rsi14[n - 1], Some(100.0));
        assert_eq!(snap.drawdown[n - 1], 0.0);
        assert!(snap.obv[n - 1] > snap.obv[n - 2]);
        assert!(snap.vol_ma20[n - 1].unwrap() > 1000.0);

        // high_20d/low_20d 不含当日：首个有效值在索引 20
        assert!(snap.high_20d[..20].iter().all(|v| v.is_none()));
        assert!(snap.high_20d[20].is_some());

        // 收盘突破前20日最高价（high_20d = 前20日 high 的最大值，不含当日）
        let high20 = snap.high_20d[n - 1].unwrap();
        assert!(bars[n - 1].close > high20);

        // ret_20d = 收盘/20日前收盘 − 1
        approx(
            snap.ret_20d[n - 1].unwrap(),
            closes[n - 1] / closes[n - 21] - 1.0,
        );

        // 130 根足够覆盖 60 日窗口
        assert!(snap.sma60[n - 1].is_some());
        assert!(snap.adx14[n - 1].unwrap() > 60.0); // 单边强趋势
    }

    #[test]
    fn snapshot_on_short_series_is_all_none_where_expected() {
        let bars = testutil::up_bars(10, 100.0, 1.0);
        let snap = build_snapshot(&bars);
        assert!(snap.sma20.iter().all(|v| v.is_none()));
        assert!(snap.rsi14.iter().all(|v| v.is_none()));
        assert!(snap.boll_mid.iter().all(|v| v.is_none()));
        // OBV / drawdown 总有值
        assert_eq!(snap.obv.len(), 10);
        assert!(!snap.drawdown.is_empty());
    }

    #[test]
    fn snapshot_empty_bars() {
        let snap = build_snapshot(&[]);
        assert!(snap.is_empty());
        assert!(snap.obv.is_empty());
    }
}
