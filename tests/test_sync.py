"""增量落库同步测试：游标推进 / 重叠 UPSERT / NaN 成交额 / 管线 store 路径。

PyDb 一律用内存库（不触盘），数据源为离线假源，不发网络请求。
"""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import ROOT, CsvSource, make_uptrend_frame
from meridian import meridian_core as mc
from meridian.data.base import BAR_COLUMNS, DataError, DataSource, FetchRequest
from meridian.data.sync import DailySyncer, SyncReport
from meridian.orchestrator.pipeline import AnalysisPipeline


class FixedSource(DataSource):
    """固定帧按请求区间过滤返回（模拟真实源的区间裁剪）。"""

    name = "fixed"

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        mask = (self.df["date"] >= pd.Timestamp(request.start).date()) & (
            self.df["date"] <= pd.Timestamp(request.end).date()
        )
        return self.df.loc[mask].reset_index(drop=True)


class BrokenSource(DataSource):
    """模拟数据源网络故障。"""

    name = "broken"

    def fetch_daily(self, request):
        raise DataError("模拟网络故障：连接被远端关闭")


def _bars(n: int = 5, *, start: str = "2026-09-01", amount=None) -> pd.DataFrame:
    """n 根合法合成日K；amount 可传 lambda(i) 定制（如 NaN）。"""
    if amount is None:
        amount = lambda i: 1000.0 + i  # noqa: E731
    rows = []
    for i in range(n):
        rows.append({
            "date": pd.Timestamp(start).date() + pd.Timedelta(days=i),
            "open": 10.0 + i, "high": 11.0 + i, "low": 9.5 + i,
            "close": 10.5 + i, "volume": 100.0 + i, "amount": amount(i),
        })
    return pd.DataFrame(rows)[BAR_COLUMNS]


def _syncer(source: DataSource, db) -> DailySyncer:
    return DailySyncer(source, db, history_days=800, overlap_days=5)


# ---------------- 游标与模式 ----------------


def test_full_then_incremental_then_none():
    """无游标 → 全量；游标已最新 → 跳过；end 推进 → 增量且无重复行。"""
    db = mc.PyDb.open_in_memory()
    syncer = _syncer(FixedSource(_bars(5)), db)

    rep = syncer.sync(symbol="600519", name="贵州茅台", market="cn", end="2026-09-05")
    assert rep.mode == "full" and rep.fetched == 5 and rep.stored == 5
    assert db.bar_count("600519", "cn", "daily") == 5

    rep2 = syncer.sync(symbol="600519", name="贵州茅台", market="cn", end="2026-09-05")
    assert rep2.mode == "none" and rep2.fetched == 0

    rep3 = syncer.sync(symbol="600519", name="贵州茅台", market="cn", end="2026-09-10")
    assert rep3.mode == "incremental" and rep3.fetched == 5
    assert db.bar_count("600519", "cn", "daily") == 5  # UPSERT 吸收重叠，无重复


def test_incremental_upsert_absorbs_revision():
    """重叠窗口重拉：尾部 bar 被覆盖（吸收盘后修正），行数不变。"""
    db = mc.PyDb.open_in_memory()
    _syncer(FixedSource(_bars(5)), db).sync(
        symbol="RB0", name="螺纹钢主力", market="cn", asset_type="futures",
        end="2026-09-05",
    )

    revised = _bars(5)
    revised.loc[revised.index[-1], ["close", "high"]] = [99.0, 99.5]
    rep = _syncer(FixedSource(revised), db).sync(
        symbol="RB0", name="螺纹钢主力", market="cn", asset_type="futures",
        end="2026-09-06",
    )
    assert rep.mode == "incremental"
    assert db.bar_count("RB0", "cn", "daily") == 5

    rows = db.read_bars(
        symbol="RB0", name="螺纹钢主力", market="cn",
        asset_type="futures", frequency="daily",
        start="2026-09-01", end="2026-09-06",
    )
    closes = {str(r["date"])[:10]: r["close"] for r in rows}
    assert closes["2026-09-05"] == 99.0  # 修正值已覆盖旧值


def test_isolated_by_market_and_frequency():
    """游标按 (market, symbol, frequency) 隔离，互不串位。"""
    db = mc.PyDb.open_in_memory()
    _syncer(FixedSource(_bars(3)), db).sync(
        symbol="600519", name="贵州茅台", market="cn", end="2026-09-03")
    # 同代码不同市场 → 视为无游标 → 全量
    rep = _syncer(FixedSource(_bars(3)), db).sync(
        symbol="600519", name="贵州茅台", market="hk", end="2026-09-03")
    assert rep.mode == "full"
    assert db.bar_count("600519", "hk", "daily") == 3
    assert db.bar_count("600519", "cn", "daily") == 3


# ---------------- 空数据语义 ----------------


