use serde::{Deserialize, Serialize};

use crate::error::MeridianError;

/// 方向判断。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Direction {
    Up,
    Down,
    Neutral,
}

impl Direction {
    pub fn as_str(&self) -> &'static str {
        match self {
            Direction::Up => "up",
            Direction::Down => "down",
            Direction::Neutral => "neutral",
        }
    }
}

impl std::str::FromStr for Direction {
    type Err = MeridianError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "up" => Ok(Direction::Up),
            "down" => Ok(Direction::Down),
            "neutral" => Ok(Direction::Neutral),
            other => Err(MeridianError::Data(format!("未知方向: {other}"))),
        }
    }
}

/// 单条因子：模型输出的可追溯明细（评分可追溯原则）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Factor {
    /// 因子名，如 "均线多头排列"
    pub name: String,
    /// 因子原始值
    pub value: f64,
    /// 对该层得分的贡献
    pub contribution: f64,
    /// 人话解释
    pub description: String,
    /// 内部触发明细：综合层把模型的规则触发挂在其因子上，
    /// 报告借此回答"这个分数为什么"。直接产出的因子可为空。
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub details: Vec<Factor>,
}

impl Factor {
    pub fn new(
        name: impl Into<String>,
        value: f64,
        contribution: f64,
        description: impl Into<String>,
    ) -> Self {
        Self {
            name: name.into(),
            value,
            contribution,
            description: description.into(),
            details: Vec::new(),
        }
    }
}

/// 单个模型的输出（规则模型与 AI 预测模型同格式，统一接口原则）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ModelOutput {
    /// 0-100（综合引擎统一 clamp）
    pub score: f64,
    pub direction: Direction,
    /// 0-1
    pub confidence: f64,
    pub factors: Vec<Factor>,
}

impl ModelOutput {
    /// 将 score 收敛到 [0,100]、confidence 收敛到 [0,1]；非有限值回退为中性默认。
    /// 统一口径，防个别模型输出越界值污染综合引擎。
    pub fn normalized(mut self) -> Self {
        if !self.score.is_finite() {
            self.score = 50.0;
        }
        self.score = self.score.clamp(0.0, 100.0);
        if !self.confidence.is_finite() {
            self.confidence = 0.0;
        }
        self.confidence = self.confidence.clamp(0.0, 1.0);
        self
    }
}

/// 机会层得分（高 = 机会大）。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OpportunityScore {
    pub score: f64,
    pub factors: Vec<Factor>,
}

/// 风险层得分（高 = 风险高）。独立维度，非扣分项。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct RiskScore {
    pub score: f64,
    pub factors: Vec<Factor>,
}

/// 操作建议。只能由 CompositeEngine 按 action_rules 规则匹配生成，AI 不可干预。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Action {
    Add,
    Hold,
    Reduce,
    Watch,
    Avoid,
}

impl Action {
    pub fn as_str(&self) -> &'static str {
        match self {
            Action::Add => "Add",
            Action::Hold => "Hold",
            Action::Reduce => "Reduce",
            Action::Watch => "Watch",
            Action::Avoid => "Avoid",
        }
    }

    pub fn description(&self) -> &'static str {
        match self {
            Action::Add => "可考虑增加关注/仓位（人做最终决策）",
            Action::Hold => "维持现状",
            Action::Reduce => "可考虑降低仓位",
            Action::Watch => "观望，等待更明确信号",
            Action::Avoid => "回避",
        }
    }
}

impl std::str::FromStr for Action {
    type Err = MeridianError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "add" => Ok(Action::Add),
            "hold" => Ok(Action::Hold),
            "reduce" => Ok(Action::Reduce),
            "watch" => Ok(Action::Watch),
            "avoid" => Ok(Action::Avoid),
            other => Err(MeridianError::Data(format!("未知建议动作: {other}"))),
        }
    }
}

/// 建议输出（含触发的规则，可追溯）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ActionOutput {
    pub action: Action,
    /// 规则式仓位参考，Phase 3 起有值
    pub position_hint: Option<f64>,
    /// 触发了哪条规则
    pub rule_triggers: Vec<String>,
}

/// 综合三层评分结果（最终产物，落库 trend_scores 表）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CompositeScore {
    pub opportunity: OpportunityScore,
    pub risk: RiskScore,
    pub action: ActionOutput,
    /// 如 "rule-v0.1"
    pub model_version: String,
    /// 生效配置内容 sha256 前16位
    pub config_fingerprint: String,
}

