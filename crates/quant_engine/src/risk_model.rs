//! 风险模型（风险通道，规则）：ATR / 年化波动 / 回撤 / 多空力量。
//! 风险分是独立维度：高分 = 风险高，不是机会分的扣分项。

use meridian_core::{
    AnalysisContext, AnalysisModel, Direction, IndicatorSnapshot, ModelCategory, ModelOutput,
    Result,
};

use crate::util::model_factor;

/// 风险规则模型。
/// 输出 direction 语义：风险升高 → Down（需防御），风险回落 → Up，中性不变。
#[derive(Debug, Clone, Copy, Default)]
pub struct RiskModel;

impl RiskModel {
    pub fn new() -> Self {
        Self
    }
}

impl AnalysisModel for RiskModel {
    fn name(&self) -> &str {
        "risk_model"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn category(&self) -> ModelCategory {
        ModelCategory::Rule
    }

    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput> {
        let ind = ctx.indicators;
        let close = ctx.bars.last().map(|b| b.close);

        let mut score = 50.0;
        let mut factors = Vec::new();

        // 1. ATR 占价比（日波动幅度）
        if let (Some(close), Some(atr)) = (close, IndicatorSnapshot::last(&ind.atr14)) {
            let atr_pct = atr / close;
            let c = if atr_pct > 0.04 {
                15.0
            } else if atr_pct > 0.02 {
                5.0
            } else {
                -10.0
            };
            factors.push(model_factor(
                "ATR占比",
                atr_pct,
                c,
                if c > 10.0 {
                    "日均波动超4%，波动剧烈"
                } else if c > 0.0 {
                    "日均波动2%-4%，波动偏高"
                } else {
                    "日均波动低于2%，波动温和"
                },
            ));
            score += c;
        }

        // 2. 20日年化波动率
        if let Some(vol) = IndicatorSnapshot::last(&ind.annual_vol_20) {
            let c = if vol > 0.45 {
                15.0
            } else if vol > 0.25 {
                5.0
            } else {
                -10.0
            };
            factors.push(model_factor(
                "年化波动率",
                vol,
                c,
                if c > 10.0 {
                    "年化波动超45%，风险显著"
                } else if c > 0.0 {
                    "年化波动25%-45%，中等偏高"
                } else {
                    "年化波动低于25%，风险温和"
                },
            ));
            score += c;
        }

        // 3. 当前回撤深度
        if let Some(dd) = ind.drawdown.last() {
            let c = if *dd < -0.25 {
                20.0
            } else if *dd < -0.10 {
                10.0
            } else {
                0.0
            };
            factors.push(model_factor(
                "回撤深度",
                *dd,
                c,
                if c >= 20.0 {
                    "距高点回撤超25%，深度回撤"
                } else if c >= 10.0 {
                    "距高点回撤10%-25%"
                } else {
                    "回撤在10%以内"
                },
            ));
            score += c;
        }

        // 4. 多空方向力量：-DI 高于 +DI → 空头占优，风险抬升
        if let (Some(plus), Some(minus)) = (
            IndicatorSnapshot::last(&ind.plus_di14),
            IndicatorSnapshot::last(&ind.minus_di14),
        ) {
            let c = if minus > plus { 10.0 } else { -5.0 };
            factors.push(model_factor(
                "多空力量",
                minus - plus,
                c,
                if c > 0.0 {
                    "-DI高于+DI，空头力量占优"
                } else {
                    "+DI不低于-DI，多头力量占优或均衡"
                },
            ));
            score += c;
        }

        let score = score.clamp(0.0, 100.0);
        let direction = if score > 60.0 {
            Direction::Down // 风险高 → 防御
        } else if score < 40.0 {
            Direction::Up // 风险低
        } else {
            Direction::Neutral
        };
        let confidence = (0.4 + 0.08 * factors.len() as f64).min(0.9);
        Ok(ModelOutput {
            score,
            direction,
            confidence,
            factors,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil;

    #[test]
    fn uptrend_has_lowish_risk() {
        let fixture = testutil::uptrend_fixture(130);
        let out = RiskModel.analyze(&fixture.ctx()).unwrap();
        // 无回撤、+DI 占优、波动温和的上涨序列 → 风险低于中性
        assert!(out.score < 50.0, "score={}", out.score);
        assert_eq!(out.direction, Direction::Up);
    }

    #[test]
    fn insufficient_data_is_neutral() {
        let fixture = testutil::uptrend_fixture(3);
        let out = RiskModel.analyze(&fixture.ctx()).unwrap();
        assert_eq!(out.score, 50.0);
        assert_eq!(out.direction, Direction::Neutral);
    }
}
