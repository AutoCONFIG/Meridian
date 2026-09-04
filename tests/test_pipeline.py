"""桥接与端到端测试（离线）：哑模型注册 → 三层评分 → Markdown 报告。"""

from __future__ import annotations

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

    path = pipeline.write_report(result)
    assert path.exists() and path.read_text(encoding="utf-8").startswith("# Meridian")


def test_pipeline_rejects_symbol_out_of_universe(tmp_path):
    pipeline = make_pipeline(tmp_path)
    from meridian.config import ConfigError

    with pytest.raises(ConfigError):
        pipeline.analyze("999999")


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
