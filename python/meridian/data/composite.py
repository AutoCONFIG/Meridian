"""多源日K组合（DataSource 实现）：按序 failover + 可选跨源对账。

对账规则：比较"最后一根收盘价"（前复权均以最新交易日为基准，
最新收盘 = 不复权收盘，跨源可比）；相对偏差超 tolerance_pct 抛 DataError。
对账源失败仅告警不致命（容错优先，与快照层 MultiSourceSnapshot 同策略）。
"""

from __future__ import annotations

import warnings
from typing import Sequence

import pandas as pd

from meridian.config import DataSourceConfig
from meridian.data.base import DataError, DataSource, FetchRequest, SourceHealth


class MultiDailySource(DataSource):
    """多历史源组合。sources 按优先级排列，首个成功源出结果。"""

    name = "multi_daily"

    def __init__(self, sources: Sequence[DataSource], *,
                 cross_check: bool = True, tolerance_pct: float = 0.01,
                 health: SourceHealth | None = None):
        if not sources:
            raise DataError("MultiDailySource 需要至少一个数据源")
        self._sources = list(sources)
        self._cross = cross_check
        self._tol = tolerance_pct
        self._health = health or SourceHealth()

    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        primary: pd.DataFrame | None = None
        errs: list[str] = []
        for src in self._health.order(self._sources):
            try:
                primary = src.fetch_daily(request)
                self._health.record_success(src.name)
                break
            except DataError as exc:
                self._health.record_failure(src.name)
                errs.append(f"{src.name}: {exc}")
        if primary is None:
            raise DataError(f"全部日K源失败: {request.symbol} :: " + " | ".join(errs))

        if self._cross and len(self._sources) > 1:
            for src in self._sources[1:]:
                if self._health.in_cooldown(src.name):
                    continue
                try:
                    second = src.fetch_daily(request)
                except DataError as exc:
                    self._health.record_failure(src.name)
                    warnings.warn(f"日K对账源 {src.name} 失败（跳过对账）: {exc}")
                    continue
                self._health.record_success(src.name)
                a = float(primary["close"].iloc[-1])
                b = float(second["close"].iloc[-1])
                if b > 0 and abs(a - b) / b > self._tol:
                    raise DataError(
                        f"日K跨源对账超差 {request.symbol}: "
                        f"{self._sources[0].name}={a} vs {src.name}={b}"
                        f"（偏差 {abs(a - b) / b:.4%} > {self._tol:.2%}），疑似脏数据"
                    )
                break  # 首个可用对账源通过即结束
        return primary


def build_cn_stock_daily(cfg: DataSourceConfig | None = None) -> MultiDailySource:
    """A股日K多源组合：akshare（东财）为主，腾讯为 failover + 对账源。"""
    from meridian.data.cn_stock import CnStockSource
    from meridian.data.cn_stock_tencent import TencentDailySource

    cfg = cfg or DataSourceConfig.load()
    daily = cfg.section("daily").get("cn_stock", {})
    chain = daily.get("chain", ["akshare", "tencent"])
    registry = {"akshare": CnStockSource(cfg), "tencent": TencentDailySource(cfg)}
    return MultiDailySource(
        [registry[n] for n in chain],
        cross_check=bool(daily.get("cross_check", True)),
        tolerance_pct=float(daily.get("tolerance_pct", 0.01)),
    )


def build_global_daily(market: str, cfg: DataSourceConfig | None = None) -> MultiDailySource:
    """港/美股日K多源组合（daily.{hk_stock,us_stock}）。market: "hk"|"us"。"""
    from meridian.data.global_stock import EmGlobalDailySource, TencentGlobalDailySource

    cfg = cfg or DataSourceConfig.load()
    section = "hk_stock" if market == "hk" else "us_stock"
    daily = cfg.section("daily").get(section, {})
    chain = daily.get("chain", ["tencent_global"])
    registry = {
        "tencent_global": TencentGlobalDailySource(cfg),
        "eastmoney_global": EmGlobalDailySource(cfg),
    }
    return MultiDailySource(
        [registry[n] for n in chain],
        cross_check=bool(daily.get("cross_check", False)),
        tolerance_pct=float(daily.get("tolerance_pct", 0.01)),
    )


def build_futures_daily(cfg: DataSourceConfig | None = None) -> MultiDailySource:
    """期货日K多源组合（daily.futures）。主力连续滞后一天，当日看快照/分钟线。"""
    from meridian.data.futures import AkshareFuturesDailySource

    cfg = cfg or DataSourceConfig.load()
    daily = cfg.section("daily").get("futures", {})
    chain = daily.get("chain", ["akshare_futures"])
    registry = {"akshare_futures": AkshareFuturesDailySource(cfg)}
    return MultiDailySource(
        [registry[n] for n in chain],
        cross_check=bool(daily.get("cross_check", False)),
        tolerance_pct=float(daily.get("tolerance_pct", 0.01)),
    )
