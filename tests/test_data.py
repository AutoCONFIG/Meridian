"""数据层测试：离线 CSV → 统一 schema。"""

from __future__ import annotations

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
