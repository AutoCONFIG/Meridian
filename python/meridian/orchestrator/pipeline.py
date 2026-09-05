"""编排管线：拉数 → 存库 → 三层评分 → Markdown 报告。

Phase 0 数据流（PLAN.md 第 5/9 节）：
    DataSource.fetch_daily → PyDb.insert_bars（UPSERT 幂等）
    → PyEngine.evaluate（Rust 指标 + 规则模型 + Python 模型桥接）
    → AnalysisResult（Markdown 渲染）
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, TYPE_CHECKING

import pandas as pd

from meridian import meridian_core as mc
from meridian.config import (
    AppConfig,
    ConfigError,
    DataSourceConfig,
    MarketEntry,
    MarketsConfig,
    RegimeConfig,
    SymbolEntry,
)
from meridian.data.base import BAR_COLUMNS, DataError, DataSource, FetchRequest

if TYPE_CHECKING:
    from meridian.ledger import LedgerBook

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

# 模型名 → 摘要用语（展示层映射，非评分逻辑）
_MODEL_LABEL = {
    "trend_model": "趋势",
    "momentum_model": "动量",
    "capital_model": "资金",
    "risk_model": "风险",
}
_DIR_TEXT = {"up": "向上", "down": "向下", "neutral": "中性"}

# Regime 代码 → 报告展示文案
_REGIME_LABEL = {
    "Bull": "上行",
    "Bear": "下行",
    "Sideways": "震荡",
    "HighVol": "高波动",
    "Crisis": "危机",
    "Unknown": "未知",
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
    df: pd.DataFrame | None = None  # 原始K线（画图用；展示层非评分链路）
    regime_confidence: float = 0.0  # regime 置信度（Unknown 时为 0）
    regime_basis: list[str] = field(default_factory=list)  # 判定依据（人话，报告展示）
    regime_detector: str = ""  # 检测器名（可追溯）
    ai_summary: str | None = None  # LLM 转译摘要（解释性文本；无评分无新建议——红线 1/2）
    research_notes: list = field(default_factory=list)  # ResearchNote 列表（客观事实，无评分——红线 2）
    fundamentals: dict | None = None  # 基本面速览（pe_ttm/pb 等；None=无数据）
    outlook: dict | None = None  # 短期预判（ATR 区间/动量外推，统计信息非建议）
    patterns: list = field(default_factory=list)  # K线形态识别（几何判定，非信号）

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
    def to_markdown(self, generated_at: datetime | None = None, chart_image: str | None = None) -> str:
        ts = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        action = self.score["action"]
        regime_label = _REGIME_LABEL.get(self.regime, self.regime)
        regime_note = (
            f"{regime_label}（置信度 {self.regime_confidence:.0%}）"
            if self.regime != "Unknown"
            else regime_label
        )
        lines = [
            f"# Meridian 分析报告 — {self.name} ({self.symbol})",
            "",
            f"- 生成时间：{ts}",
            f"- 市场/类型/频率：{self.market} / {self.asset_type} / {self.frequency}",
            f"- 数据区间：{self.start} ~ {self.end}（{self.bar_count} 根日K）",
            f"- 数据来源：{DATA_SOURCE_LABEL.get(self.data_source, self.data_source)}"
            + (f"；{self.fallback_reason}" if self.fallback_reason else ""),
            f"- 市场状态（regime）：{regime_note}",
            "",
        ]
        if self.regime_basis:
            lines += [f"> 状态判定依据（{self.regime_detector}）：{'；'.join(self.regime_basis)}", ""]
        if chart_image:
            lines += [f"![{self.name} ({self.symbol}) K线图]({chart_image})", ""]
        lines += self._summary_lines()
        if self.outlook:
            from meridian.forecast_view import render_outlook

            lines += render_outlook(self.outlook)
        if self.patterns:
            recent = self.patterns[-10:]
            lines += [
                "## K线形态（最近 10 个标注）",
                "",
                "| 日期 | 形态 | 倾向 | 含义 |",
                "| --- | --- | --- | --- |",
            ]
            lines += [
                f"| {p['date']} | {p['name']} | {p['bias']} | {p['desc']} |" for p in recent
            ]
            lines += ["", "> 形态为几何判定，不构成信号；完整列表见看板图内标注。", ""]
        if self.ai_summary:
            lines += [
                "## AI 摘要",
                "",
                self.ai_summary,
                "",
                "> 本节由 LLM 转译规则报告，仅解释已有内容，不产生新的评分或建议。",
                "",
            ]
        if self.research_notes:
            from meridian.research import render_notes

            lines += render_notes(self.research_notes)
        if self.fundamentals:
            f = self.fundamentals
            lines += ["## 基本面速览", "", f"- 数据日期：{f.get('date', '—')}（来源：{f.get('source', '—')}）"]
            for label, key in (("PE-TTM", "pe_ttm"), ("PB", "pb"), ("股息率%", "dv_ratio"), ("总市值(亿)", "total_mv")):
                v = f.get(key)
                if v is not None and v == v:  # 非 NaN
                    lines.append(f"- {label}：{float(v):.2f}" if "市值" not in label else f"- {label}：{float(v):.1f}")
            lines.append("")
        lines += [
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
            lines += self._trigger_detail_lines(layer)

        lines += [
            "---",
            "",
            "本报告由规则引擎自动生成，仅供参考，不构成投资建议。",
            "**量化负责可信，AI 负责理解，人负责决策。**",
            "",
        ]
        return "\n".join(lines)

    def _summary_lines(self) -> list[str]:
        """结论先行：规则模板把评分结构翻译成人话（不涉及 AI，全部来自落库字段）。"""
        action = self.score["action"]

        # 机会端：各模型方向一览（从因子描述提取方向词，综合层固定格式"方向X、……"）
        opp_parts: list[str] = []
        for f in self.score["opportunity"]["factors"]:
            direction = f["description"].split("、")[0].replace("方向", "")
            label = _MODEL_LABEL.get(f["name"], f["name"])
            opp_parts.append(f"{label}{_DIR_TEXT.get(direction, direction)}")

        # 风险端：取贡献为正的触发项，按贡献降序最多 3 条
        risk_parts: list[str] = []
        for f in self.score["risk"]["factors"]:
            triggers = [d for d in f.get("details", []) if d.get("contribution", 0) > 0]
            triggers.sort(key=lambda d: -d["contribution"])
            risk_parts += [d["description"].rstrip("。") for d in triggers[:3]]

        lines = ["## 结论", ""]
        if opp_parts:
            lines.append(f"机会端：{'、'.join(opp_parts)}。")
        lines.append(
            f"风险端：{'；'.join(risk_parts)}。" if risk_parts else f"风险端：风险分 {self.risk:.0f}/100。"
        )
        triggers = "；".join(action.get("rule_triggers", []))
        hint = action.get("position_hint")
        hint_text = f"，规则仓位参考 {hint:.0%}" if hint is not None else ""
        lines.append(
            f"综合：机会 {self.opportunity:.1f}/100、风险 {self.risk:.1f}/100 → **{action['action']}**"
            + (f"（规则：{triggers}）" if triggers else "")
            + hint_text
            + "。"
        )
        lines.append("")
        return lines

    def _trigger_detail_lines(self, layer: str) -> list[str]:
        """模型内部触发明细（"为什么"）：综合层从模型输出保留的规则触发。"""
        rows: list[str] = []
        for f in self.score[layer]["factors"]:
            for d in f.get("details", []):
                rows.append(
                    f"| {f['name']} | {d['name']} | {d['value']:.4g} | {d['contribution']:+.0f} | {d['description']} |"
                )
        if not rows:
            return []
        return [
            f"### {layer}·触发原因",
            "",
            "| 模型 | 触发项 | 实际值 | 贡献 | 说明 |",
            "| --- | --- | ---: | ---: | --- |",
            *rows,
            "",
        ]


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
        self._explicit_source = source
        self._root = root or Path(__file__).resolve().parents[3]
        self.persist = persist
        self._extra_models = list(extra_models)
        self._db: mc.PyDb | None = None
        self._ledger: "LedgerBook | None" = None
        self._engines: dict[str, mc.PyEngine] = {}  # asset_type → 引擎
        self._sources: dict[tuple[str, str], DataSource] = {}  # (market, asset_type) → 组合源
        self._regime_detector: "mc.PyRegimeDetector | None" = None
        self._index_sources: dict[str, "DataSource | None"] = {}  # market → 指数源（None=该市场无指数配置）
        self._fundamental_source: object | None = None  # 基本面源（懒构造；测试可注入 fake）

    # ---- 初始化懒加载 ----
    @property
    def source(self) -> DataSource:
        """缺省数据源（A 股日K组合链）。显式传入/测试替换优先。"""
        return self._source_for(None)

    @source.setter
    def source(self, value: DataSource) -> None:
        self._explicit_source = value

    def _source_for(self, entry: MarketEntry | None) -> DataSource:
        """按标的 (market, asset_type) 路由组合源；显式 source 覆盖一切路由。"""
        if self._explicit_source is not None:
            return self._explicit_source
        key = (entry.market if entry else "cn", entry.asset_type if entry else "stock")
        if key not in self._sources:
            self._sources[key] = self._build_source(*key)
        return self._sources[key]

    def _build_source(self, market: str, asset_type: str) -> DataSource:
        """市场/资产类型 → 多源组合（链与对账参数来自 config/data_sources.yaml）。"""
        from meridian.data.composite import (
            build_cn_stock_daily,
            build_futures_daily,
            build_global_daily,
        )

        cfg = DataSourceConfig.load(self._root)
        if asset_type == "futures":
            return build_futures_daily(cfg)
        if market == "hk":
            return build_global_daily("hk", cfg)
        if market == "us":
            return build_global_daily("us", cfg)
        # A股：akshare（东财）为主，腾讯 failover + 跨源对账
        return build_cn_stock_daily(cfg)

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
            for name, model, channel, category, version in self._load_registered_models():
                engine.add_python_model(name, model, channel, version=version, category=category)
            self._engines[key] = engine
        return self._engines[key]

    def _load_registered_models(self) -> list[tuple[str, object, str, str, str]]:
        """config/models.yaml → (name, 实例, channel, category, version)；缺失/单项失败跳过并告警。"""
        import importlib

        import yaml

        path = self._root / "config" / "models.yaml"
        if not path.exists():
            return []
        out: list[tuple[str, object, str, str, str]] = []
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"models.yaml 解析失败（忽略 Python 模型注册）: {exc}")
            return []
        for i, m in enumerate(raw.get("models") or []):
            try:
                cls = getattr(importlib.import_module(m["module"]), m["class"])
                out.append((
                    str(m["name"]), cls(), str(m["channel"]),
                    str(m.get("category", "ai_prediction")), str(m.get("version", "0.1.0")),
                ))
            except Exception as exc:  # noqa: BLE001
                warnings.warn(f"models.yaml 第 {i} 项加载失败（跳过）: {exc}")
        return out

    def db(self) -> mc.PyDb:
        if self._db is None:
            self.app.data_dir.mkdir(parents=True, exist_ok=True)
            self._db = mc.PyDb.open(str(self.app.data_dir / "meridian.duckdb"))
        return self._db

    @property
    def ledger(self) -> "LedgerBook":
        """决策台账门面（做账）。复用 db() 连接，避免 DuckDB 文件锁冲突。"""
        from meridian.ledger import LedgerBook

        if self._ledger is None:
            self._ledger = LedgerBook(self.db())
        return self._ledger

    def _resolve(self, symbol: str, name: str | None = None) -> tuple[MarketEntry, SymbolEntry]:
        # 标的池里有就用配置（带名称）；没有则按代码模式自动识别，不挡即兴分析
        return self.markets_cfg.find_or_auto(symbol, name=name)

    # ---- 主流程 ----
    def analyze(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        offline: bool = False,
        name: str | None = None,
    ) -> AnalysisResult:
        entry, sym = self._resolve(symbol, name=name)
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
                        live = self._source_for(entry).fetch_daily(
                            FetchRequest(symbol, start_date.isoformat(), end_date.isoformat()))
                        if not live.empty:
                            self._insert_bars(entry, sym, live)
                            df = self._read_cache(entry, sym, start_date, end_date)
                    except DataError as exc2:
                        fetch_error = fetch_error or exc2
        else:
            try:
                df = self._source_for(entry).fetch_daily(
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

        # 市场状态检测（trend_vol_v1）——优先市场指数，降级标的自身K线；失败降级 Unknown
        regime_info = self._detect_regime(entry, df, start_date, end_date, offline)
        regime_str = regime_info["regime"] if regime_info else "Unknown"

        score = self.engine(entry.asset_type).evaluate(
            symbol=sym.symbol, name=sym.name, market=entry.market,
            asset_type=entry.asset_type, frequency=entry.frequency,
            dates=[d.isoformat() for d in df["date"]],
            opens=df["open"].tolist(), highs=df["high"].tolist(),
            lows=df["low"].tolist(), closes=df["close"].tolist(),
            volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
            regime=regime_str,
        )

        result = AnalysisResult(
            symbol=sym.symbol,
            name=sym.name,
            market=entry.market,
            asset_type=entry.asset_type,
            frequency=entry.frequency,
            regime=regime_str,
            bar_count=len(df),
            start=str(df["date"].iloc[0]),
            end=str(df["date"].iloc[-1]),
            score=score,
            data_source=data_source,
            fallback_reason=fallback_reason,
            df=df,
            regime_confidence=float(regime_info["confidence"]) if regime_info else 0.0,
            regime_basis=list(regime_info["basis"]) if regime_info else [],
            regime_detector=str(regime_info["detector"]) if regime_info else "",
            fundamentals=self._load_fundamentals(entry, sym, offline),
        )
        try:
            from meridian.forecast_view import short_term_outlook

            result.outlook = short_term_outlook(df)
        except Exception as exc:  # noqa: BLE001 —— 预判属增强信息，失败不阻断
            warnings.warn(f"短期预判计算失败（报告无该节）: {exc}")
        try:
            from meridian.patterns import detect_patterns

            result.patterns = detect_patterns(df)
        except Exception as exc:  # noqa: BLE001 —— 形态标注属增强信息
            warnings.warn(f"K线形态识别失败（图无形态标注）: {exc}")

        if self.persist:
            self._record_ledger(result)
            self._record_regime(result)
        return result

    def _index_source(self, market: str) -> "DataSource | None":
        """市场 → 指数日K源（config/regime.yaml index_input；未配置的市场返回 None）。"""
        if market not in self._index_sources:
            index_symbol = RegimeConfig.load(self._root).index_input.get(market)
            if not index_symbol:
                self._index_sources[market] = None
            else:
                from meridian.data.index import TencentIndexSource

                self._index_sources[market] = TencentIndexSource()
        return self._index_sources[market]

    def _detect_regime(
        self, entry: MarketEntry, df: pd.DataFrame,
        start_date: date, end_date: date, offline: bool,
    ) -> dict | None:
        """市场状态检测（trend_vol_v1）。

        输入优先用市场指数K线（cn=沪深300，config/regime.yaml index_input）；
        指数未配置/拉取失败/离线模式时降级用标的自身K线（一期代理）。
        检测失败降级 None（报告显示未知），均不阻断分析。
        """
        bars_df = df
        index_source = None if offline else self._index_source(entry.market)
        if index_source is not None:
            index_symbol = RegimeConfig.load(self._root).index_input.get(entry.market, "")
            try:
                idx_df = index_source.fetch_daily(
                    FetchRequest(index_symbol, start_date.isoformat(), end_date.isoformat()))
                if len(idx_df) >= RegimeConfig.load(self._root).trend_ma_slow:
                    bars_df = idx_df
                else:
                    warnings.warn(f"指数 {index_symbol} 窗口不足（{len(idx_df)} 根），用标的自身K线检测")
            except DataError as exc:
                warnings.warn(f"指数 {index_symbol} 拉取失败，用标的自身K线检测 regime: {exc}")

        if self._regime_detector is None:
            from meridian import meridian_core as mc

            cfg = RegimeConfig.load(self._root)
            self._regime_detector = mc.PyRegimeDetector(
                trend_ma_fast=cfg.trend_ma_fast,
                trend_ma_slow=cfg.trend_ma_slow,
                trend_band=cfg.trend_band,
                drawdown_window=cfg.drawdown_window,
                crisis_drawdown=cfg.crisis_drawdown,
                atr_period=cfg.atr_period,
                atr_pct_crisis=cfg.atr_pct_crisis,
                atr_pct_high_vol=cfg.atr_pct_high_vol,
            )
        try:
            return self._regime_detector.detect(
                dates=[d.isoformat() if hasattr(d, "isoformat") else str(d) for d in bars_df["date"]],
                opens=[float(v) for v in bars_df["open"]],
                highs=[float(v) for v in bars_df["high"]],
                lows=[float(v) for v in bars_df["low"]],
                closes=[float(v) for v in bars_df["close"]],
                volumes=[float(v) for v in bars_df["volume"]],
                amounts=[float(v) for v in bars_df["amount"]],
            )
        except Exception as exc:  # noqa: BLE001 —— regime 属增强信息，失败不阻断分析
            warnings.warn(f"市场状态检测失败（降级为 Unknown）: {exc}")
            return None

    def _record_regime(self, result: AnalysisResult) -> None:
        """regime_history 留痕（append-only）。写失败只告警不阻断（审计旁路，同台账语义）。"""
        try:
            self.db().insert_regime_history(
                ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol=result.symbol,
                name=result.name,
                market=result.market,
                asset_type=result.asset_type,
                frequency=result.frequency,
                regime=result.regime,
                confidence=result.regime_confidence,
                basis=list(result.regime_basis),
                detector=result.regime_detector,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"regime_history 写入失败（本次检测未留痕）: {exc}")

    def analyze_universe(
        self,
        start: str | None = None,
        end: str | None = None,
        offline: bool = False,
        market: str | None = None,
    ) -> tuple[list[AnalysisResult], list[tuple[str, str, str]]]:
        """批量分析标的池（market=None 全部市场）。

        单标的失败（无数据/数据不足等）记入 failures 不挡其余——批量场景下
        个别标的缺数据是常态。返回 (results, failures)，failures 元素为
        (symbol, name, 错误信息)。
        """
        results: list[AnalysisResult] = []
        failures: list[tuple[str, str, str]] = []
        for entry in self.markets_cfg.markets:
            if market and entry.market != market:
                continue
            for sym in entry.symbols:
                try:
                    results.append(
                        self.analyze(sym.symbol, start=start, end=end, offline=offline, name=sym.name)
                    )
                except Exception as exc:  # noqa: BLE001 —— 批量场景单标的失败不挡其余
                    failures.append((sym.symbol, sym.name, str(exc)))
        return results, failures

    def _load_fundamentals(self, entry: MarketEntry, sym: SymbolEntry, offline: bool) -> dict | None:
        """基本面估值速览（v1 仅 cn 股）：在线拉最新 → UPSERT 落库；离线/失败读库。

        基本面属增强信息：拉取失败告警降级，不阻断分析。
        测试可替换 self._fundamental_source 避免网络。
        """
        if entry.asset_type != "stock" or entry.market != "cn":
            return None
        try:
            if not offline:
                if self._fundamental_source is None:
                    from meridian.data.fundamentals import FundamentalSource

                    self._fundamental_source = FundamentalSource(DataSourceConfig.load(self._root))
                snap = self._fundamental_source.fetch_latest(sym.symbol)
                self.db().insert_fundamental(
                    symbol=sym.symbol, name=sym.name, market=entry.market,
                    asset_type=entry.asset_type, frequency=entry.frequency,
                    date=snap["date"], pe_ttm=snap.get("pe_ttm"), pb=snap.get("pb"),
                    ps_ttm=snap.get("ps_ttm"), dv_ratio=snap.get("dv_ratio"),
                    total_mv=snap.get("total_mv"), source=snap.get("source", ""),
                )
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"基本面拉取失败（降级读本地库）: {exc}")
        try:
            row = self.db().latest_fundamental(entry.market, sym.symbol)
            return dict(row) if row else None
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"基本面读取失败（报告无基本面节）: {exc}")
            return None

    def _record_ledger(self, result: AnalysisResult) -> None:
        """决策台账留痕（做账）：每次成功分析 append-only 追加一条系统建议。

        写失败只告警不阻断 —— 分析本身已成功；但该次分析在台账中缺席，
        导出做账文档时即可见（审计上"缺行"比"错行"更容易被发现）。
        --no-persist 时不落任何库，自然也不留痕。
        """
        try:
            self.ledger.record_analysis(result)
        except Exception as exc:  # noqa: BLE001 —— 台账属审计旁路，不因存储问题打断分析
            warnings.warn(f"决策台账写入失败（本次分析未留痕）: {exc}")

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

        return DailySyncer(self._source_for(entry), self.db()).sync(
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
        chart_rel = self._render_chart(result, out_dir)
        path.write_text(result.to_markdown(chart_image=chart_rel), encoding="utf-8")
        return path

    def _render_chart(self, result: AnalysisResult, out_dir: Path) -> str | None:
        """K线配图 → reports/charts/<symbol>_<date>.png，返回相对报告目录的引用。

        画图失败只告警降级为无图报告（图是增强项，不该挡分析产物落盘）。
        """
        if result.df is None or result.df.empty:
            return None
        try:
            from meridian.orchestrator.chart import plot_daily_chart

            out = out_dir / "charts" / f"{result.symbol}_{date.today().isoformat()}.png"
            plot_daily_chart(result.df, result.symbol, result.name, out)
            return f"charts/{out.name}"
        except Exception as exc:  # noqa: BLE001 —— 配图属增强项，失败不阻断报告
            warnings.warn(f"K线图生成失败（报告降级为无图）: {exc}")
            return None
