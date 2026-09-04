//! 三层综合引擎：机会/风险两通道加权合成 + action_rules 规则匹配生成建议。
//!
//! 设计约束（PLAN.md 第 6 节）：
//! - opportunity = Σ(模型分 × regime档权重) / Σ权重，clamp 0-100
//! - risk 独立合成，非机会分的扣分项
//! - action 只能由 action_rules 按 (opportunity, risk) 匹配生成，任何模型（含 AI）不可干预
//! - 未在权重表登记的模型按 `unknown_model_weight` 计入，保证其贡献可见

use meridian_core::{
    Action, ActionOutput, AnalysisContext, AnalysisModel, Channel, CompositeScore, ModelOutput,
    OpportunityScore, Factor, Result, RiskScore,
};

use crate::config::ScoringConfig;

/// 注册进引擎的模型：模型 + 所属通道。
pub struct RegisteredModel {
    pub model: Box<dyn AnalysisModel>,
    pub channel: Channel,
}

impl RegisteredModel {
    pub fn new(model: Box<dyn AnalysisModel>, channel: Channel) -> Self {
        Self { model, channel }
    }
}

/// 某通道内单个模型的加权明细（可追溯）。
struct WeightedOutput {
    name: String,
    output: ModelOutput,
    weight: f64,
    /// 是否使用了 unknown_model_weight
    unregistered: bool,
}

pub struct CompositeEngine {
    config: ScoringConfig,
}

impl CompositeEngine {
    pub fn new(config: ScoringConfig) -> Self {
        Self { config }
    }

    pub fn config(&self) -> &ScoringConfig {
        &self.config
    }

    /// 执行三层合成。
    pub fn evaluate(
        &self,
        models: &[RegisteredModel],
        ctx: &AnalysisContext,
    ) -> Result<CompositeScore> {
        let mut opportunity_outs = Vec::new();
        let mut risk_outs = Vec::new();

        for registered in models {
            let output = registered.model.analyze(ctx)?.normalized();
            match registered.channel {
                Channel::Opportunity => opportunity_outs.push(self.weight_of(
                    registered.model.name(),
                    output,
                    &self.config.weights.opportunity.weights_for(ctx.regime.regime),
                )),
                Channel::Risk => risk_outs.push(self.weight_of(
                    registered.model.name(),
                    output,
                    &self.config.weights.risk.weights_for(ctx.regime.regime),
                )),
            }
        }

        let opportunity = self.compose_channel(&opportunity_outs);
        let risk = self.compose_channel(&risk_outs);
        let action = self.match_action(opportunity.score, risk.score);

        Ok(CompositeScore {
            opportunity: OpportunityScore {
                score: opportunity.score,
                factors: opportunity.factors,
            },
            risk: RiskScore {
                score: risk.score,
                factors: risk.factors,
            },
            action,
            model_version: self.config.model_version.clone(),
            config_fingerprint: self.config.fingerprint(),
        })
    }

    /// 模型 → (输出, 有效权重)。权重表未登记的模型用 unknown_model_weight。
    fn weight_of(
        &self,
        name: &str,
        output: ModelOutput,
        weights: &std::collections::BTreeMap<String, f64>,
    ) -> WeightedOutput {
        match weights.get(name) {
            Some(&w) => WeightedOutput {
                name: name.to_string(),
                output,
                weight: w,
                unregistered: false,
            },
            None => WeightedOutput {
                name: name.to_string(),
                output,
                weight: self.config.unknown_model_weight,
                unregistered: true,
            },
        }
    }

    /// 通道内加权平均：Σ(score×w)/Σw；无模型或权重和为 0 → 中性 50。
    fn compose_channel(&self, outs: &[WeightedOutput]) -> ChannelScore {
        let total_w: f64 = outs.iter().map(|o| o.weight).sum();
        if outs.is_empty() || total_w <= 0.0 {
            return ChannelScore {
                score: 50.0,
                factors: Vec::new(),
            };
        }

        let mut score = 0.0;
        let mut factors = Vec::new();
        for o in outs {
            let share = o.output.score * o.weight / total_w;
            score += share;
            let mut factor = Factor::new(
                o.name.clone(),
                o.output.score,
                share,
                format!(
                    "方向{}、置信度{:.2}、权重{:.2}{}",
                    o.output.direction.as_str(),
                    o.output.confidence,
                    o.weight,
                    if o.unregistered {
                        "（未登记，使用 unknown_model_weight）"
                    } else {
                        ""
                    }
                ),
            );
            // 保留模型内部触发明细（"为什么是这个分"），报告与落库可追溯
            factor.details = o.output.factors.clone();
            factors.push(factor);
        }

        ChannelScore {
            score: score.clamp(0.0, 100.0),
            factors,
        }
    }

