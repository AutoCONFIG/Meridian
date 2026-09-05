"""实时快照 / 分钟K 多源适配层测试：报文解析 + failover + 跨源对账。

全部离线：报文 fixture 取自 2026-09-03 实测（见 docs/DATA_SOURCES.md），
不发起网络请求。
"""

from __future__ import annotations

import pandas as pd
import pytest

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, DataSource, FetchRequest
from meridian.data.cn_stock_tencent import parse_tencent_daily
from meridian.data.composite import MultiDailySource
from meridian.data.minute import MINUTE_COLUMNS, parse_em_klines, parse_tencent_mklines
from meridian.data.realtime import (
    MultiSourceSnapshot,
    SNAPSHOT_COLUMNS,
    build_cn_stock_realtime,
    cn_symbol_prefix,
    em_futures_secid,
    parse_sina_quotes,
    parse_tencent_quotes,
)

# ---------------- 报文 fixture（实测节选） ----------------

SINA_A = (
    'var hq_str_sh600519="贵州茅台,1297.500,1297.500,1299.160,1305.000,'
    '1293.020,1299.020,1299.190,1630665,2118012774.000";'
)
SINA_RB = (
    'var hq_str_nf_RB0="螺纹钢连续,142909,3136.000,3152.000,3124.000,0.000,'
    '3142.000,3143.000,3142.000,0.000,3158.000,595,1352,1471614.000,741623,'
    '沪,螺纹钢,2026-09-03";'
)
# 股指结构与商品不同：首字段即开盘价，日期/时间在尾部，名称在最后
SINA_IF = (
    'var hq_str_nf_IF0="4541.000,4561.200,4510.400,4545.600,52482,'
    '238354635.400,129902.000,0.000,0.000,4983.400,4077.400,0.000,0.000,'
    '4532.200,4530.400,139367.000,4545.600,5,0.000,0,0.000,0,0.000,0,'
    '0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,0,0.000,'
    '2026-09-03,14:29:09,100,1,,,,,,,,,4541.645,沪深300指数期货连续";'
)


def _tencent_body() -> str:
    f = ["0"] * 40
    f[0], f[1], f[2] = "1", "贵州茅台", "600519"
    f[3], f[4], f[5] = "1298.88", "1297.50", "1297.99"
    f[30] = "20260903150012"
    f[33], f[34] = "1305.00", "1293.02"
    f[35] = "1298.88/17748/2305193119"
    f[36] = "17748"
    return "~".join(f)


TENCENT_A = f'v_sh600519="{_tencent_body()}";'


# ---------------- 新浪报文解析 ----------------


def test_sina_a_share_parse():
    rows = parse_sina_quotes(SINA_A)
    r = rows["600519"]
    assert r["name"] == "贵州茅台"
    assert r["last"] == 1299.16 and r["pre_close"] == 1297.50
    assert r["open"] == 1297.50 and r["high"] == 1305.00 and r["low"] == 1293.02
    assert r["volume"] == 16306.65  # 股 → 手
    assert r["amount"] == 2118012774.0
    assert r["ts"]  # 源不带时间 → 取拉取时刻


def test_sina_commodity_parse():
    rows = parse_sina_quotes(SINA_RB)
    r = rows["RB0"]
    assert r["name"] == "螺纹钢连续"
    assert r["last"] == 3142.0 and r["pre_close"] == 3158.0  # 昨收=昨结算
    assert r["open_interest"] == 1471614.0
    assert r["volume"] == 741623.0
    assert r["ts"] == "2026-09-03 14:29:09"


def test_sina_cffex_parse_dynamic_fields():
    """股指字段位与商品不同：日期动态定位、名称取末尾非数值字段。"""
    rows = parse_sina_quotes(SINA_IF)
    r = rows["IF0"]
    assert r["name"] == "沪深300指数期货连续"
    assert r["last"] == 4545.60 and r["open"] == 4541.0
    assert r["volume"] == 52482.0 and r["amount"] == 238354635.4
    assert r["ts"] == "2026-09-03 14:29:09"


