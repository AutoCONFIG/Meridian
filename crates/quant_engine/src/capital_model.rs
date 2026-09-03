//! 资金模型（机会通道，规则）：OBV 趋势 / 量比 / 量价配合 / 布林中轨位置。

use meridian_core::{
    AnalysisContext, AnalysisModel, IndicatorSnapshot, ModelCategory, ModelOutput, Result,
};

use crate::util::{confidence_from_factor_count, direction_from_score, model_factor};

/// 量价资金规则模型。
#[derive(Debug, Clone, Copy, Default)]
pub struct CapitalModel;

impl CapitalModel {
    pub fn new() -> Self {
        Self
    }
}

impl AnalysisModel for CapitalModel {
    fn name(&self) -> &str {
        "capital_model"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn category(&self) -> ModelCategory {
        ModelCategory::Rule
    }

    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput> {
        let ind = ctx.indicators;
        let n = ctx.bars.len();

        let mut score = 50.0;
        let mut factors = Vec::new();

        // 1. OBV 20日趋势
        if n >= 21 {
            let rising = ind.obv[n - 1] > ind.obv[n - 21];
            let c = if rising { 15.0 } else { -15.0 };
            factors.push(model_factor(
                "OBV趋势",
                ind.obv[n - 1] - ind.obv[n - 21],
                c,
                if rising {
                    "20日OBV上升，资金持续流入"
                } else {
                    "20日OBV下降，资金持续流出"
                },
            ));
            score += c;
        }

        // 2. 量比：最新成交量 / 20日均量
        let last_volume = ctx.bars.last().map(|b| b.volume);
        if let (Some(vol), Some(vol_ma)) = (last_volume, IndicatorSnapshot::last(&ind.vol_ma20)) {
            let ratio = vol / vol_ma;
            let c = if ratio > 1.2 {
                10.0
            } else if ratio < 0.7 {
                -10.0
            } else {
                0.0
            };
            factors.push(model_factor(
                "量比",
                ratio,
                c,
                if c > 0.0 {
                    "成交量较20日均量放大20%以上"
                } else if c < 0.0 {
                    "成交量较20日均量萎缩30%以上"
                } else {
                    "成交量与均量相当"
                },
            ));
            score += c;
        }

        // 3. 量价配合：放量上涨是健康信号，放量下跌是出货信号
        if let (Some(ret), Some(ratio_factor)) = (
            IndicatorSnapshot::last(&ind.ret_5d),
            factors.iter().find(|f| f.name == "量比"),
        ) {
            let ratio = ratio_factor.value;
            if ret > 0.0 && ratio > 1.0 {
                factors.push(model_factor(
                    "量价配合",
                    ret,
                    10.0,
                    "放量上涨，买盘真实",
                ));
                score += 10.0;
            } else if ret < 0.0 && ratio > 1.5 {
                factors.push(model_factor(
                    "量价背离",
                    ret,
                    -10.0,
                    "放量下跌，疑似出货",
                ));
                score -= 10.0;
            }
        }

        // 4. 布林中轨位置
        if let (Some(close), Some(mid)) = (
            ctx.bars.last().map(|b| b.close),
            IndicatorSnapshot::last(&ind.boll_mid),
        ) {
            let c = if close >= mid { 10.0 } else { -10.0 };
            factors.push(model_factor(
                "布林中轨",
                close / mid - 1.0,
                c,
                if c > 0.0 {
                    "价格位于布林中轨上方"
                } else {
                    "价格位于布林中轨下方"
                },
            ));
            score += c;
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
    fn uptrend_with_rising_volume_scores_high() {
        let fixture = testutil::uptrend_fixture(130);
        let out = CapitalModel.analyze(&fixture.ctx()).unwrap();
        // 上涨 + 量递增：OBV 上升、放量上涨、中轨上方
        assert!(out.score > 60.0, "score={}", out.score);
        assert_eq!(out.direction, Direction::Up);
    }

    #[test]
    fn insufficient_data_is_neutral() {
        let fixture = testutil::uptrend_fixture(2);
        let out = CapitalModel.analyze(&fixture.ctx()).unwrap();
        assert_eq!(out.score, 50.0);
        assert_eq!(out.direction, Direction::Neutral);
    }
}