    /// action_rules 按序匹配，首条 Conditional 命中即生效；Default 兜底。
    /// 全不命中（无 default 规则）→ Watch 并记录原因。
    fn match_action(&self, opportunity: f64, risk: f64) -> ActionOutput {
        let mut rule_triggers = Vec::new();

        for rule in &self.config.action_rules {
            match rule {
                crate::config::ActionRule::Conditional { condition, then } => {
                    if condition.matches(opportunity, risk) {
                        rule_triggers.push(format!(
                            "{} → {}",
                            condition.describe(),
                            then.as_str()
                        ));
                        return ActionOutput {
                            action: *then,
                            position_hint: None,
                            rule_triggers,
                        };
                    }
                }
                crate::config::ActionRule::Default { default } => {
                    rule_triggers.push(format!("兜底规则 → {}", default.as_str()));
                    return ActionOutput {
                        action: *default,
                        position_hint: None,
                        rule_triggers,
                    };
                }
            }
        }

        rule_triggers.push("无匹配规则且未配置 default → Watch".to_string());
        ActionOutput {
            action: Action::Watch,
            position_hint: None,
            rule_triggers,
        }
    }
}

/// 通道合成中间结果。
struct ChannelScore {
    score: f64,
    factors: Vec<Factor>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil;
    use meridian_core::{Direction, ModelCategory, Regime, RegimeState};

    const TEST_YAML: &str = r#"
model_version: "rule-test"
weights:
  opportunity:
    default:
      trend_model: 0.4
      momentum_model: 0.4
      capital_model: 0.2
    by_regime:
      Bear:
        trend_model: 0.2
        momentum_model: 0.3
        capital_model: 0.5
  risk:
    default:
      risk_model: 1.0
action_rules:
  - if: {opportunity_gte: 70, risk_lte: 40}
    then: Add
  - if: {opportunity_lte: 40}
    then: Reduce
  - if: {risk_gte: 65}
    then: Avoid
  - default: Watch
"#;

    fn engine() -> CompositeEngine {
        CompositeEngine::new(ScoringConfig::from_yaml_str(TEST_YAML).unwrap())
    }

    fn std_models() -> Vec<RegisteredModel> {
        vec![
            RegisteredModel::new(Box::new(crate::trend_model::TrendModel::new()), Channel::Opportunity),
            RegisteredModel::new(Box::new(crate::momentum_model::MomentumModel::new()), Channel::Opportunity),
            RegisteredModel::new(Box::new(crate::capital_model::CapitalModel::new()), Channel::Opportunity),
            RegisteredModel::new(Box::new(crate::risk_model::RiskModel::new()), Channel::Risk),
        ]
    }

    #[test]
    fn uptrend_composite_adds_with_traces() {
        let engine = engine();
        let fixture = testutil::uptrend_fixture(130);
        let out = engine.evaluate(&std_models(), &fixture.ctx()).unwrap();

        // 强上涨序列：机会高分、风险低分、命中 Add 规则
        assert!(out.opportunity.score > 70.0, "opp={}", out.opportunity.score);
        assert!(out.risk.score < 40.0, "risk={}", out.risk.score);
        assert_eq!(out.action.action, Action::Add);
        assert!(!out.action.rule_triggers.is_empty());
        assert!(out.action.rule_triggers[0].contains("Add"));

        // 机会通道 3 个模型各有一条因子，且 Σcontribution == 综合分
        assert_eq!(out.opportunity.factors.len(), 3);
        let sum: f64 = out.opportunity.factors.iter().map(|f| f.contribution).sum();
        assert!((sum - out.opportunity.score).abs() < 1e-9);

        // 版本与指纹来自配置
        assert_eq!(out.model_version, "rule-test");
        assert_eq!(out.config_fingerprint, engine.config().fingerprint());
        assert_eq!(out.config_fingerprint.len(), 16);
    }

