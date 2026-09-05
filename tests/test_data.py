"""数据层测试：离线 CSV → 统一 schema。"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from meridian.data.base import BAR_COLUMNS, DataError, FetchRequest

from conftest import ROOT, CsvSource, make_uptrend_frame


def test_csv_source_unified_schema(tmp_path):
    df = make_uptrend_frame(130)
    src = CsvSource(df, tmp_path / "600519.csv")
    out = src.fetch_daily(FetchRequest("600519", "2026-01-01", "2026-12-31"))

    assert list(out.columns) == BAR_COLUMNS
    assert len(out) == 130
    assert out["date"].is_monotonic_increasing
    # OHLC 关系成立
    assert (out["high"] >= out[["open", "close"]].max(axis=1)).all()
    assert (out["low"] <= out[["open", "close"]].min(axis=1)).all()


def test_csv_source_date_filtering(tmp_path):
    df = make_uptrend_frame(60)
    src = CsvSource(df, tmp_path / "600519.csv")
    out = src.fetch_daily(FetchRequest("600519", "2026-02-01", "2026-02-28"))
    assert 0 < len(out) < 60


def test_column_mapping_catches_interface_drift():
    """akshare 列名变更 → 明确报错而非静默错位。"""
    from meridian.data.cn_stock import CnStockSource

    bad = pd.DataFrame({"日期": [], "开盘": []})  # 缺收盘/最高/最低等
    with pytest.raises(DataError):
        CnStockSource._normalize(bad, "600519")


# ---- 指数日K源（regime 指数输入）----


def test_tencent_index_source_parses_and_filters(monkeypatch, tmp_path):
    """腾讯指数日K：报文解析 + 区间过滤；代码必须带市场前缀。"""
    from meridian.data import index as idx_mod
    from meridian.data.index import TencentIndexSource

    payload = {"data": {"sh000300": {"day": [
        ["2026-01-04", 3890.0, 3910.0, 3920.0, 3880.0, 111.0],
        ["2026-01-05", 3910.0, 3950.0, 3960.0, 3900.0, 122.0],
        ["2026-01-06", 3950.0, 3930.0, 3960.0, 3920.0, 133.0],
        ["2026-03-01", 3930.0, 4000.0, 4010.0, 3925.0, 144.0],
    ]}}}
    seen = {}

    def fake_http_get(url, referer=None, timeout=10):
        seen["url"] = url
        return json.dumps(payload)

    monkeypatch.setattr(idx_mod, "_http_get", fake_http_get)
    src = TencentIndexSource()
    out = src.fetch_daily(FetchRequest("sh000300", "2026-01-05", "2026-01-31"))

    assert "param=sh000300,day,2026-01-05,2026-01-31" in seen["url"], "指数代码应原样入参（不加前缀）"
    assert list(out.columns) == BAR_COLUMNS
    assert out["date"].iloc[0] == pd.Timestamp("2026-01-05").date()
    assert out["date"].iloc[-1] == pd.Timestamp("2026-01-06").date(), "区间过滤生效"
    assert out["close"].iloc[0] == 3950.0  # ⚠ 腾讯行序: 日期,开,收,高,低,量

    with pytest.raises(DataError, match="前缀"):
        src.fetch_daily(FetchRequest("000300", "2026-01-01", "2026-01-31"))

