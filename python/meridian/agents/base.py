"""Research Agent 抽象基类（Phase 0 只留此文件，8 个 Agent 为 Phase 5）。

设计约束（PLAN.md 第 3 节）：ResearchAgent 输出**信息报告**（事件/逻辑/异常），
不产生 score、不进综合引擎加权，与量化结论并排呈现。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class AgentReport:
    """研究报告：纯信息，无评分字段（刻意）。"""

    agent_name: str
    title: str
    body: str
    tags: list[str] = field(default_factory=list)


class ResearchAgent(abc.ABC):
    """研究代理：investigate 返回信息报告，绝不返回分数。"""

    name: str = "base"

    @abc.abstractmethod
    def investigate(self, symbol: str) -> list[AgentReport]:
        """针对标的开展调查，返回报告列表。"""