def test_tencent_quote_parse():
    rows = parse_tencent_quotes(TENCENT_A)
    r = rows["600519"]
    assert r["name"] == "贵州茅台"
    assert r["last"] == 1298.88 and r["pre_close"] == 1297.50
    assert r["open"] == 1297.99 and r["high"] == 1305.00 and r["low"] == 1293.02
    assert r["volume"] == 17748.0  # 手（与新浪口径一致）
    assert r["amount"] == 2305193119.0
    assert r["ts"] == "2026-09-03 15:00:12"


# ---------------- 代码规则 ----------------


def test_cn_symbol_prefix_rules():
    """按首位特征猜交易所前缀，不设白名单（159509 ETF 等尽力支持）。"""
    assert cn_symbol_prefix("600519") == "sh"
    assert cn_symbol_prefix("000001") == "sz"
    assert cn_symbol_prefix("300750") == "sz"
    assert cn_symbol_prefix("159509") == "sz"   # 深 ETF
    assert cn_symbol_prefix("510300") == "sh"   # 沪 ETF
    assert cn_symbol_prefix("900901") == "sh"   # 沪 B
    assert cn_symbol_prefix("830799") == "bj"   # 北交所（渠道有无数据由数据源报）
    with pytest.raises(DataError):
        cn_symbol_prefix("60051")               # 非 6 位仍拒绝


def test_em_futures_secid_rules():
    assert em_futures_secid("RB0", "SHFE") == "113.rbm"      # 主力连续
    assert em_futures_secid("rb2610", "SHFE") == "113.rb2610"
    with pytest.raises(DataError):
        em_futures_secid("IF0", "CFFEX")  # 中金所东财无数据


# ---------------- 分钟K / 日K 解析 ----------------


def test_em_klines_column_order():
    """东财 klines 实测顺序：日期,开,收,高,低,量,额（收在第二位）。"""
    df = parse_em_klines(
        ["2026-09-03 14:59,1298.00,1298.41,1298.50,1297.90,20,2598000.00"],
        "600519",
    )
    assert list(df.columns) == MINUTE_COLUMNS
    row = df.iloc[0]
    assert row["close"] == 1298.41 and row["high"] == 1298.50
    assert row["amount"] == 2598000.0 and pd.isna(row["open_interest"])


def test_tencent_mkline_column_order():
    df = parse_tencent_mklines(
        [["202609031500", "1298.00", "1298.88", "1298.88", "1298.41", "184"]],
        "600519",
    )
    assert list(df.columns) == MINUTE_COLUMNS
    row = df.iloc[0]
    assert row["date"] == "2026-09-03 15:00"
    assert row["close"] == 1298.88 and row["high"] == 1298.88 and row["low"] == 1298.41
    assert row["volume"] == 184.0 and pd.isna(row["amount"])


def test_tencent_daily_parse():
    payload = {"data": {"sh600519": {"qfqday": [
        ["2026-09-01", "1280.00", "1290.00", "1295.00", "1278.00", "32000.000"],
        ["2026-09-02", "1290.00", "1298.88", "1300.00", "1288.00", "28000.000"],
    ]}}}
    df = parse_tencent_daily(payload, "600519")
    assert list(df.columns) == BAR_COLUMNS
    assert df["close"].iloc[-1] == 1298.88  # 收在第二位
    assert df["date"].is_monotonic_increasing


# ---------------- 多源组合：快照 failover + 对账 ----------------


class FakeSnapshotSource:
    """离线快照源：可配置返回帧或异常。"""

    def __init__(self, name, df=None, exc=None):
        self.name = name
        self._df, self._exc = df, exc

    def fetch_snapshot(self, symbols):
        if self._exc is not None:
            raise self._exc
        return self._df


def _snap_df(last, symbol="600519"):
    row = {c: float("nan") for c in SNAPSHOT_COLUMNS}
    row.update({"symbol": symbol, "name": "X", "last": last, "pre_close": last})
    return pd.DataFrame([row])[SNAPSHOT_COLUMNS]


def test_snapshot_failover_to_second_source():
    chain = [
        FakeSnapshotSource("a", exc=DataError("down")),
        FakeSnapshotSource("b", df=_snap_df(100.0)),
    ]
    out = MultiSourceSnapshot(chain).fetch_snapshot(["600519"])
    assert float(out["last"].iloc[0]) == 100.0


