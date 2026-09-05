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


# ---- 批量分析（analyze-all）----


def test_analyze_universe_skips_failures(tmp_path):
    """批量分析：池内有数据的标的成功，无数据的记入 failures 不挡其余。"""
    pipeline = _pipeline_with_regime(tmp_path)  # 内存库只灌了 600519

    results, failures = pipeline.analyze_universe(offline=True)

    assert [r.symbol for r in results] == ["600519"]
    assert failures, "池内其他标的应因无数据失败"
    assert all(len(f) == 3 for f in failures)


def test_render_summary_table(tmp_path):
    """汇总表渲染：每标的一行 + 失败清单。"""
    from meridian.cli import render_summary

    pipeline = _pipeline_with_regime(tmp_path)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)

    text = render_summary([result], [("00700", "腾讯控股", "无数据")])
    assert f"| {result.symbol} | {result.name} |" in text
    assert "下行" in text or "上行" in text or "震荡" in text  # regime 中文
    assert "- 00700 腾讯控股：无数据" in text


# ---- 回测（Phase 2：ScoreBased 策略 + T+1 撮合）----


def test_pybacktester_simulate_known_series():
    """已知序列：满仓买入持有 → 绩效与手算一致（成本侵蚀后仍为正）。"""
    from meridian import meridian_core as mc

    n = 10
    dates = [f"2026-01-{i + 1:02d}" for i in range(n)]
    opens = [100.0 + i for i in range(n)]
    closes = [101.0 + i for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [o - 1 for o in opens]
    weights = [1.0] + [float("nan")] * (n - 1)  # 首日收盘满仓，其余维持

    r = mc.PyBacktester().simulate(
        dates, opens, highs, lows, closes,
        [1e6] * n, [float("nan")] * n, weights,
        initial_cash=1_000_000.0,
    )
    assert r["trade_count"] == 1
    assert r["total_return"] > 0.0
    assert r["trades"][0]["date_in"] == "2026-01-02", "T+1 开盘入场"
    assert len(r["equity_curve"]) == n
    assert r["profit_loss_ratio"] < 0, "无亏损交易 → Rust 侧用 -1 表示 ∞"


def test_score_based_backtester_end_to_end(tmp_path):
    """逐日评分回测集成：actions 序列完整、绩效字段齐全、净值闭环。"""
    from meridian.backtest import ScoreBasedBacktester

    pipeline = _pipeline_with_regime(tmp_path)
    bt = ScoreBasedBacktester(pipeline)
    out = bt.run("600519", start="2026-01-05", end="2026-12-31", offline=True)

    n = len(out["dates"])
    assert n == 130
    assert len(out["actions"]) == n and len(out["target_weights"]) == n
    assert all(a in {"Add", "Hold", "Reduce", "Watch", "Avoid", None} for a in out["actions"])
    assert out["actions"][: bt.settings.min_history_bars] == [None] * bt.settings.min_history_bars, \
        "指标窗口不足日无信号"
    assert out["actions"][bt.settings.min_history_bars] is not None
    for key in ("total_return", "annual_return", "max_drawdown", "sharpe",
                "win_rate", "trade_count", "final_equity", "equity_curve", "trades"):
        assert key in out, f"绩效字段 {key} 缺失"
    assert out["equity_curve"][-1][1] > 0


def test_render_backtest_report(tmp_path):
    """回测报告渲染：绩效表 + 交易明细 + 净值图引用。"""
    from meridian.backtest import ScoreBasedBacktester
    from meridian.cli import render_backtest_report

    pipeline = _pipeline_with_regime(tmp_path)
    out = ScoreBasedBacktester(pipeline).run(
        "600519", start="2026-01-05", end="2026-12-31", offline=True
    )

    text = render_backtest_report(out)
    assert "总收益率" in text and "最大回撤" in text and "夏普" in text
    assert f"charts/backtest_600519_{out['dates'][-1]}.png" in text


# ---- LLM Summary Agent（AI 转译摘要；不碰分数/action——红线 1/2）----


def test_summary_agent_disabled_without_env():
    """环境变量未配置 → 未启用，summarize 恒 None（不发网络请求）。"""
    from meridian.summary_agent import SummaryAgent

    agent = SummaryAgent.from_env()
    assert not agent.enabled
    assert agent.summarize("任意报告") is None


def test_summary_agent_failure_degrades(monkeypatch):
    """LLM 调用失败 → 返回 None，不抛异常（摘要属增强项）。"""
    import pytest

    from meridian import summary_agent as sa

    def boom(cfg, system, user):
        raise RuntimeError("模拟 LLM 断连")

    monkeypatch.setattr(sa, "chat_completion", boom)
    cfg = sa.LlmConfig("https://example.invalid", "k", "m")
    assert sa.SummaryAgent(cfg).summarize("报告") is None


def test_ai_summary_rendered_with_disclaimer(tmp_path):
    """带 ai_summary 的报告：摘要在结论后、评分前，且带免责引用。"""
    pipeline = _pipeline_with_regime(tmp_path)
    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)
    result.ai_summary = "（AI 转译）规则建议 Watch，主要因大盘下行、个股回撤深。"

    report = result.to_markdown()

    assert "## AI 摘要" in report and "（AI 转译）" in report
    assert report.index("## 结论") < report.index("## AI 摘要") < report.index("## 三层评分")
    assert "不产生新的评分或建议" in report

    # 无摘要时不渲染该节
    result.ai_summary = None
    assert "## AI 摘要" not in result.to_markdown()


