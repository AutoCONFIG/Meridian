use serde::{Deserialize, Serialize};

use crate::context::AnalysisContext;
use crate::error::Result;
use crate::signal::ModelOutput;

/// 模型类别。注意：没有 Agent —— ResearchAgent 是独立体系，输出信息报告，不进评分通道。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelCategory {
    Rule,
    AiPrediction,
}

/// 评分通道。注册模型时显式指定，与 ModelCategory 正交：
/// AI 模型无论类别都只能进这两个通道，无法绕过风控（架构上保证，见 PLAN.md 第 6 节）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Channel {
    Opportunity,
    Risk,
}

/// 所有分析模型（规则 / AI 预测）的统一接口：规则模型与 AI 模型输出同格式。
/// Send + Sync：模型以 trait object 注册进 CompositeEngine（pyclass 要求）。
pub trait AnalysisModel: Send + Sync {
    fn name(&self) -> &str;
    fn version(&self) -> &str;
    fn category(&self) -> ModelCategory;
    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::asset::{Asset, AssetType, Frequency, Market};
    use crate::bar::Bar;
    use crate::context::{AnalysisContext, IndicatorSnapshot};
    use crate::signal::{Direction, RegimeState};
    use chrono::NaiveDate;

    struct DummyModel;

    impl AnalysisModel for DummyModel {
        fn name(&self) -> &str {
            "dummy"
        }
        fn version(&self) -> &str {
            "0.1.0"
        }
        fn category(&self) -> ModelCategory {
            ModelCategory::Rule
        }
        fn analyze(&self, _ctx: &AnalysisContext) -> Result<ModelOutput> {
            Ok(ModelOutput {
                score: 55.0,
                direction: Direction::Neutral,
                confidence: 0.5,
                factors: vec![],
            })
        }
    }

    fn assert_send<T: Send>() {}

    #[test]
    fn channel_and_category_serde() {
        assert_eq!(
            serde_json::to_string(&Channel::Opportunity).unwrap(),
            "\"opportunity\""
        );
        assert_eq!(
            serde_json::to_string(&ModelCategory::AiPrediction).unwrap(),
            "\"ai_prediction\""
        );
        assert_ne!(Channel::Opportunity, Channel::Risk);
    }

    #[test]
    fn model_trait_object_is_send_and_callable() {
        assert_send::<Box<dyn AnalysisModel>>();
        let models: Vec<Box<dyn AnalysisModel>> = vec![Box::new(DummyModel)];

        let asset =
            Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily);
        let bar = Bar::new(
            NaiveDate::from_ymd_opt(2024, 1, 2).unwrap(),
            10.0,
            11.0,
            9.5,
            10.5,
            1.0,
            1.0,
        )
        .unwrap();
        let snap = IndicatorSnapshot::default();
        let ctx = AnalysisContext {
            asset: &asset,
            regime: RegimeState::unknown(),
            bars: std::slice::from_ref(&bar),
            indicators: &snap,
        };

        let out = models[0].analyze(&ctx).unwrap();
        assert_eq!(out.score, 55.0);
        assert_eq!(models[0].category(), ModelCategory::Rule);
        assert_eq!(models[0].version(), "0.1.0");
    }
}