def test_snapshot_all_sources_fail():
    chain = [
        FakeSnapshotSource("a", exc=DataError("x")),
        FakeSnapshotSource("b", exc=DataError("y")),
    ]
    with pytest.raises(DataError, match="全部快照源失败"):
        MultiSourceSnapshot(chain).fetch_snapshot(["600519"])


def test_snapshot_cross_check_within_tolerance():
    chain = [
        FakeSnapshotSource("a", df=_snap_df(100.0)),
        FakeSnapshotSource("b", df=_snap_df(100.2)),
    ]
    out = MultiSourceSnapshot(chain, cross_check=True, tolerance_pct=0.005).fetch_snapshot(["600519"])
    assert float(out["last"].iloc[0]) == 100.0


def test_snapshot_cross_check_mismatch_raises():
    chain = [
        FakeSnapshotSource("a", df=_snap_df(100.0)),
        FakeSnapshotSource("b", df=_snap_df(110.0)),
    ]
    with pytest.raises(DataError, match="跨源对账超差"):
        MultiSourceSnapshot(chain, cross_check=True, tolerance_pct=0.005).fetch_snapshot(["600519"])


def test_snapshot_cross_check_tolerates_second_source_failure():
    """对账源挂掉只告警不致命（容错优先）。"""
    chain = [
        FakeSnapshotSource("a", df=_snap_df(100.0)),
        FakeSnapshotSource("b", exc=DataError("down")),
    ]
    with pytest.warns(UserWarning):
        out = MultiSourceSnapshot(chain, cross_check=True).fetch_snapshot(["600519"])
    assert float(out["last"].iloc[0]) == 100.0


def test_snapshot_cross_check_off_ignores_mismatch():
    chain = [
        FakeSnapshotSource("a", df=_snap_df(100.0)),
        FakeSnapshotSource("b", df=_snap_df(110.0)),
    ]
    out = MultiSourceSnapshot(chain, cross_check=False).fetch_snapshot(["600519"])
    assert float(out["last"].iloc[0]) == 100.0


# ---------------- 多源组合：日K failover + 对账 ----------------


class FakeDailySource(DataSource):
    def __init__(self, name, close=None, exc=None):
        self.name = name
        self._close, self._exc = close, exc

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        if self._exc is not None:
            raise self._exc
        c = self._close
        return pd.DataFrame([{
            "date": pd.Timestamp("2026-09-02").date(),
            "open": c, "high": c, "low": c, "close": c,
            "volume": 1.0, "amount": 1.0,
        }])[BAR_COLUMNS]


def test_daily_failover_and_crosscheck_pass():
    chain = [
        FakeDailySource("a", exc=DataError("down")),
        FakeDailySource("b", close=100.5),
    ]
    out = MultiDailySource(chain, cross_check=True, tolerance_pct=0.01).fetch_daily(
        FetchRequest("600519", "2026-09-01", "2026-09-03")
    )
    assert float(out["close"].iloc[-1]) == 100.5


def test_daily_crosscheck_mismatch_raises():
    chain = [FakeDailySource("a", close=100.0), FakeDailySource("b", close=110.0)]
    with pytest.raises(DataError, match="跨源对账超差"):
        MultiDailySource(chain, cross_check=True, tolerance_pct=0.01).fetch_daily(
            FetchRequest("600519", "2026-09-01", "2026-09-03")
        )


# ---------------- 配置驱动的链组装 ----------------


def test_config_extra_section():
    cfg = DataSourceConfig(
        default="akshare", sources={},
        extra={"realtime": {"cn_stock": {"chain": ["sina"]}}},
    )
    assert cfg.section("realtime")["cn_stock"]["chain"] == ["sina"]
    assert cfg.section("missing") == {}


def test_build_fails_on_unknown_chain_source():
    cfg = DataSourceConfig(
        default="akshare", sources={},
        extra={"realtime": {"cn_stock": {"chain": ["sina", "nope"]}}},
    )
    with pytest.raises(DataError, match="未注册数据源"):
        build_cn_stock_realtime(cfg)
