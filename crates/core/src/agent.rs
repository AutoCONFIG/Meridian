//! ResearchAgent trait —— Phase 5 完整实现，Phase 0 仅占位定义（形状届时可微调）。
//!
//! 设计约束：Agent 输出**信息报告**（事件/逻辑/异常），不产生 score、
//! 不进综合引擎加权，与量化结论**并排呈现**（见 PLAN.md 第 4 节）。

use serde::{Deserialize, Serialize};

use crate::asset::Asset;
use crate::bar::Bar;
use crate::error::Result;
use crate::signal::RegimeState;

/// 研究上下文。
pub struct ResearchContext<'a> {
    pub asset: &'a Asset,
    pub regime: RegimeState,
    pub bars: &'a [Bar],
}

/// Agent 的信息报告（Markdown 正文 + 标签）。刻意没有评分字段。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AgentReport {
    pub agent_name: String,
    pub title: String,
    pub content_markdown: String,
    pub tags: Vec<String>,
}

/// 研究型 Agent 接口（新闻/宏观/情绪/公司/行业/组合/风险/异常 8 类，Phase 5 落地）。
pub trait ResearchAgent: Send {
    fn name(&self) -> &str;
    fn investigate(&self, ctx: &ResearchContext) -> Result<AgentReport>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::asset::{Asset, AssetType, Frequency, Market};

    struct StubAgent;

    impl ResearchAgent for StubAgent {
        fn name(&self) -> &str {
            "stub"
        }
        fn investigate(&self, ctx: &ResearchContext) -> Result<AgentReport> {
            Ok(AgentReport {
                agent_name: self.name().to_string(),
                title: format!("关于 {} 的研究观察", ctx.asset.name),
                content_markdown: "- 无异常信号".to_string(),
                tags: vec!["stub".to_string()],
            })
        }
    }

    fn assert_send<T: Send>() {}

    #[test]
    fn agent_produces_report_without_score() {
        assert_send::<Box<dyn ResearchAgent>>();
        let asset = Asset::new(
            "600519",
            "贵州茅台",
            Market::Cn,
            AssetType::Stock,
            Frequency::Daily,
        );
        let bars: Vec<Bar> = Vec::new();
        let ctx = ResearchContext {
            asset: &asset,
            regime: RegimeState::unknown(),
            bars: &bars,
        };
        let agents: Vec<Box<dyn ResearchAgent>> = vec![Box::new(StubAgent)];
        let report = agents[0].investigate(&ctx).unwrap();
        assert_eq!(report.agent_name, "stub");
        assert!(report.content_markdown.contains("无异常"));
    }
}
