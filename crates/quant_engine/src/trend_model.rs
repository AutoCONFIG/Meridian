//! 趋势模型（机会通道，规则）：均线结构 / MACD / 前高突破 → 机会分。

use meridian_core::{
    AnalysisContext, AnalysisModel, IndicatorSnapshot, ModelCategory, ModelOutput, Result,
};

use crate::util::{confidence_from_factor_count, direction_from_score, model_factor};

/// 趋势跟随规则模型。
/// 打分：基线 50，各因子贡献 ±15/±10/±5，clamp 0-100；
/// 窗口不足的因子不计贡献（不虚构数据）。
#[derive(Debug, Clone, Copy, Default)]
pub struct TrendModel;

impl TrendModel {
    pub fn new() -> Self {
        Self
    }
}

impl AnalysisModel for TrendModel {
    fn name(&self) -> &str {
        "trend_model"
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

        // 1. 收盘 vs MA20
        if let (Some(close), Some(sma20)) = (close, IndicatorSnapshot::last(&ind.sma20)) {
            let c = if close >= sma20 { 15.0 } else { -15.0 };
            factors.push(model_factor(
                "收盘vs MA20",
                close / sma20 - 1.0,
                c,
                if c > 0.0 {
                    "收盘价位于20日均线上方，短趋势向上"
                } else {
                    "收盘价跌破20日均线，短趋势走弱"
                },
            ));
            score += c;
        }

        // 2. MA20 vs MA60（中期均线结构）
        if let (Some(sma20), Some(sma60)) =
            (IndicatorSnapshot::last(&ind.sma20), IndicatorSnapshot::last(&ind.sma60))
        {
            let c = if sma20 >= sma60 { 15.0 } else { -15.0 };
            factors.push(model_factor(
                "MA20 vs MA60",
                sma20 / sma60 - 1.0,
                c,
                if c > 0.0 {
                    "中期均线多头排列"
                } else {
                    "中期均线空头排列"
                },
            ));
            score += c;
        }

        // 3. MACD 柱状线方向
        if let Some(hist) = IndicatorSnapshot::last(&ind.macd_hist) {
            let c = if hist > 0.0 { 10.0 } else if hist < 0.0 { -10.0 } else { 0.0 };
            factors.push(model_factor(
                "MACD动能",
                hist,
                c,
                if c > 0.0 {
                    "MACD红柱，动能向上"
                } else if c < 0.0 {
                    "MACD绿柱，动能向下"
                } else {
                    "MACD动能中性"
                },
            ));
            score += c;
        }

        // 4. DIF 位置（零轴上下）
        if let Some(dif) = IndicatorSnapshot::last(&ind.macd_dif) {
            let c = if dif > 0.0 { 10.0 } else if dif < 0.0 { -10.0 } else { 0.0 };
            factors.push(model_factor(
                "DIF零轴",
                dif,
                c,
                if c > 0.0 {
                    "DIF位于零轴上方，中期趋势偏多"
                } else if c < 0.0 {
                    "DIF位于零轴下方，中期趋势偏空"
                } else {
                    "DIF位于零轴"
                },
            ));
            score += c;
        }

        // 5. 收盘突破前20日最高价（high_20d 不含当日）
        if let (Some(close), Some(high20)) = (close, IndicatorSnapshot::last(&ind.high_20d)) {
            if close > high20 {
                factors.push(model_factor(
                    "20日新高突破",
                    close / high20 - 1.0,
                    10.0,
                    "收盘价突破前20日最高价",
                ));
                score += 10.0;
            }
        }

        // 6. ADX 趋势强度确认
        if let Some(adx) = IndicatorSnapshot::last(&ind.adx14) {
            if adx > 25.0 {
                factors.push(model_factor(
                    "ADX趋势强度",
                    adx,
                    5.0,
                    "ADX高于25，趋势强度足以支持跟随",
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
        let fixture = testutil::uptrend_fixture(130);
        let out = TrendModel.analyze(&fixture.ctx()).unwrap();
        assert!(out.score > 60.0, "score={}", out.score);
        assert_eq!(out.direction, Direction::Up);
        assert!(out.confidence > 0.5);
        // 突破 + ADX 因子都应触发
        assert!(out.factors.iter().any(|f| f.name == "20日新高突破"));
        assert!(out.factors.iter().any(|f| f.name == "ADX趋势强度"));
    }

    #[test]
    fn insufficient_data_is_neutral() {
        let fixture = testutil::uptrend_fixture(3);
        let out = TrendModel.analyze(&fixture.ctx()).unwrap();
        assert_eq!(out.score, 50.0);
        assert_eq!(out.direction, Direction::Neutral);
        assert!(out.factors.is_empty());
    }
}
