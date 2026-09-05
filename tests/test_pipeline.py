"""桥接与端到端测试（离线）：哑模型注册 → 三层评分 → Markdown 报告。"""

from __future__ import annotations

import re

import pytest

from conftest import ROOT, CsvSource, DummyModel
from meridian import meridian_core as mc
from meridian.data.base import BAR_COLUMNS, DataError, DataSource
from meridian.orchestrator.pipeline import AnalysisPipeline


class BrokenSource(DataSource):
    """模拟数据源网络故障。"""

    name = "broken"

    def fetch_daily(self, request):
        raise DataError("模拟网络故障：连接被远端关闭")


def make_pipeline(tmp_path, extra_models=(), persist=False) -> AnalysisPipeline:
    df_frame = _frame()
    source = CsvSource(df_frame, tmp_path / "bars.csv")
    return AnalysisPipeline(root=ROOT, source=source, extra_models=extra_models, persist=persist)


def _frame():
    from conftest import make_uptrend_frame

    return make_uptrend_frame(130)


def test_engine_builtin_models_with_real_config():
    """真实 scoring yaml + 4 个内置规则模型。"""
    engine = mc.PyEngine(str(ROOT / "config" / "scoring" / "stock.yaml"))
    engine.add_builtin_models()
    assert engine.registered_models() == [
        "trend_model",
        "momentum_model",
        "capital_model",
        "risk_model",
    ]
    assert len(engine.config_fingerprint()) == 16


def test_dummy_model_bridge_contributes(tmp_path):
    """验收标准 4：哑模型（score=55）注册后贡献可见。"""
    engine = mc.PyEngine(str(ROOT / "config" / "scoring" / "stock.yaml"))
    engine.add_builtin_models()
    engine.add_python_model("py_dummy_v1", DummyModel(), "opportunity")

    from conftest import make_uptrend_frame

    df = make_uptrend_frame(130)
    kwargs = dict(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock", frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(), lows=df["low"].tolist(),
        closes=df["close"].tolist(), volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
    )
    with_dummy = engine.evaluate(**kwargs)

    engine2 = mc.PyEngine(str(ROOT / "config" / "scoring" / "stock.yaml"))
    engine2.add_builtin_models()
    baseline = engine2.evaluate(**kwargs)

    # 哑模型拉低强势序列的综合分 → 贡献真实存在
    assert with_dummy["opportunity"]["score"] < baseline["opportunity"]["score"]
    dummy = [f for f in with_dummy["opportunity"]["factors"] if f["name"] == "py_dummy_v1"]
    assert dummy and dummy[0]["contribution"] > 0
    assert "unknown_model_weight" in dummy[0]["description"]


def test_pipeline_end_to_end_offline(tmp_path):
    """CSV 离线数据 → 管线 → 三层评分 + Markdown 报告。"""
    pipeline = make_pipeline(tmp_path, extra_models=[("py_dummy_v1", DummyModel(), "opportunity")])
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    assert result.symbol == "600519"
    assert result.opportunity > 50.0  # 强上涨序列
    assert 0.0 <= result.risk <= 100.0
    assert result.action in {"Add", "Hold", "Reduce", "Watch", "Avoid"}
    assert len(result.score["config_fingerprint"]) == 16

    report = result.to_markdown()
    assert "贵州茅台" in report and "600519" in report
    assert "机会 Opportunity" in report and "风险 Risk" in report
    assert result.action in report
    assert "py_dummy_v1" in report  # 哑模型因子可追溯
    assert "## 结论" in report  # 结论先行：规则模板摘要段
    assert "触发原因" in report  # 触发明细表

    path = pipeline.write_report(result)
    assert path.exists() and path.read_text(encoding="utf-8").startswith("# Meridian")
    assert result.df is not None and len(result.df) == result.bar_count


