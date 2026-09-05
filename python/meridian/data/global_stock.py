"""港/美股数据源适配层（DataSource 实现）。

渠道实测（2026-09-03，详见 docs/DATA_SOURCES.md §6）：
- 腾讯 ifzq fqkline：港股/美股日K可用；美股代码需带交易所后缀
  （AAPL→AAPL.OQ），经腾讯 smartbox 搜索解析并进程内缓存；
- 东财 push2his：港股 secid=116.XXXXX；美股交易所未知 → searchapi
  解析 QuoteID（如 105.AAPL）。⚠ 当日实测 EM 对港/美频繁掐连，适配器
  保留，EM 恢复后在 config/data_sources.yaml 将 eastmoney_global 加入链。

统一 BAR schema；腾讯源无成交额 → amount=NaN（宁缺毋错，Rust 侧允许）。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, DataSource, FetchRequest, with_retry
from meridian.data.cn_stock_tencent import parse_fqkline
from meridian.data.realtime import _http_get

# 港股代码：4-5 位数字；美股：纯字母（含 .）
_HK_RE = re.compile(r"^\d{4,5}$")
_US_RE = re.compile(r"^[A-Z]+(\.[A-Z]+)?$", re.IGNORECASE)


def hk_us_market(symbol: str) -> str:
    """市场判定：4-5 位数字=港股，纯字母=美股。"""
    s = symbol.strip()
    if _HK_RE.match(s):
        return "hk"
    if _US_RE.match(s):
        return "us"
    raise DataError(f"无法识别港/美股代码: {symbol}（港股4-5位数字 / 美股字母代码）")


@lru_cache(maxsize=512)
def tencent_us_code(ticker: str) -> str:
    """美股代码 → 腾讯完整代码（AAPL → usAAPL.OQ）。smartbox 搜索，结果缓存。"""
    q = ticker.strip().upper()
    raw = _http_get(
        f"https://smartbox.gtimg.cn/s3/?v=2&q={q}&t=all",
        referer="https://gu.qq.com/", encoding="gbk",
    )
    m = re.search(r'v_hint="([^"]*)"', raw)
    if not m:
        raise DataError(f"腾讯 smartbox 无结果: {q}")
    # 每条候选: us~aapl.oq~苹果~pg~GP，条目间 ^ 分隔；取点号前精确匹配的
    for item in m.group(1).split("^"):
        parts = item.split("~")
        if len(parts) >= 2 and parts[0] == "us" and parts[1].split(".")[0].upper() == q:
            return "us" + parts[1].upper()
    raise DataError(f"腾讯 smartbox 未匹配美股 {q}: {m.group(1)[:120]}")


@lru_cache(maxsize=512)
def em_quote_id(symbol: str) -> str:
    """港/美股代码 → 东财 QuoteID（00700→116.00700，AAPL→105.AAPL）。

    searchapi 精确码匹配（避免 AAPL22 之类债券误中）。
    """
    q = symbol.strip().upper()
    raw = _http_get(
        "https://searchapi.eastmoney.com/api/suggest/get"
        f"?input={q}&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=10",
        referer="https://quote.eastmoney.com/",
    )
    try:
        table = json.loads(raw)["QuotationCodeTable"]["Data"] or []
    except (ValueError, KeyError) as exc:
        raise DataError(f"东财 searchapi 报文异常: {exc}") from exc
    for d in table:
        if str(d.get("Code", "")).upper() == q and d.get("QuoteID"):
            return str(d["QuoteID"])
    raise DataError(f"东财 searchapi 未匹配 {q}")


def tencent_global_code(symbol: str) -> str:
    """港/美股代码 → 腾讯完整代码（00700→hk00700，AAPL→usAAPL.OQ）。"""
    if hk_us_market(symbol) == "hk":
        return f"hk{symbol.strip()}"
    return tencent_us_code(symbol)


def _filter_range(df: pd.DataFrame, request: FetchRequest) -> pd.DataFrame:
    mask = (df["date"] >= pd.Timestamp(request.start).date()) & (
        df["date"] <= pd.Timestamp(request.end).date()
    )
    out = df.loc[mask].reset_index(drop=True)
    if out.empty:
        raise DataError(f"区间无数据: {request.symbol} {request.start}~{request.end}")
    return out


class TencentGlobalDailySource(DataSource):
    """腾讯港/美股日K（ifzq fqkline，独立于东财/akshare 的历史源）。"""

    name = "tencent_global"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        code = tencent_global_code(request.symbol)

        def _call() -> pd.DataFrame:
            u = (
                "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={code},day,{request.start},{request.end},640,qfq"
            )
            try:
                payload = json.loads(_http_get(u, referer="https://gu.qq.com/"))
            except ValueError as exc:
                raise DataError(f"腾讯全球日K报文异常: {exc}") from exc
            return parse_fqkline(payload, code)

        df = with_retry(_call, self._cfg.retry_for(self.name),
                        what=f"腾讯全球日K {request.symbol}")
        return _filter_range(df, request)


class EmGlobalDailySource(DataSource):
    """东财港/美股日K（push2his；secid 经 searchapi 解析）。

    ⚠ 2026-09-03 实测 EM 对港/美连接不稳定，默认链未启用；
    作为 failover 成员保留（SourceHealth 冷却会自动跳过不可用源）。
    """

    name = "eastmoney_global"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        secid = em_quote_id(request.symbol)

        def _call() -> pd.DataFrame:
            u = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid={secid}&klt=101&fqt=1&lmt=640&end=20500101"
                "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
            )
            try:
                payload = json.loads(_http_get(u, referer="https://quote.eastmoney.com/"))
            except ValueError as exc:
                raise DataError(f"东财全球日K报文异常: {exc}") from exc
            klines = (payload.get("data") or {}).get("klines") or []
            if not klines:
                raise DataError(f"东财全球日K无数据: {request.symbol} secid={secid} rc={payload.get('rc')}")
            rows = []
            for line in klines:
                f = line.split(",")
                rows.append({
                    "date": pd.Timestamp(f[0]).date(),
                    "open": float(f[1]), "high": float(f[3]),
                    "low": float(f[4]), "close": float(f[2]),
                    "volume": float(f[5]), "amount": float(f[6]),
                })
            return pd.DataFrame(rows)[BAR_COLUMNS]

        df = with_retry(_call, self._cfg.retry_for(self.name),
                        what=f"东财全球日K {request.symbol}")
        return _filter_range(df, request)