    #[test]
    fn model_internal_factors_preserved_as_details() {
        // "为什么"必须可追溯：综合层的每模型因子要挂上模型内部触发明细
        let engine = engine();
        let fixture = testutil::uptrend_fixture(130);
        let out = engine.evaluate(&std_models(), &fixture.ctx()).unwrap();

        let risk_f = &out.risk.factors[0];
        assert_eq!(risk_f.name, "risk_model");
        assert!(!risk_f.details.is_empty(), "risk_model 内部触发明细不得被综合层丢弃");
        assert!(risk_f
            .details
            .iter()
            .all(|d| !d.description.is_empty() && d.contribution.is_finite()));

        for f in &out.opportunity.factors {
            assert!(!f.details.is_empty(), "{} 触发明细丢失", f.name);
        }
    }

    #[test]
    fn dumb_model_contributes_via_unknown_weight() {
        struct DumbModel;
        impl AnalysisModel for DumbModel {
            fn name(&self) -> &str {
                "py_dummy_v1"
            }
            fn version(&self) -> &str {
                "0.1.0"
            }
            fn category(&self) -> ModelCategory {
                ModelCategory::AiPrediction
            }
            fn analyze(&self, _ctx: &AnalysisContext) -> Result<ModelOutput> {
                Ok(ModelOutput {
                    score: 55.0,
                    direction: Direction::Neutral,
                    confidence: 0.8,
                    factors: vec![],
                })
            }
        }

        let engine = engine();
        let fixture = testutil::uptrend_fixture(130);

        let mut models = std_models();
        let baseline = engine.evaluate(&models, &fixture.ctx()).unwrap();

        // 注册未登记权重的哑模型（Python 桥接场景）
        models.push(RegisteredModel::new(Box::new(DumbModel), Channel::Opportunity));
        let with_dummy = engine.evaluate(&models, &fixture.ctx()).unwrap();

        // unknown_model_weight=0.2 → 哑模型 55 分把综合分从高处拉低，贡献可见
        assert!(with_dummy.opportunity.score < baseline.opportunity.score);
        let dummy_factor = with_dummy
            .opportunity
            .factors
            .iter()
            .find(|f| f.name == "py_dummy_v1")
            .expect("哑模型因子必须可追溯");
        assert!(dummy_factor.description.contains("unknown_model_weight"));
    }

    #[test]
    fn risk_rule_triggers_avoid() {
        struct HotRiskModel;
        impl AnalysisModel for HotRiskModel {
            fn name(&self) -> &str {
                "risk_model"
            }
            fn version(&self) -> &str {
                "0.1.0"
            }
            fn category(&self) -> ModelCategory {
                ModelCategory::Rule
            }
            fn analyze(&self, _ctx: &AnalysisContext) -> Result<ModelOutput> {
                Ok(ModelOutput {
                    score: 80.0,
                    direction: Direction::Down,
                    confidence: 0.8,
                    factors: vec![],
                })
            }
        }

        let engine = engine();
        let fixture = testutil::uptrend_fixture(130);
        // 机会通道给两个高分规则模型（模拟强机会），但风险 80 → 应命中 Avoid
        let models = vec![
            RegisteredModel::new(Box::new(crate::trend_model::TrendModel::new()), Channel::Opportunity),
            RegisteredModel::new(Box::new(crate::momentum_model::MomentumModel::new()), Channel::Opportunity),
            RegisteredModel::new(Box::new(HotRiskModel), Channel::Risk),
        ];
        let out = engine.evaluate(&models, &fixture.ctx()).unwrap();
        assert!(out.risk.score > 65.0);
        assert_eq!(out.action.action, Action::Avoid);
    }

    #[test]
    fn regime_switches_weight_tier() {
        let engine = engine();
        // 同一序列，Unknown 与 Bear 档权重不同 → 综合分不同
        let fixture_unknown = testutil::uptrend_fixture(130);
        let mut fixture_bear = testutil::uptrend_fixture(130);
        fixture_bear.regime = RegimeState {
            regime: Regime::Bear,
            confidence: 0.9,
        };

        let out_unknown = engine.evaluate(&std_models(), &fixture_unknown.ctx()).unwrap();
        let out_bear = engine.evaluate(&std_models(), &fixture_bear.ctx()).unwrap();
        assert_ne!(out_unknown.opportunity.score, out_bear.opportunity.score);
    }

    #[test]
    fn empty_models_yield_neutral() {
        let engine = engine();
        let fixture = testutil::uptrend_fixture(130);
        let out = engine.evaluate(&[], &fixture.ctx()).unwrap();
        assert_eq!(out.opportunity.score, 50.0);
        assert_eq!(out.risk.score, 50.0);
        assert_eq!(out.action.action, Action::Watch); // 兜底规则
        assert!(out.opportunity.factors.is_empty());
    }
}
