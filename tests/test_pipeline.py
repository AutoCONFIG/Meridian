"""桥接与端到端测试（离线）：哑模型注册 → 三层评分 → Markdown 报告。"""

from __future__ import annotations

import pytest

from conftest import ROOT, CsvSource, DummyModel
from meridian import meridian_core as mc
from meridian.orchestrator.pipeline import AnalysisPipeline


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
