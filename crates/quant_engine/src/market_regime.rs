//! 市场状态检测（Market Regime）。
//! Phase 1：TrendVolDetector 规则版——趋势（MA 快慢线）× 波动（ATR 占比）× 急跌（窗内回撤）。
//! 一期以被分析标的自身K线作代理输入（数据层尚无指数渠道）；PLAN 的指数版
//! （沪深300/标普500/VIX）后续接入指数数据源后只需换喂入的 bars，检测代码不变。

use meridian_core::{Bar, Regime, RegimeState};
use meridian_indicators::{atr, sma};

pub trait RegimeDetector: Send + Sync {
    fn name(&self) -> &str;

    /// 基于指数（或代理指数组合）K线判断当前市场状态 + 置信度。
    fn detect(&self, index_bars: &[Bar]) -> RegimeState;
}

/// 检测阈值（config/regime.yaml 可覆盖，红线 4：阈值不硬编码在业务路径）。
#[derive(Debug, Clone, PartialEq)]
pub struct RegimeThresholds {
    /// 快均线窗口（日）
    pub trend_ma_fast: usize,
    /// 慢均线窗口（日）
    pub trend_ma_slow: usize,
    /// 收盘相对慢线偏离绝对值低于此值 → 趋势不成立（震荡候选）
    pub trend_band: f64,
    /// 急跌观察窗（日）
    pub drawdown_window: usize,
    /// 窗内高点回撤超过此值 → 危机候选（0.10 = 10%）
    pub crisis_drawdown: f64,
    /// ATR 周期
    pub atr_period: usize,
    /// ATR/收盘 ≥ 此值 → 波动确认危机
    pub atr_pct_crisis: f64,
    /// ATR/收盘 ≥ 此值 → 高波动
    pub atr_pct_high_vol: f64,
}

impl Default for RegimeThresholds {
    fn default() -> Self {
        Self {
            trend_ma_fast: 20,
            trend_ma_slow: 60,
            trend_band: 0.03,
            drawdown_window: 20,
            crisis_drawdown: 0.10,
            atr_period: 14,
            atr_pct_crisis: 0.035,
            atr_pct_high_vol: 0.025,
        }
    }
}

/// 空检测器：恒返回 Unknown + 置信度 0（测试/降级用，验收标准 5：可替换）。
#[derive(Debug, Clone, Copy, Default)]
pub struct NullDetector;

impl RegimeDetector for NullDetector {
    fn name(&self) -> &str {
        "null"
    }

    fn detect(&self, _index_bars: &[Bar]) -> RegimeState {
        RegimeState::unknown()
    }
}

/// 规则检测器 trend_vol_v1。
///
/// 判定优先级：Crisis > Bear/Bull > HighVol > Sideways。
/// 理由：权重档（by_regime）主要按方向切档，趋势显著时优先给方向状态；
/// 趋势不成立而波动爆表才标 HighVol——此时趋势判定本身不可靠；
/// Crisis 要求"急跌+高波动"同时成立，避免普通回调误报危机。
pub struct TrendVolDetector {
    t: RegimeThresholds,
}

impl TrendVolDetector {
    pub fn new(thresholds: RegimeThresholds) -> Self {
        Self { t: thresholds }
    }
}

impl RegimeDetector for TrendVolDetector {
    fn name(&self) -> &str {
        "trend_vol_v1"
    }

    fn detect(&self, bars: &[Bar]) -> RegimeState {
        let n = bars.len();
        // 慢线窗口不足 → 不下结论（与指标红线同款约定：宁缺毋滥）
        if n < self.t.trend_ma_slow {
            return RegimeState::unknown();
        }
        let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
        let ma_fast = sma(&closes, self.t.trend_ma_fast);
        let ma_slow = sma(&closes, self.t.trend_ma_slow);
        let (Some(&Some(fast)), Some(&Some(slow))) = (ma_fast.last(), ma_slow.last()) else {
            return RegimeState::unknown();
        };
        let close = closes[n - 1];
        if !close.is_finite() || close <= 0.0 {
            return RegimeState::unknown(); // 收盘非正属脏数据，不猜测
        }

        let dev = close / slow - 1.0; // 收盘相对慢线偏离
        let atr_pct = atr(bars, self.t.atr_period)
            .last()
            .and_then(|x| *x)
            .map(|a| a / close);

        // 急跌维度：观察窗内最高收盘 → 当前回撤（≤ 0）
        let dd_start = n.saturating_sub(self.t.drawdown_window);
        let window_high = closes[dd_start..].iter().copied().fold(f64::MIN, f64::max);
        let drawdown = if window_high > 0.0 { close / window_high - 1.0 } else { 0.0 };

        let mut basis = vec![
            format!(
                "MA{}/MA{} = {:.2}/{:.2}，收盘偏离慢线 {:+.1}%",
                self.t.trend_ma_fast, self.t.trend_ma_slow, fast, slow, dev * 100.0
            ),
            format!(
                "{}日内自高点回撤 {:+.1}%",
                self.t.drawdown_window,
                drawdown * 100.0
            ),
        ];
        match atr_pct {
            Some(p) => basis.push(format!("ATR{}/收盘 = {:.1}%", self.t.atr_period, p * 100.0)),
            None => basis.push(format!("ATR{} 窗口不足，波动维度未计", self.t.atr_period)),
        }

        let trend_up = dev >= self.t.trend_band && fast > slow;
        let trend_down = dev <= -self.t.trend_band && fast < slow;
        let crisis = drawdown <= -self.t.crisis_drawdown
            && atr_pct.is_some_and(|p| p >= self.t.atr_pct_crisis);
        let high_vol = atr_pct.is_some_and(|p| p >= self.t.atr_pct_high_vol);

        let (regime, confidence) = if crisis {
            (Regime::Crisis, 0.9)
        } else if trend_up {
            (Regime::Bull, strength_conf(dev, self.t.trend_band))
        } else if trend_down {
            (Regime::Bear, strength_conf(-dev, self.t.trend_band))
        } else if high_vol {
            (Regime::HighVol, vol_conf(atr_pct, self.t.atr_pct_high_vol))
        } else {
            (Regime::Sideways, 0.6)
        };

        RegimeState {
            regime,
            confidence,
            basis,
        }
        .normalized()
    }
}

