//! 动量模型（机会通道，规则）：RSI / 区间收益 / MACD 动能 / 量能确认。

use meridian_core::{
    AnalysisContext, AnalysisModel, IndicatorSnapshot, ModelCategory, ModelOutput, Result,
};

use crate::util::{confidence_from_factor_count, direction_from_score, model_factor};

/// 动量规则模型。
#[derive(Debug, Clone, Copy, Default)]
pub struct MomentumModel;

impl MomentumModel {
    pub fn new() -> Self {
        Self
    }
}

impl AnalysisModel for MomentumModel {
    fn name(&self) -> &str {
        "momentum_model"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn category(&self) -> ModelCategory {
        ModelCategory::Rule
    }

    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput> {
        let ind = ctx.indicators;

        let mut score = 50.0;
        let mut factors = Vec::new();

        // 1. RSI14：50 线上下 + 超买惩罚
        if let Some(rsi) = IndicatorSnapshot::last(&ind.rsi14) {
            let c = if rsi > 80.0 {
                -10.0
            } else if rsi >= 50.0 {
                10.0
            } else {
                -10.0
            };
            factors.push(model_factor(
                "RSI14",
                rsi,
                c,
                if rsi > 80.0 {
                    "RSI超买（>80），动量过热存在回落风险"
                } else if rsi >= 50.0 {
                    "RSI处于强势区（≥50）"
                } else {
                    "RSI处于弱势区（<50）"
                },
            ));
            score += c;
        }

        // 2. 5日收益
        if let Some(ret) = IndicatorSnapshot::last(&ind.ret_5d) {
            let c = if ret > 0.0 { 10.0 } else if ret < 0.0 { -10.0 } else { 0.0 };
            factors.push(model_factor(
                "5日动量",
                ret,
                c,
                if c > 0.0 { "近5日上涨" } else if c < 0.0 { "近5日下跌" } else { "近5日持平" },
            ));
            score += c;
        }

        // 3. 20日收益
        if let Some(ret) = IndicatorSnapshot::last(&ind.ret_20d) {
            let c = if ret > 0.0 { 10.0 } else if ret < 0.0 { -10.0 } else { 0.0 };
            factors.push(model_factor(
                "20日动量",
                ret,
                c,
                if c > 0.0 { "近20日上涨" } else if c < 0.0 { "近20日下跌" } else { "近20日持平" },
            ));
            score += c;
        }

        // 4. MACD 柱状线
        if let Some(hist) = IndicatorSnapshot::last(&ind.macd_hist) {
            let c = if hist > 0.0 { 10.0 } else if hist < 0.0 { -10.0 } else { 0.0 };
            factors.push(model_factor(
                "MACD动能",
                hist,
                c,
                if c > 0.0 { "MACD红柱扩张中" } else if c < 0.0 { "MACD绿柱，动能不足" } else { "MACD动能中性" },
            ));
            score += c;
        }

        // 5. 量能确认：成交量高于20日均量
        let last_volume = ctx.bars.last().map(|b| b.volume);
        if let (Some(vol), Some(vol_ma)) =
            (last_volume, IndicatorSnapshot::last(&ind.vol_ma20))
        {
            if vol > vol_ma {
                factors.push(model_factor(
                    "量能放大",
                    vol / vol_ma - 1.0,
                    5.0,
                    "成交量高于20日均量，动量有量能支撑",
                ));
                score += 5.0;
            }
        }

        let score = score.clamp(0.0, 100.0);
        Ok(ModelOutput {
            score,
            direction: direction_from_score(score),
            confidence: confidence_from_factor_count(factors.len()),
            factors,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil;
    use meridian_core::Direction;

    #[test]
    fn uptrend_scores_high_and_points_up() {
        // 加速上涨（二次曲线）：MACD 红柱扩张、动量健康；
        // 线性序列下 hist→0⁻、RSI 恒 100 属合成数据 artifact，不用于本测试
        let fixture = testutil::quadratic_fixture(130);
        let out = MomentumModel.analyze(&fixture.ctx()).unwrap();
        assert!(out.score > 60.0, "score={}", out.score);
        assert_eq!(out.direction, Direction::Up);
    }

    #[test]
    fn insufficient_data_is_neutral() {
        let fixture = testutil::uptrend_fixture(5);
        let out = MomentumModel.analyze(&fixture.ctx()).unwrap();
        assert_eq!(out.score, 50.0);
        assert_eq!(out.direction, Direction::Neutral);
    }
}