# ---- 组合分析（Phase 3：集中度/相关性/风险暴露/规则仓位）----


def _pipeline_with_two_bars(tmp_path):
    """内存库灌一涨一跌两个标的（日收益率严格互为相反数），供组合分析离线使用。"""
    from conftest import make_uptrend_frame

    pipeline = _pipeline_with_regime(tmp_path)
    db = pipeline._db

    up = make_uptrend_frame(130)
    closes = up["close"].tolist()
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    inv = [100.0]
    for r in rets:
        inv.append(inv[-1] * (1 - r))  # 收益率取反 → 与 600519 严格负相关

    dates = [str(d) for d in up["date"]]
    db.insert_bars(
        symbol="300750", name="宁德时代", market="cn", asset_type="stock", frequency="daily",
        dates=dates,
        opens=[c + 0.5 for c in inv], highs=[c + 1.0 for c in inv],
        lows=[c - 1.0 for c in inv], closes=inv,
        volumes=[5e5] * len(inv), amounts=[float("nan")] * len(inv),
    )
    return pipeline


def test_portfolio_analyze_concentration_and_correlation(tmp_path):
    """等权两只（一涨一跌）：HHI=0.5、相关性强负、风险暴露=均值、字段齐全。"""
    from meridian.portfolio import PortfolioAnalyzer

    pipeline = _pipeline_with_two_bars(tmp_path)
    out = PortfolioAnalyzer(pipeline).analyze(["600519", "300750"])

    assert len(out["rows"]) == 2
    assert abs(out["concentration_hhi"] - 0.5) < 1e-9, "等权两只 HHI 应为 0.5"
    assert out["effective_holdings"] == 2.0
    corr = out["correlation"]
    assert abs(corr.loc["600519", "600519"] - 1.0) < 1e-9
    assert corr.loc["600519", "300750"] < -0.9, "一涨一跌镜像序列应强负相关"
    assert 0 <= out["risk_exposure"] <= 100
    assert 0 <= out["position_suggestion"] <= 1.0


def test_portfolio_explicit_weights_from_config(tmp_path, monkeypatch):
    """config/portfolio.yaml holdings → 显式权重参与 HHI 与仓位建议。"""
    from meridian import portfolio as pf_mod
    from meridian.portfolio import PortfolioAnalyzer

    pipeline = _pipeline_with_two_bars(tmp_path)
    monkeypatch.setattr(pf_mod, "load_portfolio_weights", lambda root: {"600519": 3.0, "300750": 1.0})

    out = PortfolioAnalyzer(pipeline).analyze(["600519", "300750"])

    w = {r.symbol: r.weight for r in out["rows"]}
    assert abs(w["600519"] - 0.75) < 1e-9 and abs(w["300750"] - 0.25) < 1e-9
    assert abs(out["concentration_hhi"] - (0.75**2 + 0.25**2)) < 1e-9


def test_render_portfolio_report(tmp_path):
    """组合报告渲染：持仓表 + 相关性矩阵 + 高相关对提示。"""
    from meridian.cli import render_portfolio_report
    from meridian.portfolio import PortfolioAnalyzer

    pipeline = _pipeline_with_two_bars(tmp_path)
    out = PortfolioAnalyzer(pipeline).analyze(["600519", "300750"])

    text = render_portfolio_report(out)
    assert "| 600519 |" in text and "| 300750 |" in text
    assert "集中度 HHI" in text and "规则仓位建议" in text
    # 镜像序列强负相关（≈-1），不应触发 >0.7 高相关提示
    assert "同涨同跌风险大" not in text


def test_daily_command_invokes_all_steps(monkeypatch):
    """daily 一条龙：依次调用 analyze-all / portfolio / ledger 三步并取最大退出码。"""
    from meridian import cli as cli_mod

    calls = []

    def fake(name, rc):
        def _cmd(_args):
            calls.append(name)
            return rc
        return _cmd

    monkeypatch.setattr(cli_mod, "cmd_analyze_all", fake("all", 0))
    monkeypatch.setattr(cli_mod, "cmd_portfolio", fake("pf", 0))
    monkeypatch.setattr(cli_mod, "cmd_ledger", fake("led", 0))

    rc = cli_mod.main(["daily"])
    assert calls == ["all", "pf", "led"] and rc == 0

    calls.clear()
    monkeypatch.setattr(cli_mod, "cmd_analyze_all", fake("all", 1))
    assert cli_mod.main(["daily"]) == 1, "任一步失败 → 退出码取最大"


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
