"""港/美股与期货适配层测试：报文解析 + 代码规则 + 源健康跟踪。

全部离线；快照 fixture 为 2026-09-03 实测报文节选（docs/DATA_SOURCES.md §6），
字段位用报文内部自洽性交叉验证（涨跌幅 = last/pre_close - 1、量额均价合理）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from meridian.data.base import BAR_COLUMNS, DataError, SourceHealth
from meridian.data.cn_stock_tencent import parse_fqkline
from meridian.data.futures import AkshareFuturesDailySource, _MAIN_RE
from meridian.data.global_stock import hk_us_market
from meridian.data.realtime import (
    SNAPSHOT_COLUMNS,
    MultiSourceSnapshot,
    _tx_ts,
    parse_sina_hk_quotes,
    parse_sina_us_quotes,
    parse_tencent_quotes,
)

# ---------------- 报文 fixture（实测节选） ----------------

SINA_HK = (
    'var hq_str_rt_hk00700="TENCENT,腾讯控股,444.200,438.200,445.600,433.000,'
    '433.000,-5.200,-1.187,433.000,433.200,7597753829.266,17387096,15.745,'
    '0.000,675.134,411.000,2026/09/03,16:08:22,100|0";'
)

SINA_US = (
    'var hq_str_gb_aapl="苹果,324.9600,-0.05,2026-09-03 17:23:59,-0.1700,'
    '326.8650,328.4000,323.5300,344.5700,225.1600,33776365,38165880,'
    '4742525294655,8.30,39.150000,0.00,0.00,0.00,0.00,14594181729,63,'
    '324.9160,-0.01,-0.04,Sep 03 05:23AM EDT,Sep 02 04:00PM EDT,325.1300,'
    '102634,1,2026";'
)


def _tencent_hk_body() -> str:
    f = ["0"] * 40
    f[1], f[2] = "腾讯控股", "00700"
    f[3], f[4], f[5] = "433.00", "438.20", "444.20"  # 最新/昨收/今开（与 A股同位）
    f[30] = "2026/09/03 16:08:23"
    f[33], f[34] = "445.60", "433.00"
    f[35] = "433.000"  # 港股无 A股"价/量/额"斜杠结构
    f[36], f[37] = "17387096.0", "7597753829.266"
    return "~".join(f)


TENCENT_HK = f'v_hk00700="{_tencent_hk_body()}";'


# ---------------- 新浪港/美快照解析 ----------------


def test_sina_hk_parse_field_positions():
    rows = parse_sina_hk_quotes(SINA_HK)
    r = rows["00700"]
    assert r["name"] == "腾讯控股"
    assert r["last"] == 433.0 and r["pre_close"] == 438.2
    assert r["open"] == 444.2 and r["high"] == 445.6 and r["low"] == 433.0
    assert r["volume"] == 17387096.0      # 股（f12，与腾讯 f36 一致）
    assert r["amount"] == 7597753829.266  # 港元（f11，量×均价≈额 才合理）
    assert r["ts"] == "2026-09-03 16:08:22"
    # 自洽: 涨跌幅字段 = last/pre_close - 1
    assert abs(r["last"] / r["pre_close"] - 1 - (-1.187 / 100)) < 0.0005


def test_sina_us_parse_field_positions():
    rows = parse_sina_us_quotes(SINA_US)
    r = rows["AAPL"]
    assert r["name"] == "苹果"
    assert r["last"] == 324.96 and r["pre_close"] == 325.13  # 26=昨收（27 是笔数）
    assert r["open"] == 326.865 and r["high"] == 328.4 and r["low"] == 323.53
    assert r["volume"] == 33776365.0
    assert pd.isna(r["amount"])  # 美股成交额无可靠字段 → NaN
    assert r["ts"] == "2026-09-03 17:23:59"
    assert abs(r["last"] / r["pre_close"] - 1 - (-0.05 / 100)) < 0.0005


def test_sina_global_short_body_skipped():
    rows = parse_sina_hk_quotes('var hq_str_rt_hk00001="1,2,3";')
    assert rows == {}


# ---------------- 腾讯港股快照（与 A股共用布局） ----------------


def test_tencent_hk_quote_parse():
    rows = parse_tencent_quotes(TENCENT_HK)
    r = rows["00700"]
    assert r["name"] == "腾讯控股"
    assert r["last"] == 433.0 and r["pre_close"] == 438.2 and r["open"] == 444.2
    assert r["high"] == 445.6 and r["low"] == 433.0
    assert r["volume"] == 17387096.0 and r["amount"] == 7597753829.266
    assert r["ts"] == "2026-09-03 16:08:23"
    # 新浪/腾讯同标的对账：量额完全一致（跨源佐证的实测依据）
    hk = parse_sina_hk_quotes(SINA_HK)["00700"]
    assert hk["volume"] == r["volume"] and hk["amount"] == r["amount"]


def test_tx_ts_formats():
    assert _tx_ts("2026/09/03 16:08:23") == "2026-09-03 16:08:23"
    assert _tx_ts("20260903150012") == "2026-09-03 15:00:12"


# ---------------- 代码规则 ----------------


def test_hk_us_market_rules():
    assert hk_us_market("00700") == "hk"
    assert hk_us_market("0700") == "hk"
    assert hk_us_market("AAPL") == "us"
    assert hk_us_market("BRK.A") == "us"
    with pytest.raises(DataError, match="无法识别"):
        hk_us_market("600519")  # 6 位数字是 A股规则，不属于港/美
    with pytest.raises(DataError):
        hk_us_market("700")  # 3 位数字不是港股（4-5 位）


# ---------------- 腾讯 fqkline（港/美共用解析） ----------------


def test_fqkline_hk_ignores_trailing_metadata():
    """港股行尾附分红元数据 dict → 忽略额外元素，且收在行序第 2 位。"""
    payload = {"data": {"hk00700": {"day": [
        ["2026-09-01", "440.000", "438.900", "443.000", "436.500", "19341328.000"],
        ["2026-09-02", "439.000", "438.200", "441.400", "435.000",
         "21341328.000", {"date": "2026-09-02", "id": "hk00700"}],
    ]}}}
    df = parse_fqkline(payload, "hk00700")
    assert list(df.columns) == BAR_COLUMNS
    assert df["close"].iloc[-1] == 438.2
    assert df["volume"].iloc[-1] == 21341328.0
    assert df["amount"].isna().all()
    assert df["date"].is_monotonic_increasing


def test_fqkline_us_row_order():
    payload = {"data": {"usAAPL.OQ": {"qfqday": [
        ["2026-09-03", "326.000", "324.960", "328.400", "323.530", "33776365"],
    ]}}}
    df = parse_fqkline(payload, "usAAPL.OQ")
    row = df.iloc[0]
    assert row["close"] == 324.96 and row["high"] == 328.4  # 收在开之后
    assert row["date"] == pd.Timestamp("2026-09-03").date()


def test_fqkline_empty_raises():
    with pytest.raises(DataError, match="腾讯日K无数据"):
        parse_fqkline({"data": {"hk00700": {}}}, "hk00700")


# ---------------- 期货适配层 ----------------


def test_main_continuous_pattern():
    assert _MAIN_RE.match("RB0") and _MAIN_RE.match("IF0")
    assert not _MAIN_RE.match("RB2610") and not _MAIN_RE.match("MA601")


def test_futures_normalize_columns():
    df = pd.DataFrame({
        "日期": ["2026-09-02", "2026-09-03"],
        "开盘价": [3130.0, 3136.0], "最高价": [3155.0, 3152.0],
        "最低价": [3120.0, 3124.0], "收盘价": [3138.0, 3142.0],
        "成交量": [700000.0, 741623.0], "持仓量": [1470000.0, 1471614.0],
    })
    out = AkshareFuturesDailySource._normalize(df, "RB0")
    assert list(out.columns) == BAR_COLUMNS
    assert out["amount"].isna().all()  # 期货日K无成交额 → NaN
    assert str(out["date"].iloc[0]) == "2026-09-02"
    assert out["date"].is_monotonic_increasing


def test_futures_normalize_rejects_empty_and_missing_columns():
    with pytest.raises(DataError, match="返回空数据"):
        AkshareFuturesDailySource._normalize(pd.DataFrame(), "RB0")
    bad = pd.DataFrame({"日期": ["2026-09-02"], "开盘价": [1.0]})  # 缺高低收量
    with pytest.raises(DataError, match="缺少列"):
        AkshareFuturesDailySource._normalize(bad, "RB0")


# ---------------- 源健康跟踪（SourceHealth） ----------------


class FakeSnapshotSource:
    """离线快照源：可配置返回帧或异常，记录调用次数。"""

    def __init__(self, name, df=None, exc=None):
        self.name = name
        self._df, self._exc = df, exc
        self.calls = 0

    def fetch_snapshot(self, symbols):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._df


def _snap_df(last: float, symbol: str = "00700") -> pd.DataFrame:
    row = {c: float("nan") for c in SNAPSHOT_COLUMNS}
    row.update({"symbol": symbol, "name": "X", "last": last, "pre_close": last})
    return pd.DataFrame([row])[SNAPSHOT_COLUMNS]


def test_source_health_cooldown_order_and_reset():
    h = SourceHealth(cooldown_seconds=60.0)
    h.record_failure("b")
    h.record_failure("b")
    assert h.in_cooldown("b") and not h.in_cooldown("a")
    # 冷却源排链尾兜底（无论原序），健康源保持原相对顺序
    a, b = FakeSnapshotSource("a"), FakeSnapshotSource("b")
    assert [s.name for s in h.order([a, b])] == ["a", "b"]
    assert [s.name for s in h.order([b, a])] == ["a", "b"]
    h.record_success("b")
    assert not h.in_cooldown("b")  # 成功一次即清零


def test_multisource_cooldown_skips_failed_source():
    """失败源进入冷却：下次组合直接跳过，不再付出超时代价。"""
    flaky = FakeSnapshotSource("flaky", exc=DataError("连接被重置"))
    backup = FakeSnapshotSource("backup", df=_snap_df(100.0))
    combo = MultiSourceSnapshot([flaky, backup], cross_check=False)

    assert float(combo.fetch_snapshot(["00700"])["last"].iloc[0]) == 100.0
    assert flaky.calls == 1 and backup.calls == 1

    assert float(combo.fetch_snapshot(["00700"])["last"].iloc[0]) == 100.0
    assert flaky.calls == 1 and backup.calls == 2  # 冷却期 flaky 未被调用
