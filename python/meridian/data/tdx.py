"""通达信协议数据源（pytdx，可选依赖）——A股五档快照 / 未复权日K / 分笔。

pytdx 是社区对通达信私有 TCP 协议（7709 端口）的逆向实现。
注意：
- 内置服务器列表时效性强（2026-09-03 实测 7 台可达），支持 config 覆盖 + 探活换台；
- 日K为不复权原始价，且**最后一根是当日实时累计值**（盘中即返回 15:00 行）；
- 未安装 pytdx 时所有方法抛 DataError（提示安装命令），不影响其他数据源。

统一快照 schema 之外附加五档列：bid1..bid5 / ask1..ask5 / bid_vol1..5 / ask_vol1..5
（价格元、量为手；无档位为 NaN）。
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, with_retry
from meridian.data.realtime import SNAPSHOT_COLUMNS, _now_str

# 2026-09-03 实测可达（内置 hq_hosts 已过时；端口 7709）
DEFAULT_SERVERS: list[tuple[str, int]] = [
    ("115.238.90.165", 7709),
    ("218.75.126.9", 7709),
    ("60.12.136.250", 7709),
    ("115.238.56.198", 7709),
    ("180.153.18.170", 7709),
    ("110.41.147.114", 7709),
    ("122.51.120.217", 7709),
]

_LEVEL_COLS = (
    [f"bid{i}" for i in range(1, 6)] + [f"ask{i}" for i in range(1, 6)]
    + [f"bid_vol{i}" for i in range(1, 6)] + [f"ask_vol{i}" for i in range(1, 6)]
)

TDX_COLUMNS = SNAPSHOT_COLUMNS + _LEVEL_COLS


def _tdx_market(symbol: str) -> int:
    """A股 6 位代码 → 通达信市场号（1=沪 0=深）。"""
    s = symbol.strip()
    if len(s) == 6 and s.isdigit():
        return 1 if s[0] == "6" else 0
    raise DataError(f"通达信源仅支持 A 股 6 位代码: {symbol}")


class TdxSource:
    """通达信协议源：五档快照 / 未复权日K / 分笔成交（3 秒快照级）。"""

    name = "tdx"

    def __init__(self, servers: Sequence[tuple[str, int]] | None = None,
                 cfg: DataSourceConfig | None = None):
        self._cfg = cfg
        if servers is None:
            self._cfg = cfg or DataSourceConfig.load()
            raw = self._cfg.sources.get("tdx", {}).get("servers") or []
            servers = [
                (str(item).split(":")[0], int(str(item).split(":")[1]))
                for item in raw
            ]
        self._servers = list(servers) or DEFAULT_SERVERS

    def _connect(self):
        try:
            from pytdx.hq import TdxHq_API
        except ImportError as exc:
            raise DataError(
                "pytdx 未安装: uv pip install -p .venv/Scripts/python.exe pytdx"
            ) from exc
        errs = []
        for ip, port in self._servers:
            api = TdxHq_API()
            try:
                if api.connect(ip, port, time_out=3):
                    return api
            except Exception as exc:  # noqa: BLE001 —— pytdx 连接错误类型不可枚举
                errs.append(f"{ip}:{port} {exc}")
        raise DataError("通达信服务器全部未连通: " + "; ".join(errs or self._servers))

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        """A股五档快照。列为 TDX_COLUMNS（SNAPSHOT_COLUMNS 超集）。"""
        pairs = [(_tdx_market(s), s.strip()) for s in symbols]

        def _call() -> list[dict]:
            api = self._connect()
            try:
                quotes = api.get_security_quotes(pairs)
            finally:
                api.disconnect()
            if not quotes:
                raise DataError(f"通达信快照为空: {list(symbols)}")
            return quotes

        quotes = with_retry(_call, (self._cfg_retry()), what=f"通达信快照 {list(symbols)}")
        rows = []
        for d in quotes:
            row = {
                "symbol": d["code"], "name": d.get("name", ""),
                "last": float(d["price"]), "pre_close": float(d["last_close"]),
                "open": float(d["open"]), "high": float(d["high"]), "low": float(d["low"]),
                "volume": float(d["vol"]),  # 手
                "amount": float(d["amount"]), "open_interest": float("nan"),
                "ts": _now_str(),
            }
            for col in _LEVEL_COLS:
                row[col] = float(d.get(col, float("nan")) or float("nan"))
            rows.append(row)
        return pd.DataFrame(rows)[TDX_COLUMNS]

    def fetch_recent_daily(self, symbol: str, count: int = 120) -> pd.DataFrame:
        """未复权日K（count ≤ 800；最后一根为当日实时累计值）。"""
        market, code = _tdx_market(symbol), symbol.strip()

        def _call() -> pd.DataFrame:
            api = self._connect()
            try:
                bars = api.get_security_bars(9, market, code, 0, min(count, 800))
            finally:
                api.disconnect()
            if not bars:
                raise DataError(f"通达信日K为空: {symbol}")
            rows = [{
                "date": pd.Timestamp(b["datetime"][:10]).date(),
                "open": float(b["open"]), "high": float(b["high"]),
                "low": float(b["low"]), "close": float(b["close"]),
                "volume": float(b["vol"]), "amount": float(b["amount"]),
            } for b in bars]
            return pd.DataFrame(rows)[BAR_COLUMNS].sort_values("date").reset_index(drop=True)

        return with_retry(_call, self._cfg_retry(), what=f"通达信日K {symbol}")

    def fetch_transaction(self, symbol: str, count: int = 30) -> pd.DataFrame:
        """分笔成交（3 秒快照级，非逐笔）。列: time/price/vol/num/buyorsale。"""
        market, code = _tdx_market(symbol), symbol.strip()

        def _call() -> pd.DataFrame:
            api = self._connect()
            try:
                data = api.get_transaction_data(market, code, 0, count)
            finally:
                api.disconnect()
            if not data:
                raise DataError(f"通达信分笔为空: {symbol}")
            return pd.DataFrame(data)

        return with_retry(_call, self._cfg_retry(), what=f"通达信分笔 {symbol}")

    def _cfg_retry(self):
        from meridian.data.base import retry_from_config

        return retry_from_config(self._cfg or DataSourceConfig.load(), self.name)
