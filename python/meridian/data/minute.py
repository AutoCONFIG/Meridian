"""分钟K多源适配层：东财(A股) / 腾讯(A股) / 新浪(期货，经 akshare)。

统一 schema（MINUTE_COLUMNS = BAR_COLUMNS + open_interest）：
- date 为分钟时间戳字符串 "YYYY-MM-DD HH:MM"；
- amount：东财A股有值，腾讯/新浪期货无此字段 → NaN（宁缺毋错）；
- open_interest：仅期货有值，A股 NaN；
- volume 统一为手。
"""

from __future__ import annotations

import json
import re
import time
from typing import Sequence

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, SourceHealth, with_retry
from meridian.data.realtime import _http_get, _resolve_chain, cn_symbol_prefix

MINUTE_COLUMNS = BAR_COLUMNS + ["open_interest"]

_EM_KLT = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
_TX_PERIOD = {"1": "m1", "5": "m5", "15": "m15", "30": "m30", "60": "m60"}


def _slice_tail(df: pd.DataFrame, limit: int | None) -> pd.DataFrame:
    if limit is not None and len(df) > limit:
        df = df.iloc[-limit:]
    return df.reset_index(drop=True)


# ---------------- 东财（A股分钟） ----------------


def parse_em_klines(klines: Sequence[str], symbol: str) -> pd.DataFrame:
    """东财 push2his klines（CSV 字符串列表）→ 统一分钟K。

    ⚠ 顺序实测为: 日期,开,收,高,低,量,额（收盘在第二位）。
    """
    rows = []
    for line in klines:
        f = line.split(",")
        rows.append({
            "date": f[0], "open": float(f[1]), "high": float(f[3]),
            "low": float(f[4]), "close": float(f[2]),
            "volume": float(f[5]), "amount": float(f[6]),
            "open_interest": float("nan"),
        })
    return pd.DataFrame(rows)[MINUTE_COLUMNS]


class EastmoneyMinuteSource:
    """东财 A股分钟K（push2his）。⚠ lmt 的值被忽略但参数必须存在。"""

    name = "eastmoney_minute"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_minute(self, symbol: str, period: str = "1",
                     limit: int | None = None) -> pd.DataFrame:
        klt = _EM_KLT.get(period)
        if klt is None:
            raise DataError(f"不支持的分钟周期: {period}")
        secid = f"{1 if cn_symbol_prefix(symbol) == 'sh' else 0}.{symbol.strip()}"

        def _call() -> pd.DataFrame:
            u = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid={secid}&klt={klt}&fqt=0&lmt=3&end=20500101"
                "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
            )
            try:
                payload = json.loads(_http_get(u, referer="https://quote.eastmoney.com/"))
            except (ValueError, KeyError) as exc:
                raise DataError(f"东财分钟K报文异常: {exc}") from exc
            data = payload.get("data")
            if not data or not data.get("klines"):
                raise DataError(f"东财分钟K无数据: {symbol} klt={klt} rc={payload.get('rc')}")
            return parse_em_klines(data["klines"], symbol)

        return _slice_tail(with_retry(_call, self._cfg.retry_for(self.name),
                                      what=f"东财分钟K {symbol}"), limit)


# ---------------- 腾讯（A股分钟） ----------------


def parse_tencent_mklines(rows: Sequence[Sequence], symbol: str) -> pd.DataFrame:
    """腾讯 ifzq mkline 行 → 统一分钟K。行序: [时间,开,收,高,低,量]。"""
    out = []
    for r in rows:
        t = str(r[0])  # YYYYMMDDHHMM
        out.append({
            "date": f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}",
            "open": float(r[1]), "high": float(r[3]), "low": float(r[4]),
            "close": float(r[2]), "volume": float(r[5]),
            "amount": float("nan"), "open_interest": float("nan"),
        })
    return pd.DataFrame(out)[MINUTE_COLUMNS]


