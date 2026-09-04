"""配置加载：config/*.yaml → 类型化配置。

原则（验收标准 6）：软件名 / 权重 / 标的池 / 数据源只存在于 config/，代码零硬编码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    """配置缺失或格式错误。"""


def _detect_market(symbol: str) -> tuple[str, str]:
    """从代码模式推断 (market, asset_type)。

    - 6位数字、0/3/6开头 → A股
    - 5位数字 → 港股
    - 纯字母 → 美股
    - 字母+数字（RB0/IF0 等） → 期货
    """
    s = symbol.strip().upper()
    if s.isdigit():
        if len(s) == 6 and s[0] in "036":
            return "cn", "stock"
        if len(s) == 5:
            return "hk", "stock"
        return "cn", "stock"
    if s.isalpha():
        return "us", "stock"
    if any(c.isalpha() for c in s) and any(c.isdigit() for c in s):
        return "cn", "futures"
    return "cn", "stock"


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件格式错误（应为映射）: {path}")
    return data


def _project_root() -> Path:
    """项目根 = python/meridian 的上两级。"""
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppConfig:
    """config/app.yaml：软件名与路径。"""

    name: str
    version: str
    locale: str
    data_dir: Path
    report_dir: Path
    log_level: str

    @classmethod
    def load(cls, root: Path | None = None) -> "AppConfig":
        root = root or _project_root()
        raw = _load_yaml(root / "config" / "app.yaml")
        paths = raw.get("paths", {})
        log = raw.get("log", {})
        return cls(
            name=str(raw.get("name", "Meridian")),
            version=str(raw.get("version", "0.0.0")),
            locale=str(raw.get("locale", "zh-CN")),
            data_dir=root / str(paths.get("data_dir", "data")),
            report_dir=root / str(paths.get("report_dir", "reports")),
            log_level=str(log.get("level", "info")),
        )


@dataclass(frozen=True)
class SymbolEntry:
    symbol: str
    name: str


@dataclass(frozen=True)
class MarketEntry:
    market: str
    asset_type: str
    frequency: str
    symbols: tuple[SymbolEntry, ...]

    def scoring_config(self) -> str:
        """该资产类型对应的评分配置文件名。"""
        return f"{self.asset_type}.yaml"


@dataclass(frozen=True)
class MarketsConfig:
    """config/markets.yaml：标的池。"""

    markets: tuple[MarketEntry, ...] = field(default=())

    def find(self, symbol: str) -> MarketEntry:
        for entry in self.markets:
            if any(s.symbol == symbol for s in entry.symbols):
                return entry
        raise ConfigError(f"标的 {symbol} 不在标的池中（config/markets.yaml）")

    def find_or_auto(self, symbol: str, name: str | None = None) -> tuple[MarketEntry, SymbolEntry]:
        """标的池里有就用配置；没有则按代码模式自动识别市场/类型。

        标的池是为批量扫描/组合管理准备的，不该挡住单标的即兴分析。
        """
        for entry in self.markets:
            for s in entry.symbols:
                if s.symbol == symbol:
                    return entry, s
        market, asset_type = _detect_market(symbol)
        auto_entry = MarketEntry(
            market=market, asset_type=asset_type, frequency="daily", symbols=()
        )
        return auto_entry, SymbolEntry(symbol=symbol, name=name or symbol)

    @classmethod
    def load(cls, root: Path | None = None) -> "MarketsConfig":
        root = root or _project_root()
        raw = _load_yaml(root / "config" / "markets.yaml")
        entries = []
        for m in raw.get("markets", []):
            entries.append(
                MarketEntry(
                    market=str(m["market"]),
                    asset_type=str(m["asset_type"]),
                    frequency=str(m.get("frequency", "daily")),
                    symbols=tuple(
                        SymbolEntry(symbol=str(s["symbol"]), name=str(s["name"]))
                        for s in m.get("symbols", [])
                    ),
                )
            )
        return cls(markets=tuple(entries))


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_seconds: float = 2.0


@dataclass(frozen=True)
class DataSourceConfig:
    """config/data_sources.yaml：数据源、重试策略与多源链配置。"""

    default: str
    sources: dict
    extra: dict = field(default_factory=dict)  # 其余顶层节（daily/realtime/minute 等）

    def section(self, name: str) -> dict:
        """取顶层配置节（如 daily / realtime / minute），缺省空映射。"""
        return dict(self.extra.get(name, {}) or {})

    def retry_for(self, name: str) -> RetryConfig:
        src = self.sources.get(name, {})
        retry = src.get("retry", {})
        return RetryConfig(
            max_attempts=int(retry.get("max_attempts", 3)),
            backoff_seconds=float(retry.get("backoff_seconds", 2.0)),
        )

    @classmethod
    def load(cls, root: Path | None = None) -> "DataSourceConfig":
        root = root or _project_root()
        raw = _load_yaml(root / "config" / "data_sources.yaml")
        return cls(
            default=str(raw.get("default", "akshare")),
            sources=dict(raw.get("sources", {})),
            extra={k: v for k, v in raw.items() if k not in ("default", "sources")},
        )