def test_full_empty_raises_incremental_empty_ok():
    """全量空帧 → 明确报错；有游标后增量空帧 → 0 根报告（夜间常态）。"""
    db = mc.PyDb.open_in_memory()
    empty = pd.DataFrame(columns=BAR_COLUMNS)

    with pytest.raises(DataError, match="全量同步返回空数据"):
        _syncer(FixedSource(empty), db).sync(
            symbol="600519", name="贵州茅台", market="cn", end="2026-09-05")

    db.insert_bars(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock",
        frequency="daily",
        dates=["2026-09-05"], opens=[10.0], highs=[11.0], lows=[9.5],
        closes=[10.5], volumes=[100.0], amounts=[1000.0],
    )
    rep = _syncer(FixedSource(empty), db).sync(
        symbol="600519", name="贵州茅台", market="cn", end="2026-09-08")
    assert rep.mode == "incremental" and rep.fetched == 0 and rep.stored == 0


def test_nan_amount_stored():
    """期货无成交额 → NaN 入库不报错（Bar::validate 放行 NaN）。"""
    db = mc.PyDb.open_in_memory()
    rep = _syncer(FixedSource(_bars(3, amount=lambda i: float("nan"))), db).sync(
        symbol="RB0", name="螺纹钢主力", market="cn", asset_type="futures",
        end="2026-09-03")
    assert rep.stored == 3

    rows = db.read_bars(
        symbol="RB0", name="螺纹钢主力", market="cn", asset_type="futures",
        frequency="daily", start="2026-09-01", end="2026-09-03")
    assert all(pd.isna(r["amount"]) for r in rows)


def test_sync_report_brief():
    assert "已最新" in SyncReport(
        "600519", "cn", "none", "2026-09-05", "2026-09-05", 0, 0, "akshare").brief()
    b = SyncReport(
        "600519", "cn", "incremental", "2026-09-01", "2026-09-05", 3, 3, "akshare").brief()
    assert "增量" in b and "入库 3" in b and "akshare" in b


# ---------------- 管线 store 路径 ----------------


def _persist_pipeline(tmp_path) -> AnalysisPipeline:
    return AnalysisPipeline(
        root=ROOT,
        source=CsvSource(make_uptrend_frame(130), tmp_path / "bars.csv"),
        persist=True,
    )


def test_pipeline_store_path(tmp_path):
    """persist=True 端到端：增量同步入库 → 从本地库读 → data_source=store。"""
    pipeline = _persist_pipeline(tmp_path)
    pipeline._db = mc.PyDb.open_in_memory()

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    assert result.data_source == "store"
    assert result.fallback_reason is None
    assert result.bar_count == 130
    assert result.opportunity > 50.0  # 强上涨序列走完整评分
    assert pipeline._db.bar_count("600519", "cn", "daily") == 130

    report = result.to_markdown()
    assert "本地库（增量同步）" in report


def test_pipeline_store_fallback_on_source_failure(tmp_path):
    """同步失败（源挂）→ 回退本地库并明确标注，不静默。"""
    pipeline = _persist_pipeline(tmp_path)
    db = mc.PyDb.open_in_memory()
    df = make_uptrend_frame(130)
    db.insert_bars(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock",
        frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(),
        lows=df["low"].tolist(), closes=df["close"].tolist(),
        volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
    )
    pipeline._db = db
    pipeline.source = BrokenSource()

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")

    assert result.data_source == "cache"
    assert result.fallback_reason and "数据源拉取失败" in result.fallback_reason
    assert result.bar_count == 130


def test_pipeline_futures_asset_type_uses_futures_scoring(tmp_path):
    """RB0（futures）→ 离线读 futures.yaml 评分；股票权重不得静默套用。"""
    pipeline = _persist_pipeline(tmp_path)
    db = mc.PyDb.open_in_memory()
    df = make_uptrend_frame(130)
    db.insert_bars(
        symbol="RB0", name="螺纹钢主力", market="cn", asset_type="futures",
        frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(),
        lows=df["low"].tolist(), closes=df["close"].tolist(),
        volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
    )
    pipeline._db = db

    result = pipeline.analyze("RB0", start="2026-01-05", end="2026-12-31", offline=True)

    assert result.asset_type == "futures" and result.market == "cn"
    assert result.data_source == "cache"
    assert result.opportunity > 50.0
    assert len(result.score["config_fingerprint"]) == 16
    # futures 引擎独立缓存，股票引擎互不影响
    assert "futures" in pipeline._engines and "stock" not in pipeline._engines


def test_pipeline_syncs_gap_between_calls(tmp_path):
    """两次分析之间数据推进 → 第二次自动增量补缺口（不重复全量）。"""
    pipeline = _persist_pipeline(tmp_path)
    pipeline._db = mc.PyDb.open_in_memory()

    pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")
    count_after_first = pipeline._db.bar_count("600519", "cn", "daily")

    # 源数据尾部追加新 bar → 下次 analyze 增量同步补上
    extended = pd.concat(
        [make_uptrend_frame(130), make_uptrend_frame(140).iloc[130:]],
        ignore_index=True,
    )
    pipeline.source = CsvSource(extended, tmp_path / "bars2.csv")

    result = pipeline.analyze("600519", start="2026-01-05", end="2026-12-31")
    assert result.bar_count == count_after_first + 10
    assert result.data_source == "store"
