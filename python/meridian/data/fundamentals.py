"""基本面数据源（Phase 3 v1：日频估值快照）。

渠道：百度股市通（经 akshare stock_zh_valuation_baidu），A股个股可用。
实测可用指标：市盈率(TTM)/市净率/总市值/市现率；市销率/股息率的 akshare
解析当前损坏（2026-09-05，None 占位）。单源 + 本地库回退（管线负责落库与降级，
拉不到不挡分析——基本面属增强信息）。
"""

from __future__ import annotations

import pandas as pd

from meridian.data.base import DataError, with_retry

_INDICATORS = (("pe_ttm", "市盈率(TTM)"), ("pb", "市净率"), ("total_mv", "总市值"), ("ps_ttm", "市现率"))


def normalize_valuation_frames(frames: dict[str, pd.DataFrame], symbol: str) -> dict:
    """多指标序列 → 最新对齐快照 dict（date/pe_ttm/pb/...，缺失为 None）。

    date 取各指标最新日期的最大值（指标可能不同步）。
    """
    out: dict = {"date": None, "source": "baidu"}
    if not frames:
        raise DataError(f"百度估值全部指标无数据: {symbol}")
    dates = []
    for key, df in frames.items():
        if df is None or df.empty or "value" not in df.columns:
            out[key] = None
            continue
        last = df.sort_values("date").iloc[-1]
        out[key] = float(last["value"]) if pd.notna(last["value"]) else None
        dates.append(str(last["date"]))
    if not dates:
        raise DataError(f"百度估值序列为空: {symbol}")
    out["date"] = max(dates)
    return out


class FundamentalSource:
    """A股个股估值快照源（百度渠道，多指标逐个拉取，单项失败留 None）。"""

    name = "baidu_fundamental"

    def __init__(self, cfg=None):
        self._cfg = cfg

    def fetch_latest(self, symbol: str) -> dict:
        """拉取某标的最新估值快照。全部指标失败才抛 DataError（调用方降级）。"""
        import akshare as ak

        def _fetch_one(indicator: str) -> pd.DataFrame:
            return ak.stock_zh_valuation_baidu(symbol=symbol, indicator=indicator, period="全部")

        frames: dict[str, pd.DataFrame] = {}
        for key, indicator in _INDICATORS:
            try:
                if self._cfg is not None:
                    frames[key] = with_retry(
                        lambda ind=indicator: _fetch_one(ind),
                        self._cfg.retry_for(self.name), what=f"百度估值 {symbol} {indicator}")
                else:
                    frames[key] = _fetch_one(indicator)
            except Exception:  # noqa: BLE001 —— 单项指标失败留 None
                frames[key] = None

        return normalize_valuation_frames(frames, symbol)
