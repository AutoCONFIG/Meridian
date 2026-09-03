//! 基本面模型（占位）：Phase 3 接入财务数据后实现。
//! 设计约束：基本面模型属于机会通道（估值/质量因子），输出与其它规则模型同格式，
//! 由 CompositeEngine 统一加权，不因类别特殊化。

use meridian_core::{
    AnalysisContext, AnalysisModel, Direction, Factor, ModelCategory, ModelOutput, Result,
};

/// 基本面模型占位实现：恒输出中性 50 / 置信度 0。
/// 置信度 0 表示"本模型无信息量"——注册方应据此选择暂不注册或以极低权重注册；
/// Phase 3 以 PE/PB/ROE/营收增速等因子替换本实现。
#[derive(Debug, Clone, Copy, Default)]
pub struct FundamentalModel;

impl FundamentalModel {
    pub fn new() -> Self {
        Self
    }
}

impl AnalysisModel for FundamentalModel {
    fn name(&self) -> &str {
        "fundamental_model"
    }

    fn version(&self) -> &str {
        "0.0.0"
    }

    fn category(&self) -> ModelCategory {
        ModelCategory::Rule
    }

    fn analyze(&self, _ctx: &AnalysisContext) -> Result<ModelOutput> {
        Ok(ModelOutput {
            score: 50.0,
            direction: Direction::Neutral,
            confidence: 0.0,
            factors: vec![Factor::new(
                "占位",
                0.0,
                0.0,
                "基本面模型为占位实现，Phase 3 接入财务数据",
            )],
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil;

    #[test]
    fn placeholder_is_neutral_with_zero_confidence() {
        let fixture = testutil::uptrend_fixture(130);
        let out = FundamentalModel.analyze(&fixture.ctx()).unwrap();
        assert_eq!(out.score, 50.0);
        assert_eq!(out.direction, Direction::Neutral);
        assert_eq!(out.confidence, 0.0);
        assert_eq!(out.factors.len(), 1);
    }
}
