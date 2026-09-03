//! 评分配置：`config/scoring/{asset_type}.yaml` 的 Rust 映射 + 配置指纹。

use std::collections::BTreeMap;
use std::path::Path;

use meridian_core::{Action, MeridianError, Regime, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// 权重档：default + 按 regime 覆盖。
// 用 BTreeMap 而非 HashMap：序列化键序确定，配置指纹才稳定。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct WeightSpec {
    /// 模型名 → 权重
    pub default: BTreeMap<String, f64>,
    /// regime 配置键（Bull/Bear/Sideways/HighVol/Crisis/Unknown）→ 模型名 → 权重。
    /// Phase 1 起 RegimeDetector 输出非 Unknown 时生效。
    #[serde(default)]
    pub by_regime: BTreeMap<String, BTreeMap<String, f64>>,
}

impl WeightSpec {
    /// 某 regime 下的权重表：优先 by_regime 覆盖，否则 default。
    pub fn weights_for(&self, regime: Regime) -> &BTreeMap<String, f64> {
        self.by_regime.get(regime.config_key()).unwrap_or(&self.default)
    }
}

/// 权重配置（机会 / 风险两个通道各自独立）。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct WeightsConfig {
    pub opportunity: WeightSpec,
    pub risk: WeightSpec,
}

/// 建议规则匹配条件：出现的条件全部满足才算命中。
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct Condition {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub opportunity_gte: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub opportunity_lte: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub risk_gte: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub risk_lte: Option<f64>,
}

impl Condition {
    pub(crate) fn matches(&self, opportunity: f64, risk: f64) -> bool {
        self.opportunity_gte.is_none_or(|t| opportunity >= t)
            && self.opportunity_lte.is_none_or(|t| opportunity <= t)
            && self.risk_gte.is_none_or(|t| risk >= t)
            && self.risk_lte.is_none_or(|t| risk <= t)
    }

    /// 条件的可读描述（进入 rule_triggers，可追溯）。
    pub(crate) fn describe(&self) -> String {
        let mut parts = Vec::new();
        if let Some(t) = self.opportunity_gte {
            parts.push(format!("机会≥{t}"));
        }
        if let Some(t) = self.opportunity_lte {
            parts.push(format!("机会≤{t}"));
        }
        if let Some(t) = self.risk_gte {
            parts.push(format!("风险≥{t}"));
        }
        if let Some(t) = self.risk_lte {
            parts.push(format!("风险≤{t}"));
        }
        parts.join(" 且 ")
    }
}

/// 单条 action 规则：`if` + `then`，或 `default` 兜底。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum ActionRule {
    Conditional {
        #[serde(rename = "if")]
        condition: Condition,
        then: Action,
    },
    Default {
        default: Action,
    },
}

/// 评分配置：model_version + weights（默认 + 按 regime 覆盖）+ action_rules。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScoringConfig {
    /// 如 "rule-v0.1"
    pub model_version: String,
    pub weights: WeightsConfig,
    /// 按序匹配，首条命中即生效；`default` 规则兜底（应放最后）。
    pub action_rules: Vec<ActionRule>,
    /// 未在 weights 中登记的模型（如临时注册的 Python 哑模型）使用的默认权重，
    /// 保证其贡献体现在综合分中。设为 0 则完全忽略未登记模型。
    #[serde(default = "default_unknown_model_weight")]
    pub unknown_model_weight: f64,
}

fn default_unknown_model_weight() -> f64 {
    0.2
}

impl ScoringConfig {
    pub fn from_yaml_str(s: &str) -> Result<Self> {
        serde_yaml::from_str(s)
            .map_err(|e| MeridianError::InvalidConfig(format!("解析评分配置失败: {e}")))
    }

