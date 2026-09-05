"""指数日K数据源（regime 检测的指数输入）。

接口与 cn_stock_tencent 同款（ifzq.gtimg.cn fqkline）——指数代码自带市场前缀
（sh000300 沪深300 / hkHSI 恒生），无需再加前缀，也不做复权处理（qfq 对指数无害）。
拉取失败由调用方降级为标的自身K线代理，不阻断分析。
"""

from __future__ import annotations

import json
import warnings

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import BAR_COLUMNS, DataError, FetchRequest, DataSource, with_retry
from meridian.data.realtime import _http_get
from meridian.data.cn_stock_tencent import parse_fqkline

_MAX_BARS = 640


class TencentIndexSource(DataSource):
    """腾讯指数日K。request.symbol 必须是带前缀的完整指数代码（如 sh000300）。"""

    name = "tencent_index"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        code = request.symbol.strip()
        if not code[:2].isalpha():
            raise DataError(f"指数代码需带市场前缀（如 sh000300）: {request.symbol}")

        def _call() -> pd.DataFrame:
            u = (
                "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={code},day,{request.start},{request.end},{_MAX_BARS},qfq"
            )
            try:
                payload = json.loads(_http_get(u, referer="https://gu.qq.com/"))
            except ValueError as exc:
                raise DataError(f"腾讯指数日K报文异常: {exc}") from exc
            return parse_fqkline(payload, code)

        df = with_retry(_call, self._cfg.retry_for(self.name),
                        what=f"腾讯指数日K {code}")
        mask = (df["date"] >= pd.Timestamp(request.start).date()) & (
            df["date"] <= pd.Timestamp(request.end).date()
        )
        df = df.loc[mask].reset_index(drop=True)
        if df.empty:
            raise DataError(f"腾讯指数日K区间为空: {code} {request.start}~{request.end}")
        if df["date"].iloc[0] > pd.Timestamp(request.start).date():
            warnings.warn(f"腾讯指数日K单次最多 {_MAX_BARS} 根，{code} 区间头部可能被截断")
        return df