def test_write_report_embeds_kline_chart(tmp_path):
    """write_report 生成 K线配图并在 markdown 里引用（相对路径 charts/）。"""
    pipeline = make_pipeline(tmp_path)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    path = pipeline.write_report(result)
    text = path.read_text(encoding="utf-8")

    assert re.search(r"!\[.*\]\(charts/600519_\d{4}-\d{2}-\d{2}\.png\)", text), "报告应引用K线图"
    chart = path.parent / "charts" / text.split("](charts/")[1].split(")")[0]
    assert chart.exists() and chart.stat().st_size > 10_000, "PNG 应真实落盘且非空"

    # df 未挂载时降级为无图，不报错
    result.df = None
    path2 = pipeline.write_report(result)
    assert "charts/" not in path2.read_text(encoding="utf-8")


def test_pipeline_adhoc_symbol_out_of_universe(tmp_path):
    """标的池外的代码不再拒绝：自动识别市场后走正常管线（数据源注入，离线可测）。"""
    pipeline = make_pipeline(tmp_path)  # CsvSource 固定返回 600519 合成数据
    # 002475 不在 conftest 的 markets.yaml 里也无所谓——自动识别为 cn/stock
    result = pipeline.analyze("000001", start="2026-01-05", end="2026-12-31")
    assert result.symbol == "000001"
    assert result.market == "cn" and result.asset_type == "stock"


# ---- Phase 1：市场状态检测（regime）----


def _pipeline_with_regime(tmp_path, persist=False) -> AnalysisPipeline:
    """同 test_ledger 模式：CSV 合成源 + 预灌内存库（离线分析走 cache 路径，
    不碰真实 data/ 库文件，避免与其他进程的 DuckDB 文件锁冲突）。"""
    from conftest import make_uptrend_frame

    pipeline = AnalysisPipeline(
        root=ROOT, source=CsvSource(make_uptrend_frame(130), tmp_path / "bars.csv"),
        persist=persist,
    )
    db = mc.PyDb.open_in_memory()
    df = make_uptrend_frame(130)
    db.insert_bars(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock", frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(), lows=df["low"].tolist(),
        closes=df["close"].tolist(), volumes=df["volume"].tolist(),
        amounts=df["amount"].tolist(),
    )
    pipeline._db = db
    return pipeline


def test_regime_detected_on_uptrend(tmp_path):
    """强上涨合成序列 → Bull + 置信度 + 判定依据（阈值来自 config/regime.yaml）。"""
    pipeline = _pipeline_with_regime(tmp_path)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)

    assert result.regime == "Bull"
    assert result.regime_confidence > 0.6
    assert result.regime_basis, "判定依据应非空"
    assert result.regime_detector == "trend_vol_v1"


def test_regime_history_recorded_when_persist(tmp_path):
    """persist=True 时 regime 快照 append-only 落库，可读回且与结果一致。"""
    pipeline = _pipeline_with_regime(tmp_path, persist=True)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)

    row = pipeline.db().latest_regime_history("cn", "600519")
    assert row is not None
    assert row["regime"] == result.regime == "Bull"
    assert row["confidence"] == result.regime_confidence
    assert row["basis"] == result.regime_basis
    assert row["detector"] == "trend_vol_v1"
    assert pipeline.db().latest_regime_history("hk", "600519") is None  # 市场隔离


def test_report_shows_regime_with_basis(tmp_path):
    """报告 meta 显示 regime 中文名 + 置信度，依据以引用行呈现。"""
    pipeline = _pipeline_with_regime(tmp_path)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)

    report = result.to_markdown()
    assert "市场状态（regime）：上行" in report
    assert "置信度" in report
    assert "状态判定依据（trend_vol_v1）" in report
    assert any(line in report for line in result.regime_basis)  # 依据原文可见


def test_regime_config_missing_file_falls_back(tmp_path):
    """config/regime.yaml 缺失 → 全默认（增强配置不挡管线）。"""
    from meridian.config import RegimeConfig

    cfg = RegimeConfig.load(tmp_path)
    assert cfg == RegimeConfig()
    assert cfg.trend_ma_slow == 60 and cfg.crisis_drawdown == 0.10