/// 市场状态。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Regime {
    Bull,
    Bear,
    Sideways,
    HighVol,
    Crisis,
    Unknown,
}

impl Regime {
    /// scoring yaml 中 `by_regime` 键使用的名称。
    pub fn config_key(&self) -> &'static str {
        match self {
            Regime::Bull => "Bull",
            Regime::Bear => "Bear",
            Regime::Sideways => "Sideways",
            Regime::HighVol => "HighVol",
            Regime::Crisis => "Crisis",
            Regime::Unknown => "Unknown",
        }
    }

    pub fn as_str(&self) -> &'static str {
        self.config_key()
    }
}

impl std::str::FromStr for Regime {
    type Err = MeridianError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "bull" => Ok(Regime::Bull),
            "bear" => Ok(Regime::Bear),
            "sideways" => Ok(Regime::Sideways),
            "highvol" | "high_vol" | "high-vol" => Ok(Regime::HighVol),
            "crisis" => Ok(Regime::Crisis),
            "unknown" => Ok(Regime::Unknown),
            other => Err(MeridianError::Data(format!("未知市场状态: {other}"))),
        }
    }
}

/// 市场状态 + 置信度。Phase 0 恒为 Unknown（NullDetector 产出）。
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct RegimeState {
    pub regime: Regime,
    pub confidence: f64,
}

impl RegimeState {
    pub fn unknown() -> Self {
        Self {
            regime: Regime::Unknown,
            confidence: 0.0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn direction_parse_and_serde() {
        assert_eq!("UP".parse::<Direction>().unwrap(), Direction::Up);
        assert_eq!("neutral".parse::<Direction>().unwrap(), Direction::Neutral);
        assert!("sideways".parse::<Direction>().is_err());
        assert_eq!(
            serde_json::from_str::<Direction>("\"down\"").unwrap(),
            Direction::Down
        );
        assert_eq!(
            serde_json::to_string(&Direction::Up).unwrap(),
            "\"up\""
        );
    }

    #[test]
    fn action_mapping() {
        assert_eq!("add".parse::<Action>().unwrap(), Action::Add);
        assert_eq!("Avoid".parse::<Action>().unwrap(), Action::Avoid);
        assert_eq!(Action::Reduce.as_str(), "Reduce");
        assert!("buy".parse::<Action>().is_err());
        assert!(matches!(
            "乱写".parse::<Action>(),
            Err(MeridianError::Data(_))
        ));
    }

    #[test]
    fn model_output_normalized_clamps() {
        let out = ModelOutput {
            score: 150.0,
            direction: Direction::Up,
            confidence: 1.5,
            factors: vec![],
        }
        .normalized();
        assert_eq!(out.score, 100.0);
        assert_eq!(out.confidence, 1.0);

        let out = ModelOutput {
            score: f64::NAN,
            direction: Direction::Neutral,
            confidence: -3.0,
            factors: vec![],
        }
        .normalized();
        assert_eq!(out.score, 50.0);
        assert_eq!(out.confidence, 0.0);
    }

    #[test]
    fn composite_score_serde_roundtrip() {
        let cs = CompositeScore {
            opportunity: OpportunityScore {
                score: 72.5,
                factors: vec![Factor::new("均线多头排列", 1.0, 25.0, "收盘价位于均线上方")],
            },
            risk: RiskScore {
                score: 35.0,
                factors: vec![],
            },
            action: ActionOutput {
                action: Action::Hold,
                position_hint: None,
                rule_triggers: vec!["opportunity_gte: 50".to_string()],
            },
            model_version: "rule-v0.1".to_string(),
            config_fingerprint: "abcdef1234567890".to_string(),
        };
        let json = serde_json::to_string(&cs).unwrap();
        let back: CompositeScore = serde_json::from_str(&json).unwrap();
        assert_eq!(back, cs);
        assert!(json.contains("\"action\":\"Hold\""));
    }

    #[test]
    fn regime_config_key_matches_yaml() {
        assert_eq!(Regime::Bear.config_key(), "Bear");
        assert_eq!(Regime::HighVol.config_key(), "HighVol");
        assert_eq!(Regime::Unknown.config_key(), "Unknown");
        assert_eq!("bear".parse::<Regime>().unwrap(), Regime::Bear);
        assert_eq!("high_vol".parse::<Regime>().unwrap(), Regime::HighVol);
    }

    #[test]
    fn regime_state_unknown() {
        let s = RegimeState::unknown();
        assert_eq!(s.regime, Regime::Unknown);
        assert_eq!(s.confidence, 0.0);
    }
}
