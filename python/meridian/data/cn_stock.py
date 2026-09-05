"""A 股数据源适配层（akshare）。

akshare 接口名/参数经常变动——所有对 akshare 的直接调用集中在本文件，
上层只依赖 DataSource 抽象。
"""

from __future__ import annotations

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import (
    BAR_COLUMNS,
    DataError,
    DataSource,
    FetchRequest,
    with_retry,
)

# akshare stock_zh_a_hist 返回列 → 统一 schema 列
_AKSHARE_COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
}


class CnStockSource(DataSource):
    """akshare A 股日K（前复权）。"""

    name = "akshare"

    def __init__(self, sources_cfg: DataSourceConfig | None = None):
        self._cfg = sources_cfg or DataSourceConfig.load()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        import akshare as ak  # 延迟导入：不联网的测试不付出 import 成本

        def _call() -> pd.DataFrame:
            df = ak.stock_zh_a_hist(
                symbol=request.symbol,
                period="daily",
                start_date=request.start.replace("-", ""),
                end_date=request.end.replace("-", ""),
                adjust="qfq",
            )
            return self._normalize(df, request.symbol)

        return with_retry(_call, self._cfg.retry_for(self.name), what=f"akshare 日K {request.symbol}")

    @staticmethod
    def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if df is None or df.empty:
            raise DataError(f"akshare 返回空数据: {symbol}")
        missing = [c for c in _AKSHARE_COLUMN_MAP if c not in df.columns]
        if missing:
            raise DataError(f"akshare 返回缺少列 {missing}（接口可能已变更）: {symbol}")

        out = df[list(_AKSHARE_COLUMN_MAP)].rename(columns=_AKSHARE_COLUMN_MAP)
        out["date"] = pd.to_datetime(out["date"]).dt.date
        out = out[BAR_COLUMNS].sort_values("date").reset_index(drop=True)
        return out