class TencentMinuteSource:
    """腾讯 A股分钟K（ifzq.gtimg.cn，独立于东财的第二分钟源）。"""

    name = "tencent_minute"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_minute(self, symbol: str, period: str = "1",
                     limit: int | None = None) -> pd.DataFrame:
        key = _TX_PERIOD.get(period)
        if key is None:
            raise DataError(f"不支持的分钟周期: {period}")
        code = f"{cn_symbol_prefix(symbol)}{symbol.strip()}"

        def _call() -> pd.DataFrame:
            u = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{key},,{limit or 320}"
            try:
                payload = json.loads(_http_get(u, referer="https://gu.qq.com/"))
            except ValueError as exc:
                raise DataError(f"腾讯分钟K报文异常: {exc}") from exc
            node = payload.get("data", {}).get(code, {})
            rows = node.get(key) or node.get("m1")
            if not rows:
                raise DataError(f"腾讯分钟K无数据: {symbol} {key}")
            return parse_tencent_mklines(rows, symbol)

        return with_retry(_call, self._cfg.retry_for(self.name), what=f"腾讯分钟K {symbol}")


# ---------------- 新浪（期货分钟） ----------------


class SinaFuturesMinuteSource:
    """新浪期货分钟K（经 akshare futures_zh_minute_sina，带持仓量）。"""

    name = "sina_futures_minute"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_minute(self, symbol: str, period: str = "1",
                     limit: int | None = None) -> pd.DataFrame:
        if not re.match(r"^[A-Za-z]{1,2}\d{1,4}$", symbol.strip()):
            raise DataError(f"新浪期货分钟仅支持合约代码: {symbol}")

        def _call() -> pd.DataFrame:
            import akshare as ak  # 延迟导入：离线测试不付 import 成本

            df = ak.futures_zh_minute_sina(
                symbol=symbol.strip().upper(), period=period,
            )
            if df is None or df.empty:
                raise DataError(f"新浪期货分钟K为空: {symbol}")
            out = pd.DataFrame({
                "date": df["datetime"].astype(str).str.slice(0, 16),
                "open": df["open"].astype(float), "high": df["high"].astype(float),
                "low": df["low"].astype(float), "close": df["close"].astype(float),
                "volume": df["volume"].astype(float),
                "amount": float("nan"),
                "open_interest": df["hold"].astype(float),
            })
            return out[MINUTE_COLUMNS]

        return _slice_tail(with_retry(_call, self._cfg.retry_for(self.name),
                                      what=f"新浪期货分钟K {symbol}"), limit)


# ---------------- 多源组合 ----------------


class MultiSourceMinute:
    """分钟K failover 组合：按序取第一个成功源（分钟数据不做跨源对账，
    各源聚合时点略有差异，仅对 failover 负责；对账在快照层做）。"""

    def __init__(self, sources: Sequence, health: SourceHealth | None = None):
        if not sources:
            raise DataError("MultiSourceMinute 需要至少一个数据源")
        self._sources = list(sources)
        self._health = health or SourceHealth()

    def fetch_minute(self, symbol: str, period: str = "1",
                     limit: int | None = None) -> pd.DataFrame:
        errs: list[str] = []
        for src in self._health.order(self._sources):
            try:
                out = src.fetch_minute(symbol, period=period, limit=limit)
                self._health.record_success(src.name)
                return out
            except DataError as exc:
                self._health.record_failure(src.name)
                errs.append(f"{src.name}: {exc}")
        raise DataError(f"全部分钟源失败: {symbol} :: " + " | ".join(errs))


def build_cn_stock_minute(cfg: DataSourceConfig | None = None) -> MultiSourceMinute:
    """按 config/data_sources.yaml 的 minute.cn_stock 组装 A股分钟链。"""
    cfg = cfg or DataSourceConfig.load()
    rt = cfg.section("minute").get("cn_stock", {})
    chain = rt.get("chain", ["eastmoney_minute", "tencent_minute"])
    registry = {
        "eastmoney_minute": EastmoneyMinuteSource(cfg),
        "tencent_minute": TencentMinuteSource(cfg),
    }
    return MultiSourceMinute(_resolve_chain(chain, registry, "A股分钟"))


def build_futures_minute(cfg: DataSourceConfig | None = None) -> MultiSourceMinute:
    """按 config/data_sources.yaml 的 minute.futures 组装期货分钟链。"""
    cfg = cfg or DataSourceConfig.load()
    rt = cfg.section("minute").get("futures", {})
    chain = rt.get("chain", ["sina_futures_minute"])
    registry = {"sina_futures_minute": SinaFuturesMinuteSource(cfg)}
    return MultiSourceMinute(_resolve_chain(chain, registry, "期货分钟"))