    pub fn from_yaml_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path).map_err(|e| {
            MeridianError::InvalidConfig(format!("读取评分配置 {} 失败: {e}", path.display()))
        })?;
        Self::from_yaml_str(&text)
    }

    /// 配置指纹：规范化配置（serde_json 序列化，注释/键序无关）的 sha256 前 16 位。
    pub fn fingerprint(&self) -> String {
        let json = serde_json::to_vec(self).expect("ScoringConfig 必然可序列化");
        let digest = Sha256::digest(&json);
        let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
        hex[..16].to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// PLAN.md 第 10 节 Step 8 的示例配置（原样）。
    const SAMPLE_YAML: &str = r#"
model_version: "rule-v0.1"
weights:
  opportunity:
    default: { trend_model: 0.40, momentum_model: 0.30, capital_model: 0.30 }
    by_regime:
      Bear:  { trend_model: 0.30, momentum_model: 0.20, capital_model: 0.50 }
  risk:
    default: { risk_model: 1.0 }
action_rules:
  - if: { opportunity_gte: 75, risk_lte: 40 }
    then: Add
  - if: { opportunity_gte: 50 }
    then: Hold
  - if: { opportunity_gte: 35 }
    then: Watch
  - default: Avoid
"#;

    #[test]
    fn parse_plan_sample_yaml() {
        let cfg = ScoringConfig::from_yaml_str(SAMPLE_YAML).unwrap();
        assert_eq!(cfg.model_version, "rule-v0.1");
        assert_eq!(cfg.weights.opportunity.default["trend_model"], 0.40);
        assert_eq!(cfg.weights.opportunity.by_regime["Bear"]["capital_model"], 0.50);
        assert_eq!(cfg.weights.risk.default["risk_model"], 1.0);
        // 规则：3 条条件规则 + 1 条默认
        assert_eq!(cfg.action_rules.len(), 4);
        assert_eq!(cfg.action_rules[0], ActionRule::Conditional {
            condition: Condition {
                opportunity_gte: Some(75.0),
                opportunity_lte: None,
                risk_gte: None,
                risk_lte: Some(40.0),
            },
            then: Action::Add,
        });
        assert_eq!(cfg.action_rules[3], ActionRule::Default { default: Action::Avoid });
        assert_eq!(cfg.unknown_model_weight, 0.2);
    }

    #[test]
    fn weights_for_regime_fallback() {
        let cfg = ScoringConfig::from_yaml_str(SAMPLE_YAML).unwrap();
        // Bear 有覆盖档
        assert_eq!(cfg.weights.opportunity.weights_for(Regime::Bear)["momentum_model"], 0.20);
        // Unknown 无覆盖 → 回落 default
        assert_eq!(cfg.weights.opportunity.weights_for(Regime::Unknown)["trend_model"], 0.40);
        assert_eq!(cfg.weights.risk.weights_for(Regime::Bull)["risk_model"], 1.0);
    }

    #[test]
    fn condition_matching() {
        let c = Condition { opportunity_gte: Some(75.0), risk_lte: Some(40.0), ..Default::default() };
        assert!(c.matches(80.0, 30.0));
        assert!(!c.matches(80.0, 50.0));
        assert!(!c.matches(70.0, 30.0));
        // 无条件 → 恒真
        assert!(Condition::default().matches(0.0, 0.0));
    }

    #[test]
    fn fingerprint_stable_and_sensitive() {
        let a = ScoringConfig::from_yaml_str(SAMPLE_YAML).unwrap();
        let b = ScoringConfig::from_yaml_str(SAMPLE_YAML).unwrap();
        assert_eq!(a.fingerprint(), b.fingerprint());
        assert_eq!(a.fingerprint().len(), 16);

        let mut changed = a.clone();
        changed.weights.opportunity.default.insert("trend_model".into(), 0.5);
        assert_ne!(a.fingerprint(), changed.fingerprint());
    }

    #[test]
    fn reject_garbage() {
        assert!(ScoringConfig::from_yaml_str("not: [valid").is_err());
        assert!(ScoringConfig::from_yaml_str("").is_err());
    }
}
