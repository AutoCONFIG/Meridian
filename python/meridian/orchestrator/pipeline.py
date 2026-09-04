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

# 数据来源代码 → 报告展示文案
DATA_SOURCE_LABEL = {
    "live": "数据源拉取",
    "store": "本地库（增量同步）",
    "cache": "本地缓存（DuckDB）",
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
    data_source: str = "live"  # live=数据源拉取 / cache=本地缓存回退
    fallback_reason: str | None = None  # 回退原因（数据源失败详情 / 离线模式）

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
            f"- 数据来源：{DATA_SOURCE_LABEL.get(self.data_source, self.data_source)}"
            + (f"；{self.fallback_reason}" if self.fallback_reason else ""),
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
        self._engines: dict[str, mc.PyEngine] = {}  # asset_type → 引擎

    # ---- 初始化懒加载 ----
    @staticmethod
    def _default_source(root: Path | None) -> DataSource:
        from meridian.data.composite import build_cn_stock_daily

        # 多源组合：akshare（东财）为主，腾讯 failover + 跨源对账
        return build_cn_stock_daily(DataSourceConfig.load(root))

    def engine(self, asset_type: str | None = None) -> mc.PyEngine:
        """资产类型 → scoring yaml 构建引擎（验收标准 7：加资产类型不改核心代码）。

        港/美/期货各自的 scoring yaml 独立加载，避免用股票权重静默算分。
        """
        key = asset_type or self.markets_cfg.markets[0].asset_type
        if key not in self._engines:
            yaml_path = self._root / "config" / "scoring" / f"{key}.yaml"
            if not yaml_path.exists():
                raise ConfigError(f"缺少评分配置: {yaml_path}（asset_type={key}）")
            engine = mc.PyEngine(str(yaml_path))
            engine.add_builtin_models()
            for name, model, channel in self._extra_models:
                engine.add_python_model(name, model, channel)
            self._engines[key] = engine
        return self._engines[key]

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
    def analyze(
        self, symbol: str, start: str | None = None, end: str | None = None, offline: bool = False
    ) -> AnalysisResult:
        entry, sym = self._resolve(symbol)
        end_date = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
        start_date = date.fromisoformat(start) if start else end_date - timedelta(days=240)

        df: pd.DataFrame = pd.DataFrame(columns=BAR_COLUMNS)
        fetch_error: DataError | None = None
        data_source, fallback_reason = "live", None

        if offline:
            data_source = "cache"
            df = self._read_cache(entry, sym, start_date, end_date)
            if len(df):
                fallback_reason = "离线模式（--offline）"
        elif self.persist:
            # 增量同步（库内最新日期为游标，只拉缺口）→ 从库读分析窗口；
            # 同步失败回退本地库，明确标注，不静默。
            try:
                self._sync_store(entry, sym, end_date)
            except DataError as exc:
                fetch_error = exc
            df = self._read_cache(entry, sym, start_date, end_date)
            if fetch_error is not None:
                data_source = "cache"
                fallback_reason = f"数据源拉取失败，自动回退: {fetch_error}"
            else:
                data_source = "store"
                if len(df) < 30:
                    # 库内窗口不足（如指定了更早的 start）→ 源补拉指定窗口
                    try:
                        live = self.source.fetch_daily(
                            FetchRequest(symbol, start_date.isoformat(), end_date.isoformat()))
                        if not live.empty:
                            self._insert_bars(entry, sym, live)
                            df = self._read_cache(entry, sym, start_date, end_date)
                    except DataError as exc2:
                        fetch_error = fetch_error or exc2
        else:
            try:
                df = self.source.fetch_daily(
                    FetchRequest(symbol, start_date.isoformat(), end_date.isoformat()))
            except DataError as exc:
                fetch_error = exc

        # live 不足时本地缓存兜底（保持 Phase 0 语义：缓存更多就用缓存）
        if len(df) < 30:
            cached = self._read_cache(entry, sym, start_date, end_date)
            if len(cached) > len(df):
                df = cached
                data_source = "cache"
                fallback_reason = fallback_reason or (
                    "离线模式（--offline）" if offline
                    else f"数据源拉取失败，自动回退: {fetch_error}")

        if df.empty or len(df) < 30:
            origin = "离线模式下" if offline else "数据源与"
            raise DataError(
                f"{symbol} 有效K线不足（{len(df)} 根，至少 30 根；本地缓存 {len(df)} 根）。"
                f"{origin}本地缓存均无可用数据"
                + (f"；数据源错误: {fetch_error}" if fetch_error else "")
            )

        if self.persist and data_source == "live":
            self._insert_bars(entry, sym, df)

        score = self.engine(entry.asset_type).evaluate(
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
            data_source=data_source,
            fallback_reason=fallback_reason,
        )

    def _read_cache(self, entry: MarketEntry, sym: SymbolEntry, start_date: date, end_date: date) -> pd.DataFrame:
        """从本地 DuckDB 读K线（缓存回退 / 离线模式）。库中无数据返回空表。"""
        rows = self.db().read_bars(
            symbol=sym.symbol,
            name=sym.name,
            market=entry.market,
            asset_type=entry.asset_type,
            frequency=entry.frequency,
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS)
        df = pd.DataFrame(rows)[BAR_COLUMNS]
        df["date"] = pd.to_datetime(df["date"]).dt.date  # read_bars 返回字符串日期，与 live 路径对齐
        return df.reset_index(drop=True)

    def _sync_store(self, entry: MarketEntry, sym: SymbolEntry, end_date: date):
        """增量同步：库内最新日期为游标，只拉缺口并 UPSERT（见 data/sync.py）。"""
        from meridian.data.sync import DailySyncer

        return DailySyncer(self.source, self.db()).sync(
            symbol=sym.symbol, name=sym.name, market=entry.market,
            asset_type=entry.asset_type, frequency=entry.frequency,
            end=end_date.isoformat(),
        )

    def _insert_bars(self, entry: MarketEntry, sym: SymbolEntry, df: pd.DataFrame) -> int:
        return self.db().insert_bars(
            symbol=sym.symbol, name=sym.name, market=entry.market,
            asset_type=entry.asset_type, frequency=entry.frequency,
            dates=[d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]],
            opens=[float(v) for v in df["open"]],
            highs=[float(v) for v in df["high"]],
            lows=[float(v) for v in df["low"]],
            closes=[float(v) for v in df["close"]],
            volumes=[float(v) for v in df["volume"]],
            amounts=[float(v) for v in df["amount"]],
        )

    def write_report(self, result: AnalysisResult, output: str | Path | None = None) -> Path:
        out_dir = self.app.report_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = Path(output) if output else out_dir / f"{result.symbol}_{date.today().isoformat()}.md"
        path.write_text(result.to_markdown(), encoding="utf-8")
        return path
