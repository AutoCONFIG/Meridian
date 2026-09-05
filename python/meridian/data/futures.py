"""期货数据源适配层（DataSource 实现，akshare/新浪系）。

- 主力连续（RB0/IF0）：ak.futures_main_sina —— 日K滞后一天（新浪源特性）；
  当日 bar 用实时快照（realtime）与分钟线（minute）补足。东财期货日K
  连接恢复后可加 EastmoneyFuturesDailySource（DATA_SOURCES.md §5）。
- 具体合约（RB2610）：ak.futures_zh_daily_sina，本地按区间过滤。

统一 BAR schema；无成交额 → NaN；持仓量不在日K schema（分钟线 schema 有）。
"""

from __future__ import annotations

import re

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, DataSource, FetchRequest, with_retry

# akshare 期货日K列名 → 统一 schema（缺列即报错，接口漂移不静默）
_FUTURES_COLUMN_MAP = {
    "日期": "date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "成交量": "volume",
}

_MAIN_RE = re.compile(r"^[A-Za-z]{1,2}0$")  # 主力连续: RB0 / IF0


class AkshareFuturesDailySource(DataSource):
    """akshare 期货日K（新浪源：主力连续 / 具体合约）。"""

    name = "akshare_futures"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        import akshare as ak  # 延迟导入：离线测试不付 import 成本

        symbol = request.symbol.strip().upper()
        start = request.start.replace("-", "")
        end = request.end.replace("-", "")

        def _call() -> pd.DataFrame:
            if _MAIN_RE.match(symbol):
                df = ak.futures_main_sina(symbol=symbol, start_date=start, end_date=end)
            else:
                df = ak.futures_zh_daily_sina(symbol=symbol)
            df = self._normalize(df, symbol)
            mask = (df["date"] >= pd.Timestamp(request.start).date()) & (
                df["date"] <= pd.Timestamp(request.end).date()
            )
            return df.loc[mask].reset_index(drop=True)

        return with_retry(_call, self._cfg.retry_for(self.name),
                          what=f"akshare 期货日K {request.symbol}")

    @staticmethod
    def _normalize(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if df is None or df.empty:
            raise DataError(f"akshare 期货日K返回空数据: {symbol}")
        missing = [c for c in _FUTURES_COLUMN_MAP if c not in df.columns]
        if missing:
            raise DataError(f"akshare 期货日K缺少列 {missing}（接口可能已变更）: {symbol}")
        out = df[list(_FUTURES_COLUMN_MAP)].rename(columns=_FUTURES_COLUMN_MAP)
        out["date"] = pd.to_datetime(out["date"]).dt.date
        out["amount"] = float("nan")
        return out[BAR_COLUMNS].sort_values("date").reset_index(drop=True)