/// 趋势置信度：偏离超出趋势带越多越自信，封顶 0.95。
fn strength_conf(dev: f64, band: f64) -> f64 {
    0.6 + 0.35 * ((dev / band - 1.0).clamp(0.0, 1.0))
}

/// 波动置信度：ATR 占比超出高波动线越多越自信，封顶 0.9。
fn vol_conf(atr_pct: Option<f64>, line: f64) -> f64 {
    let strength = atr_pct.map(|p| (p / line - 1.0).clamp(0.0, 1.0)).unwrap_or(0.0);
    0.6 + 0.3 * strength
}

#[cfg(test)]
mod tests {
    use super::*;
    use meridian_core::Bar;

    fn bar(day: u32, close: f64, prev_close: f64) -> Bar {
        // 构造 OHLC：以 prev_close 为锚的简单展开，波动由 close 差间接控制
        let open = prev_close;
        let high = open.max(close) * 1.01;
        let low = open.min(close) * 0.99;
        Bar {
            date: chrono::NaiveDate::from_ymd_opt(2026, 1, day.max(1)).unwrap(),
            open,
            high,
            low,
            close,
            volume: 1_000_000.0,
            amount: f64::NAN,
        }
    }

    /// 生成一段几何级数走势：daily_return 为日收益率。
    fn series(days: usize, daily_return: f64, start: f64) -> Vec<Bar> {
        let mut out = Vec::with_capacity(days);
        let mut close = start;
        for i in 0..days {
            let prev = close;
            close *= 1.0 + daily_return;
            out.push(bar((i % 28 + 1) as u32, close, prev));
        }
        out
    }

    fn detector() -> TrendVolDetector {
        TrendVolDetector::new(RegimeThresholds::default())
    }

    #[test]
    fn strong_uptrend_is_bull() {
        // 日涨 1.5%，130 天：慢线上方且快线在上
        let state = detector().detect(&series(130, 0.015, 100.0));
        assert_eq!(state.regime, Regime::Bull);
        assert!(state.confidence > 0.6);
        assert!(!state.basis.is_empty(), "判定依据应非空");
        assert!(state.basis[0].contains("MA20/MA60"));
    }

    #[test]
    fn steady_downtrend_is_bear() {
        // 日跌 1%：趋势向下但不够"急跌"（130 天缓跌回撤远超窗内 10%？——
        // 20 日窗内高点回撤：日跌 1% 20 天 ≈ -18%，会够 crisis_drawdown，
        // 但危机还需高波动（ATR占比 ≥ 3.5%）——日跌 1% 的 TR 占比约 1.2%，不够 → Bear
        let state = detector().detect(&series(130, -0.01, 100.0));
        assert_eq!(state.regime, Regime::Bear, "basis={:?}", state.basis);
    }

    #[test]
    fn crash_with_volatility_is_crisis() {
        // 急跌段：日跌 3.5% × 25 天，前置温和上涨垫高窗口高点
        let mut bars = series(100, 0.002, 100.0);
        bars.extend(series(25, -0.035, bars.last().unwrap().close));
        let state = detector().detect(&bars);
        assert_eq!(state.regime, Regime::Crisis, "basis={:?}", state.basis);
    }

    #[test]
    fn flat_series_is_sideways() {
        // 纯横盘：±0.05% 微波动
        let state = detector().detect(&series(130, 0.0, 100.0));
        assert_eq!(state.regime, Regime::Sideways, "basis={:?}", state.basis);
    }

    #[test]
    fn short_window_is_unknown() {
        let state = detector().detect(&series(30, 0.01, 100.0));
        assert_eq!(state.regime, Regime::Unknown);
        assert_eq!(state.confidence, 0.0);
        assert!(state.basis.is_empty());
    }

    #[test]
    fn null_detector_still_unknown() {
        let state = NullDetector.detect(&series(130, 0.01, 100.0));
        assert_eq!(state.regime, Regime::Unknown);
        assert_eq!(state.confidence, 0.0);
    }

    #[test]
    fn thresholds_override_changes_verdict() {
        // 收紧危机回撤线到 1%：缓跌也判危机（验证阈值确实参与判定）
        let t = RegimeThresholds {
            crisis_drawdown: 0.01,
            atr_pct_crisis: 0.005,
            ..RegimeThresholds::default()
        };
        let state = TrendVolDetector::new(t).detect(&series(130, -0.01, 100.0));
        assert_eq!(state.regime, Regime::Crisis, "basis={:?}", state.basis);
    }
}
