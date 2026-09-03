"""编排管线：拉数 → 存库 → 三层评分 → Markdown 报告。

Phase 0 数据流（PLAN.md 第 5/9 节）：
    DataSource.fetch_daily → PyDb.insert_bars（UPSERT 幂等）
    → PyEngine.evaluate（Rust 指标 + 规则模型 + Python 模型桥接）
    → AnalysisResult（Markdown 渲染）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from meridian import meridian_core as mc
from meridian.config import (
    AppConfig,
    ConfigError,
    DataSourceConfig,
    MarketEntry,
    MarketsConfig,
    SymbolEntry,
)
from meridian.data.base import BAR_COLUMNS, DataError, DataSource, FetchRequest

ACTION_SENTENCE = {
    "Add": "机会与风险条件满足规则 Add 档",
    "Hold": "维持现状",
    "Reduce": "触发 Reduce 档规则",
    "Watch": "观望，等待更明确信号",
    "Avoid": "触发回避规则",
}


@dataclass
class AnalysisResult:
    """一次分析的完整产物（报告 / 落库 / 前端共用的结构）。"""

    symbol: str
    name: str
    market: str
    asset_type: str
    frequency: str
    regime: str
    bar_count: int
    start: str
    end: str
    score: dict

    # ---- 视图便捷属性 ----
    @property
    def opportunity(self) -> float:
        return float(self.score["opportunity"]["score"])

    @property
    def risk(self) -> float:
        return float(self.score["risk"]["score"])

    @property
    def action(self) -> str:
        return str(self.score["action"]["action"])

    # ---- Markdown 渲染 ----
    def to_markdown(self, generated_at: datetime | None = None) -> str:
        ts = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        action = self.score["action"]
        lines = [
            f"# Meridian 分析报告 — {self.name} ({self.symbol})",
            "",
            f"- 生成时间：{ts}",
            f"- 市场/类型/频率：{self.market} / {self.asset_type} / {self.frequency}",
            f"- 数据区间：{self.start} ~ {self.end}（{self.bar_count} 根日K）",
            f"- 市场状态（regime）：{self.regime}",
            "",
            "## 三层评分",
            "",
            "| 层 | 分数 | 说明 |",
            "| --- | ---: | --- |",
            f"| 机会 Opportunity | {self.opportunity:.1f} / 100 | 高 = 机会大 |",
            f"| 风险 Risk | {self.risk:.1f} / 100 | 独立维度：高 = 风险高 |",
            f"| 建议 Action | **{action['action']}** | {action.get('description', '')} |",
            "",
            f"> 建议由 action_rules 规则匹配生成：{'；'.join(action.get('rule_triggers', []))}",
            f"> 配置指纹：`{self.score['config_fingerprint']}`（model_version: {self.score['model_version']}）",
            "",
        ]

        for title, layer in (("机会因子明细", "opportunity"), ("风险因子明细", "risk")):
            lines += [f"## {title}", "", "| 模型 | 输出分 | 加权贡献 | 说明 |", "| --- | ---: | ---: | --- |"]
            for f in self.score[layer]["factors"]:
                lines.append(
                    f"| {f['name']} | {f['value']:.2f} | {f['contribution']:+.2f} | {f['description']} |"
                )
            lines.append("")

        lines += [
            "---",
            "",
            "本报告由规则引擎自动生成，仅供参考，不构成投资建议。",
            "**量化负责可信，AI 负责理解，人负责决策。**",
            "",
        ]
        return "\n".join(lines)


class AnalysisPipeline:
    """面向单标的的分析管线。

    extra_models: iterable of (name, model_object_or_callable, channel) ——
    Python 模型经桥接注册进引擎（哑模型 / 未来 AI 预测模型）。
    """

    def __init__(
        self,
        root: Path | None = None,
        source: DataSource | None = None,
        extra_models: Iterable[tuple[str, object, str]] = (),
        persist: bool = True,
    ):
        self.app = AppConfig.load(root)
        self.markets_cfg = MarketsConfig.load(root)
        self.source = source or self._default_source(root)
        self.persist = persist
        self._extra_models = list(extra_models)
        self._root = root or Path(__file__).resolve().parents[3]
        self._db: mc.PyDb | None = None
        self._engine: mc.PyEngine | None = None

    # ---- 初始化懒加载 ----
    @staticmethod
    def _default_source(root: Path | None) -> DataSource:
        from meridian.data.cn_stock import CnStockSource

        return CnStockSource(DataSourceConfig.load(root))

    def engine(self) -> mc.PyEngine:
        if self._engine is None:
            # 资产类型 → scoring yaml（验收标准 7：加资产类型不改核心代码）
            stock_entry = self.markets_cfg.markets[0]
            yaml_path = self._root / "config" / "scoring" / stock_entry.scoring_config()
            self._engine = mc.PyEngine(str(yaml_path))
            self._engine.add_builtin_models()
            for name, model, channel in self._extra_models:
                self._engine.add_python_model(name, model, channel)
        return self._engine

    def db(self) -> mc.PyDb:
        if self._db is None:
            self.app.data_dir.mkdir(parents=True, exist_ok=True)
            self._db = mc.PyDb.open(str(self.app.data_dir / "meridian.duckdb"))
        return self._db

    def _resolve(self, symbol: str) -> tuple[MarketEntry, SymbolEntry]:
        entry = self.markets_cfg.find(symbol)
        sym = next(s for s in entry.symbols if s.symbol == symbol)
        return entry, sym

    # ---- 主流程 ----
    def analyze(self, symbol: str, start: str | None = None, end: str | None = None) -> AnalysisResult:
        entry, sym = self._resolve(symbol)
        end_date = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
        start_date = date.fromisoformat(start) if start else end_date - timedelta(days=240)

        df = self.source.fetch_daily(FetchRequest(symbol, start_date.isoformat(), end_date.isoformat()))
        if df.empty or len(df) < 30:
            raise DataError(f"{symbol} 有效K线不足（{0 if df.empty else len(df)} 根），至少需要 30 根")

        if self.persist:
            self.db().insert_bars(
                symbol=sym.symbol, name=sym.name, market=entry.market,
                asset_type=entry.asset_type, frequency=entry.frequency,
                dates=[d.isoformat() for d in df["date"]],
                opens=df["open"].tolist(), highs=df["high"].tolist(),
                lows=df["low"].tolist(), closes=df["close"].tolist(),
                volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
            )

        score = self.engine().evaluate(
            symbol=sym.symbol, name=sym.name, market=entry.market,
            asset_type=entry.asset_type, frequency=entry.frequency,
            dates=[d.isoformat() for d in df["date"]],
            opens=df["open"].tolist(), highs=df["high"].tolist(),
            lows=df["low"].tolist(), closes=df["close"].tolist(),
            volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
        )

        return AnalysisResult(
            symbol=sym.symbol,
            name=sym.name,
            market=entry.market,
            asset_type=entry.asset_type,
            frequency=entry.frequency,
            regime="unknown",  # Phase 0：NullDetector 恒 Unknown
            bar_count=len(df),
            start=str(df["date"].iloc[0]),
            end=str(df["date"].iloc[-1]),
            score=score,
        )

    def write_report(self, result: AnalysisResult, output: str | Path | None = None) -> Path:
        out_dir = self.app.report_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = Path(output) if output else out_dir / f"{result.symbol}_{date.today().isoformat()}.md"
        path.write_text(result.to_markdown(), encoding="utf-8")
        return path
