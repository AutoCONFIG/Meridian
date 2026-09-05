"""腾讯日K数据源（DataSource 实现）——独立于东财/akshare 的第二历史源。

用途：与 akshare（东财源）互为 failover，并做跨源对账（composite.MultiDailySource）。
接口：ifzq.gtimg.cn fqkline（前复权）。
注意：
- 前复权以最新交易日为基准，最新 bar 的收盘价与不复权一致（对账依据）；
- 腾讯日K无成交额字段 → amount 为 NaN（BAR schema 保留列位）；
- 单次最多 640 根，请求区间过长时最早的数据缺失（缺失即告警）。
"""

from __future__ import annotations

import json
import warnings

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, FetchRequest, DataSource, with_retry
from meridian.data.realtime import _http_get, cn_symbol_prefix

_MAX_BARS = 640


def parse_fqkline(payload: dict, code: str) -> pd.DataFrame:
    """腾讯 fqkline JSON → 统一日K（⚠ 行序: 日期,开,收,高,低,量）。

    code 为已带前缀的完整代码（sh600519 / hk00700 / usAAPL.OQ）。
    港股行尾可能附分红元数据 dict → 忽略额外元素。
    """
    node = payload.get("data", {}).get(code, {})
    rows = node.get("qfqday") or node.get("day")
    if not rows:
        raise DataError(f"腾讯日K无数据: {code}（keys={list(node.keys())}）")
    out = []
    for r in rows:
        out.append({
            "date": pd.Timestamp(str(r[0])).date(),
            "open": float(r[1]), "high": float(r[3]), "low": float(r[4]),
            "close": float(r[2]),
            "volume": float(r[5]),  # 手（港股为股，口径见 DATA_SOURCES.md）
            "amount": float("nan"),
        })
    return pd.DataFrame(out)[BAR_COLUMNS].sort_values("date").reset_index(drop=True)


def parse_tencent_daily(payload: dict, symbol: str) -> pd.DataFrame:
    """腾讯 A股 fqkline JSON → 统一日K。"""
    code = f"{cn_symbol_prefix(symbol)}{symbol.strip()}"
    return parse_fqkline(payload, code)


class TencentDailySource(DataSource):
    """腾讯 A股日K（前复权，独立于东财的历史源）。"""

    name = "tencent"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        code = f"{cn_symbol_prefix(request.symbol)}{request.symbol.strip()}"

        def _call() -> pd.DataFrame:
            u = (
                "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={code},day,{request.start},{request.end},{_MAX_BARS},qfq"
            )
            try:
                payload = json.loads(_http_get(u, referer="https://gu.qq.com/"))
            except ValueError as exc:
                raise DataError(f"腾讯日K报文异常: {exc}") from exc
            return parse_tencent_daily(payload, request.symbol)

        df = with_retry(_call, self._cfg.retry_for(self.name),
                        what=f"腾讯日K {request.symbol}")
        mask = (df["date"] >= pd.Timestamp(request.start).date()) & (
            df["date"] <= pd.Timestamp(request.end).date()
        )
        df = df.loc[mask].reset_index(drop=True)
        if df.empty:
            raise DataError(f"腾讯日K区间为空: {request.symbol} {request.start}~{request.end}")
        if df["date"].iloc[0] > pd.Timestamp(request.start).date():
            warnings.warn(f"腾讯日K单次最多 {_MAX_BARS} 根，{request.symbol} 区间头部可能被截断")
        return df
