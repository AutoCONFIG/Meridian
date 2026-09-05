"""增量落库同步：库内最新日期为游标，只拉缺口；UPSERT 幂等入库。

流程（docs/DATA_SOURCES.md §7）：
    latest_bar_date（Rust 单行查询）→ 无游标或 full=True → 全量窗口
    （history_days 自然日）；有游标 → 增量 [latest - overlap_days, end]，
    UPSERT 覆盖尾部重叠 bar，吸收盘后修正与复权微调。
    前复权整体基准漂移（分红除权致全历史平移）须 full=True 周期性重拉
    （数百根，成本可忽略）。

数据源失败原样抛 DataError，由调用方决定回退（管线自动回退本地库）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from meridian.data.base import DataError, DataSource, FetchRequest


@dataclass
class SyncReport:
    """一次同步的产出摘要（日志 / CLI 展示用）。"""

    symbol: str
    market: str
    mode: str  # full / incremental / none（库内已最新）
    start: str
    end: str
    fetched: int
    stored: int
    source: str

    def brief(self) -> str:
        if self.mode == "none":
            return f"{self.symbol}({self.market}) 已最新（{self.end}），本次未拉取"
        verb = "全量" if self.mode == "full" else "增量"
        return (
            f"{self.symbol}({self.market}) {verb}同步 {self.start}~{self.end}: "
            f"拉取 {self.fetched} 根，入库 {self.stored} 根（源={self.source}）"
        )


class DailySyncer:
    """日K增量同步器：source 拉缺口 → PyDb UPSERT。"""

    def __init__(self, source: DataSource, db, *,
                 history_days: int = 800, overlap_days: int = 5):
        self._source = source
        self._db = db
        self._history_days = history_days
        self._overlap = overlap_days

    def sync(self, *, symbol: str, name: str, market: str,
             asset_type: str = "stock", frequency: str = "daily",
             end: str | None = None, full: bool = False) -> SyncReport:
        end_d = date.fromisoformat(end) if end else date.today()
        latest = self._db.latest_bar_date(symbol=symbol, market=market, frequency=frequency)

        if full or not latest:
            start_d = end_d - timedelta(days=self._history_days)
            mode = "full"
        else:
            latest_d = date.fromisoformat(latest)
            if latest_d >= end_d:
                return SyncReport(symbol, market, "none", latest, str(end_d), 0, 0,
                                  self._source.name)
            start_d = latest_d - timedelta(days=self._overlap)
            mode = "incremental"

        df = self._source.fetch_daily(FetchRequest(symbol, start_d.isoformat(), end_d.isoformat()))
        if df.empty:
            if mode == "full":
                raise DataError(f"全量同步返回空数据: {symbol}（源={self._source.name}）")
            return SyncReport(symbol, market, mode, str(start_d), str(end_d), 0, 0,
                              self._source.name)

        stored = self._db.insert_bars(
            symbol=symbol, name=name, market=market, asset_type=asset_type,
            frequency=frequency,
            dates=[d.isoformat() if hasattr(d, "isoformat") else str(d) for d in df["date"]],
            opens=[float(v) for v in df["open"]],
            highs=[float(v) for v in df["high"]],
            lows=[float(v) for v in df["low"]],
            closes=[float(v) for v in df["close"]],
            volumes=[float(v) for v in df["volume"]],
            amounts=[float(v) for v in df["amount"]],
        )
        return SyncReport(symbol, market, mode,
                          str(df["date"].iloc[0]), str(df["date"].iloc[-1]),
                          len(df), stored, self._source.name)
