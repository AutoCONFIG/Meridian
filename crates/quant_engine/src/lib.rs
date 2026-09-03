//! Meridian 量化引擎：规则模型 + 市场状态检测 + 三层综合评分引擎。
//!
//! 模块：
//! - `config`：评分配置（YAML → 结构体）+ 配置指纹
//! - `market_regime`：RegimeDetector trait + NullDetector（Phase 0）
//! - 规则模型：trend / momentum / capital（机会通道）、risk（风险通道）、fundamental（占位）
//! - `composite`：CompositeEngine 三层合成 + regime 权重档 + action 规则匹配

pub mod capital_model;
pub mod composite;
pub mod config;
pub mod fundamental_model;
pub mod market_regime;
pub mod momentum_model;
pub mod risk_model;
pub mod trend_model;
pub mod util;

pub use capital_model::CapitalModel;
pub use composite::{CompositeEngine, RegisteredModel};
pub use config::{ActionRule, Condition, ScoringConfig, WeightSpec, WeightsConfig};
pub use fundamental_model::FundamentalModel;
pub use market_regime::{NullDetector, RegimeDetector};
pub use momentum_model::MomentumModel;
pub use risk_model::RiskModel;
pub use trend_model::TrendModel;

/// 测试辅助：构造规则 K 线序列与 AnalysisContext fixture（crate 内共享）。
#[cfg(test)]
pub(crate) mod testutil {
    use chrono::NaiveDate;
    use meridian_core::{
        Asset, AssetType, Bar, Frequency, IndicatorSnapshot, Market, RegimeState,
    };
    use meridian_indicators::build_snapshot;

    /// 拥有 ctx 所需的全部数据，按需借用生成 AnalysisContext。
    pub(crate) struct CtxFixture {
        pub asset: Asset,
        pub bars: Vec<Bar>,
        pub snapshot: IndicatorSnapshot,
        pub regime: RegimeState,
    }

    impl CtxFixture {
        pub fn ctx(&self) -> meridian_core::AnalysisContext<'_> {
            meridian_core::AnalysisContext {
                asset: &self.asset,
                regime: self.regime,
                bars: &self.bars,
                indicators: &self.snapshot,
            }
        }
    }

    fn date(i: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(2020, 1, 1).unwrap() + chrono::Duration::days(i as i64)
    }

    /// 严格上升K线：close_i = start + step·i，open = 前一收盘，
    /// high = close+0.5，low = open−0.5，volume 递增（量价齐升）。
    pub(crate) fn uptrend_bars(n: usize, start: f64, step: f64) -> Vec<Bar> {
        let mut bars = Vec::with_capacity(n);
        let mut prev_close = start - step;
        for i in 0..n {
            let close = start + step * i as f64;
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

    /// 上涨序列 + 指标快照 + Unknown regime 的标准测试上下文。
    pub(crate) fn uptrend_fixture(n: usize) -> CtxFixture {
        let bars = uptrend_bars(n, 100.0, 1.0);
        let snapshot = build_snapshot(&bars);
        CtxFixture {
            asset: Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily),
            bars,
            snapshot,
            regime: RegimeState::unknown(),
        }
    }

    /// 二次曲线上升K线：close_i = start + accel·i²（加速上涨，动量类测试）。
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

    /// 加速上涨序列 fixture（MACD 红柱、动量健康的形态）。
    pub(crate) fn quadratic_fixture(n: usize) -> CtxFixture {
        let bars = quadratic_bars(n, 100.0, 0.5);
        let snapshot = build_snapshot(&bars);
        CtxFixture {
            asset: Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily),
            bars,
            snapshot,
            regime: RegimeState::unknown(),
        }
    }
}
