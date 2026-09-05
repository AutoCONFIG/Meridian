"""数据源抽象：统一 schema 的日K拉取接口。"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Sequence

import pandas as pd

from meridian.config import DataSourceConfig, RetryConfig

# 统一 K 线 schema（PLAN.md Step 7）：列名与顺序固定
BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


class DataError(Exception):
    """数据拉取失败（重试耗尽 / 返回为空 / 格式不符）。"""


@dataclass
class FetchRequest:
    symbol: str
    start: str  # YYYY-MM-DD
    end: str  # YYYY-MM-DD


class DataSource(abc.ABC):
    """所有数据源的统一抽象。

    实现约定：
    - fetch_daily 返回 DataFrame，列为 BAR_COLUMNS，date 为 datetime.date 升序
    - 失败时抛 DataError（重试由适配层负责，接口纯净）
    """

    name: str = "base"

    @abc.abstractmethod
    def fetch_daily(self, request: FetchRequest) -> pd.DataFrame:
        """拉取日K并返回统一 schema DataFrame。"""


def with_retry(fn, retry: RetryConfig, *, what: str):
    """带重试的调用包装：指数退避，最后一次失败抛原异常。"""

    last_exc: Exception | None = None
    for attempt in range(1, retry.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 —— 数据源库抛错类型不可枚举
            last_exc = exc
            if attempt < retry.max_attempts:
                time.sleep(retry.backoff_seconds * attempt)
    raise DataError(f"{what} 失败（重试 {retry.max_attempts} 次后放弃）: {last_exc}") from last_exc


def retry_from_config(sources: DataSourceConfig, name: str) -> RetryConfig:
    return sources.retry_for(name)


class SourceHealth:
    """进程内数据源健康跟踪（稳定性机制）。

    连续失败 → 冷却期线性延长（cooldown_seconds × 连续次数，封顶 max_cooldown）；
    冷却期内多源组合把它排到链尾兜底（不永久拉黑）。成功一次即清零。
    默认由组合对象各持一个实例；也可显式共享实现全局治理。
    """

    def __init__(self, cooldown_seconds: float = 300.0, max_cooldown: float = 1800.0):
        self._cooldown = cooldown_seconds
        self._max = max_cooldown
        self._fail: dict[str, tuple[int, float]] = {}  # name -> (连续失败次数, 冷却截止时刻)

    def record_success(self, name: str) -> None:
        self._fail.pop(name, None)

    def record_failure(self, name: str) -> None:
        n, _ = self._fail.get(name, (0, 0.0))
        n += 1
        self._fail[name] = (n, time.time() + min(self._cooldown * n, self._max))

    def in_cooldown(self, name: str) -> bool:
        n, until = self._fail.get(name, (0, 0.0))
        return n > 0 and time.time() < until

    def order(self, sources: Sequence) -> list:
        """冷却中的源排到链尾（保留兜底能力），其余保持原序。"""
        healthy = [s for s in sources if not self.in_cooldown(s.name)]
        cooling = [s for s in sources if self.in_cooldown(s.name)]
        return healthy + cooling
