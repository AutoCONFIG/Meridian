"""SummaryAgent：LLM 把规则报告转译成人话摘要（Phase 2）。

架构红线（PLAN.md 第 6 节）：本 Agent 只输出解释性文本，没有评分字段，
不改 action 建议——量化负责可信，AI 负责理解，人负责决策。
环境变量（见 llm_client）未配置或调用失败时返回 None，报告保持纯规则版本。
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.llm_client import LlmConfig, chat_completion

_SYSTEM_PROMPT = (
    "你是量化分析报告的转译者。把规则引擎生成的报告转成 3-5 句中文摘要，"
    "面向个人投资者：先说结论（建议是什么），再说主要依据（哪些因子/触发项贡献最大），"
    "最后一句风险提示。禁止给出新的评分或新的操作建议——你只能解释报告里已有的内容。"
)


@dataclass(frozen=True)
class SummaryAgent:
    """规则报告 → 人话摘要。cfg=None 表示未启用。"""

    cfg: LlmConfig | None = None

    @classmethod
    def from_env(cls) -> "SummaryAgent":
        return cls(LlmConfig.from_env())

    @property
    def enabled(self) -> bool:
        return self.cfg is not None

    def summarize(self, rule_report: str) -> str | None:
        """返回 AI 摘要文本；未启用/失败返回 None（调用方降级，不阻断报告）。"""
        if self.cfg is None:
            return None
        try:
            return chat_completion(self.cfg, _SYSTEM_PROMPT, rule_report)
        except Exception:  # noqa: BLE001 —— AI 摘要属增强项，任何失败都降级
            return None
