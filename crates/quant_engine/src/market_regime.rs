//! 市场状态检测（Market Regime）。
//! Phase 0 仅 NullDetector（恒 Unknown），接口就位、可随时替换；
//! Phase 1 填入基于沪深300/标普500/VIX/美元指数/利率的规则检测器。

use meridian_core::{Bar, RegimeState};

pub trait RegimeDetector: Send + Sync {
    fn name(&self) -> &str;

    /// 基于指数（或代理指数组合）K线判断当前市场状态 + 置信度。
    fn detect(&self, index_bars: &[Bar]) -> RegimeState;
}

/// 空检测器：恒返回 Unknown + 置信度 0（Phase 0 默认，验收标准 5：可替换）。
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

#[cfg(test)]
mod tests {
    use super::*;
    use meridian_core::Regime;

    fn assert_send_sync<T: Send + Sync>() {}

    #[test]
    fn null_detector_returns_unknown_with_zero_confidence() {
        assert_send_sync::<Box<dyn RegimeDetector>>();
        let detector: Box<dyn RegimeDetector> = Box::new(NullDetector);
        assert_eq!(detector.name(), "null");
        let state = detector.detect(&[]);
        assert_eq!(state.regime, Regime::Unknown);
        assert_eq!(state.confidence, 0.0);
    }
}