# ---- Phase 1：regime 指数输入（优先市场指数，降级标的自身K线）----


class _FakeIndexSource(DataSource):
    name = "fake_index"

    def __init__(self, df=None, error=None):
        self.df = df
        self.error = error
        self.calls = 0

    def fetch_daily(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.df


def _declining_frame(days=130, start=100.0, step=-0.5):
    """持续下跌日K（与 make_uptrend_frame 反向，供假指数源用）。"""
    import pandas as pd

    dates = pd.date_range("2026-01-05", periods=days, freq="D")
    closes = [start + step * i for i in range(days)]
    rows, prev = [], start - step
    for d, c in zip(dates, closes):
        rows.append({"date": d.date(), "open": prev,
                     "high": max(prev, c) + 0.3, "low": min(prev, c) - 0.3,
                     "close": c, "volume": 1e6, "amount": float("nan")})
        prev = c
    return pd.DataFrame(rows)[BAR_COLUMNS]


def test_regime_prefers_index_input(tmp_path):
    """配置了指数输入的市场：用指数K线检测（指数跌 → Bear），即使标的本身是涨势。"""
    pipeline = _pipeline_with_regime(tmp_path)
    fake = _FakeIndexSource(_declining_frame(130))
    pipeline._index_sources["cn"] = fake

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    assert fake.calls == 1, "应拉取指数日K"
    assert result.regime == "Bear", "指数状态应覆盖标的自身形态"


def test_regime_falls_back_when_index_fails(tmp_path):
    """指数拉取失败 → 告警并降级用标的自身K线，分析不中断。"""
    from meridian.data.base import DataError

    pipeline = _pipeline_with_regime(tmp_path)
    fake = _FakeIndexSource(error=DataError("模拟指数断连"))
    pipeline._index_sources["cn"] = fake

    with pytest.warns(UserWarning, match="指数"):
        result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    assert fake.calls == 1
    assert result.regime == "Bull", "降级后应使用标的自身K线（uptrend → Bull）"


def test_regime_offline_never_fetches_index(tmp_path):
    """离线模式不发起任何网络请求（含指数拉取），regime 用标的自身K线。"""
    pipeline = _pipeline_with_regime(tmp_path)
    fake = _FakeIndexSource(_declining_frame(130))
    pipeline._index_sources["cn"] = fake

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)

    assert fake.calls == 0
    assert result.regime == "Bull"


def test_detect_market_patterns():
    """代码模式 → (market, asset_type)。"""
    from meridian.config import _detect_market

    assert _detect_market("600519") == ("cn", "stock")
    assert _detect_market("002475") == ("cn", "stock")
    assert _detect_market("300750") == ("cn", "stock")
    assert _detect_market("00700") == ("hk", "stock")
    assert _detect_market("AAPL") == ("us", "stock")
    assert _detect_market("RB0") == ("cn", "futures")
    assert _detect_market("IF0") == ("cn", "futures")


def test_find_or_auto_prefers_config(tmp_path):
    """标的池内的代码仍用配置（名称/市场以配置为准）。"""
    from meridian.config import MarketsConfig

    cfg = MarketsConfig.load(ROOT)
    entry, sym = cfg.find_or_auto("600519")
    assert sym.name == "贵州茅台" and entry.market == "cn"
    # 池外（601318 不在标的池）：自动识别 + 可带名称
    entry2, sym2 = cfg.find_or_auto("601318", name="中国平安")
    assert sym2.name == "中国平安" and entry2.market == "cn" and entry2.asset_type == "stock"
    entry3, sym3 = cfg.find_or_auto("601318")
    assert sym3.name == "601318"  # 未提供名称时用代码占位


# ---- 缓存回退 / 离线模式 ----

