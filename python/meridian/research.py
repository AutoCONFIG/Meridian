"""Research Agents（Phase 3）：只产出信息性研究笔记，没有评分字段（架构红线 2）。

笔记只能描述客观事实（区间涨跌/波动/量能/位置），不下操作结论、不产分数——
评分与建议只属于三层评分引擎。AnalysisResult 之外不读任何数据，保证与报告同源。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ResearchNote:
    """单条研究笔记（agent 名 + 标题 + 正文）。正文为客观描述，无评分无建议。"""

    agent: str
    title: str
    body: str


class TechnicalPostureAgent:
    """技术形态描述：区间涨跌、距高点回撤、均线位置。"""

    name = "technical"

    def investigate(self, result) -> list[ResearchNote]:
        df: pd.DataFrame = result.df
        if df is None or len(df) < 20:
            return []
        closes = df["close"].astype(float)
        n = len(closes)
        last = float(closes.iloc[-1])
        first = float(closes.iloc[0])
        hi = float(closes.max())
        ret = last / first - 1.0
        drawdown = last / hi - 1.0
        ma20 = float(closes.tail(20).mean())
        ma60 = float(closes.tail(60).mean()) if n >= 60 else None

        parts = [
            f"近 {n} 个交易日累计 {'上涨' if ret >= 0 else '下跌'} {abs(ret):.1%}"
            f"（{first:.2f} → {last:.2f}）",
            f"当前距区间最高点回撤 {abs(drawdown):.1f}%",
            f"收盘价位于 MA20 {'上方' if last >= ma20 else '下方'}（MA20={ma20:.2f}）",
        ]
        if ma60 is not None:
            parts.append(f"MA60={'上方' if last >= ma60 else '下方'}（MA60={ma60:.2f}）")

        return [ResearchNote(
            agent=self.name,
            title="技术形态",
            body="；".join(parts) + "。",
        )]


class VolatilityLiquidityAgent:
    """波动与量能描述：已实现波动、量能近远期对比。"""

    name = "vol_liquidity"

    def investigate(self, result) -> list[ResearchNote]:
        df: pd.DataFrame = result.df
        if df is None or len(df) < 30:
            return []
        closes = df["close"].astype(float)
        rets = closes.pct_change().dropna().tail(60)
        vol = float(rets.std()) * (252 ** 0.5)
        volume = df["volume"].astype(float)
        recent = float(volume.tail(5).mean())
        baseline = float(volume.tail(60).mean())

        parts = [f"近 60 日已实现年化波动 {vol:.1%}"]
        if baseline > 0:
            ratio = recent / baseline
            level = "放量" if ratio >= 1.3 else ("缩量" if ratio <= 0.7 else "量能平稳")
            parts.append(f"近 5 日均量为 60 日均量的 {ratio:.0%}（{level}）")

        return [ResearchNote(
            agent=self.name,
            title="波动与量能",
            body="；".join(parts) + "。",
        )]


class ResearchTeam:
    """跑全部研究 agent，汇总笔记（任何 agent 失败跳过，不阻断）。"""

    def __init__(self, agents: list | None = None):
        self.agents = agents or [TechnicalPostureAgent(), VolatilityLiquidityAgent()]

    def investigate(self, result) -> list[ResearchNote]:
        notes: list[ResearchNote] = []
        for agent in self.agents:
            try:
                notes.extend(agent.investigate(result))
            except Exception:  # noqa: BLE001 —— 研究笔记属增强信息
                continue
        return notes


def render_notes(notes: list[ResearchNote]) -> list[str]:
    """笔记 → markdown「研究视角」节（无笔记返回空）。"""
    if not notes:
        return []
    lines = ["## 研究视角", "", "> 以下为规则引擎产出的客观事实描述，不含评分与建议。", ""]
    lines += [f"- **{note.title}**（{note.agent}）：{note.body}" for note in notes]
    lines.append("")
    return lines
