"""组合分析（Phase 3）：集中度 / 相关性 / 风险暴露 / 规则仓位汇总。

输入：标的池或显式列表 + 本地库K线（离线优先）。全部基于规则评分产物——
评分可追溯（factors/指纹落库），本层只做组合数学，不引入新预测。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from meridian.orchestrator.pipeline import AnalysisPipeline

_RET_WINDOW = 120  # 相关性估计窗口（交易日）


@dataclass(frozen=True)
class PortfolioRow:
    """组合内单标的的分析快照。"""

    symbol: str
    name: str
    market: str
    weight: float
    opportunity: float
    risk: float
    action: str
    position_hint: float | None  # 规则仓位参考（未配置 position 的规则为 None）
    regime: str


def load_portfolio_weights(root: Path | None = None) -> dict[str, float]:
    """config/portfolio.yaml 的 holdings 节（symbol → 权重）；缺失返回空（等权）。"""
    import yaml

    path = (root or Path(__file__).resolve().parents[2]) / "config" / "portfolio.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): float(v) for k, v in (raw.get("holdings") or {}).items()}


def concentration_hhi(weights: list[float]) -> float:
    """HHI 集中度：Σw²（权重归一化后）。等权 n 持仓 → 1/n；全押一只 → 1。"""
    total = sum(weights)
    if total <= 0:
        return 0.0
    return sum((w / total) ** 2 for w in weights)


def return_correlation(frames: dict[str, pd.DataFrame], window: int = _RET_WINDOW) -> pd.DataFrame:
    """多标的日收益率 Pearson 相关矩阵（尾部对齐，各取最近 window 根）。"""
    rets = {}
    for sym, df in frames.items():
        close = df["close"].astype(float).reset_index(drop=True)
        r = close.pct_change().dropna().tail(window - 1)
        r.index = range(len(r))
        rets[sym] = r
    aligned = pd.DataFrame(rets).dropna()
    if aligned.empty or len(aligned) < 10:
        raise ValueError("重叠收益样本不足，无法估计相关性（需 ≥10 个共同交易日）")
    return aligned.corr()


class PortfolioAnalyzer:
    """对标的池/显式列表做组合层面汇总（离线读本地库，不依赖网络）。"""

    def __init__(self, pipeline: "AnalysisPipeline"):
        self.pipeline = pipeline

    def analyze(self, symbols: list[str] | None = None, market: str | None = None) -> dict:
        """逐标的离线分析 → 组合指标。

        symbols=None 时用标的池（market 可过滤）。权重来自 config/portfolio.yaml
        的 holdings，未配置的标的等权。
        """
        universe = self._resolve_universe(symbols, market)
        cfg_weights = load_portfolio_weights(self.pipeline._root)

        rows: list[PortfolioRow] = []
        frames: dict[str, pd.DataFrame] = {}
        for entry, sym in universe:
            result = self.pipeline.analyze(sym.symbol, offline=True, name=sym.name)
            frames[sym.symbol] = result.df
            hint = result.score["action"].get("position_hint")
            rows.append(PortfolioRow(
                symbol=sym.symbol, name=sym.name, market=entry.market,
                weight=cfg_weights.get(sym.symbol, 0.0),
                opportunity=result.opportunity, risk=result.risk,
                action=result.action, position_hint=hint, regime=result.regime,
            ))

        # 权重：显式配置优先（归一化，未配置者 0）；全部未配置 → 等权
        if cfg_weights and any(r.weight > 0 for r in rows):
            total = sum(r.weight for r in rows if r.weight > 0)
            rows = [
                replace(r, weight=(r.weight / total if r.weight > 0 else 0.0))
                for r in rows
            ]
        elif rows:
            equal = 1.0 / len(rows)
            rows = [replace(r, weight=equal) for r in rows]

        corr = return_correlation(frames)
        hhi = concentration_hhi([r.weight for r in rows])
        # 组合风险暴露：权重加权平均风险分（0-100 口径）
        risk_exposure = sum(r.weight * r.risk for r in rows if r.weight > 0)
        # 组合规则仓位：Σ(w × position_hint)，None 视作该标的"建议空仓"（0）
        position_suggestion = sum(
            r.weight * (r.position_hint or 0.0) for r in rows if r.weight > 0
        )

        return {
            "rows": rows,
            "correlation": corr,
            "concentration_hhi": hhi,
            "effective_holdings": round(1 / hhi, 2) if hhi > 0 else 0.0,
            "risk_exposure": risk_exposure,
            "position_suggestion": position_suggestion,
            "weights_configured": bool(cfg_weights),
        }

    def _resolve_universe(self, symbols: list[str] | None, market: str | None):
        """显式列表 → find_or_auto；否则标的池（market 过滤）。"""
        from meridian.data.base import BAR_COLUMNS  # noqa: F401 保持依赖一致

        if symbols:
            return [self.pipeline._resolve(s) for s in symbols]
        out = []
        for entry in self.pipeline.markets_cfg.markets:
            if market and entry.market != market:
                continue
            for sym in entry.symbols:
                out.append((entry, sym))
        return out