def _prefill_cache(pipeline: AnalysisPipeline) -> None:
    """把合成K线预灌进内存库，模拟"此前拉取成功已入库"。"""
    df = _frame()
    db = mc.PyDb.open_in_memory()
    db.insert_bars(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock", frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(), lows=df["low"].tolist(),
        closes=df["close"].tolist(), volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
    )
    pipeline._db = db


def test_pipeline_falls_back_to_cache_on_source_failure(tmp_path):
    """数据源拉数失败 → 自动回退本地 DuckDB，报告标注数据来源。"""
    pipeline = make_pipeline(tmp_path)
    _prefill_cache(pipeline)
    pipeline.source = BrokenSource()

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    assert result.data_source == "cache"
    assert result.fallback_reason and "数据源拉取失败" in result.fallback_reason
    assert result.bar_count == len(_frame())

    report = result.to_markdown()
    assert "本地缓存" in report and "数据来源" in report
    # 缓存数据同样走完整评分管线（可追溯指纹不缺席）
    assert len(result.score["config_fingerprint"]) == 16


def test_pipeline_offline_mode_reads_cache_directly(tmp_path):
    """--offline：不触碰数据源，直接读本地库。"""
    pipeline = make_pipeline(tmp_path)
    _prefill_cache(pipeline)

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)

    assert result.data_source == "cache"
    assert result.opportunity > 50.0
    assert "离线模式" in result.to_markdown()


def test_pipeline_raises_when_source_and_cache_both_empty(tmp_path):
    """数据源失败且本地无缓存 → 合并两者信息的明确报错。"""
    pipeline = make_pipeline(tmp_path)
    _prefill_cache(pipeline)
    pipeline.source = BrokenSource()
    # 清空缓存库：换一个空内存库
    pipeline._db = mc.PyDb.open_in_memory()

    with pytest.raises(DataError) as exc_info:
        pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")
    msg = str(exc_info.value)
    assert "有效K线不足" in msg and "模拟网络故障" in msg


def test_cache_dates_normalized_like_live(tmp_path):
    """缓存路径的日期与 live 路径同构（date 对象、升序、列序一致）。"""
    from datetime import date

    pipeline = make_pipeline(tmp_path)
    _prefill_cache(pipeline)

    entry, sym = pipeline._resolve("600519")
    cache_df = pipeline._read_cache(entry, sym, date(2026, 1, 5), date(2026, 12, 31))
    assert list(cache_df.columns) == BAR_COLUMNS
    assert str(cache_df["date"].iloc[0]) == "2026-01-05"
    assert cache_df["date"].is_monotonic_increasing


def test_report_renders_trigger_details(tmp_path):
    """触发明细全链路透传："为什么"从 Rust 规则模型到 dict 再到报告。"""
    pipeline = make_pipeline(tmp_path)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    risk_details = result.score["risk"]["factors"][0]["details"]
    assert risk_details, "risk_model 内部触发明细必须透传到 Python dict"
    assert all("name" in d and "value" in d and "description" in d for d in risk_details)

    report = result.to_markdown()
    assert "触发原因" in report  # 渲染为独立明细表
    assert any(d["name"] == "ATR占比" for d in risk_details)


def test_source_routing_by_market(tmp_path):
    """管线按 (market, asset_type) 路由组合源：cn→A股链，hk/us→腾讯全球，futures→akshare期货。"""
    pipeline = AnalysisPipeline(root=ROOT, persist=False)  # 不传 source → 走路由

    assert pipeline._source_for(pipeline._resolve("600519")[0])._sources[0].name == "akshare"
    assert pipeline._source_for(pipeline._resolve("00700")[0])._sources[0].name == "tencent_global"
    assert pipeline._source_for(pipeline._resolve("AAPL")[0])._sources[0].name == "tencent_global"
    assert pipeline._source_for(pipeline._resolve("RB0")[0])._sources[0].name == "akshare_futures"

    # 显式源覆盖路由（测试注入数据源的既有语义）
    pipeline.source = BrokenSource()
    assert pipeline._source_for(pipeline._resolve("00700")[0]) is pipeline.source
