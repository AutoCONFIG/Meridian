"""ScoreBased 回测：三层评分的 action 序列 → 目标仓位 → Rust 事件驱动撮合。

信号语义（与实盘同构）：T 日收盘出评分/建议 → T+1 开盘按目标仓位调仓；
回测器不理解 action，action→仓位的映射来自 config/backtest.yaml（策略层）。
逐日评分用与实盘完全相同的 evaluate 路径（含逐日 regime 切权重档）——
回测测的就是线上行为，不另写一套"回测版评分"。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from meridian import meridian_core as mc
from meridian.config import RegimeConfig
from meridian.data.base import BAR_COLUMNS, DataError, FetchRequest

if TYPE_CHECKING:
    from meridian.config import MarketEntry, SymbolEntry
    from meridian.orchestrator.pipeline import AnalysisPipeline


@dataclass(frozen=True)
class BacktestSettings:
    """config/backtest.yaml；缺失文件时全默认（与 Rust BacktestConfig::default 一致）。"""

    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = 0.001
    trading_days_per_year: float = 252.0
    min_history_bars: int = 60
    warmup_regime: bool = True
    # action → 目标仓位；None = 维持现状
    action_weights: dict[str, float | None] = field(
        default_factory=lambda: {"Add": 1.0, "Hold": None, "Watch": None, "Reduce": 0.5, "Avoid": 0.0}
    )

    @classmethod
    def load(cls, root: Path | None = None) -> "BacktestSettings":
        import yaml

        path = (root or Path(__file__).resolve().parents[2]) / "config" / "backtest.yaml"
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            initial_cash=float(raw.get("initial_cash", 1_000_000.0)),
            commission_rate=float(raw.get("commission_rate", 0.0003)),
            min_commission=float(raw.get("min_commission", 5.0)),
            slippage_rate=float(raw.get("slippage_rate", 0.001)),
            trading_days_per_year=float(raw.get("trading_days_per_year", 252.0)),
            min_history_bars=int(raw.get("min_history_bars", 60)),
            warmup_regime=bool(raw.get("warmup_regime", True)),
            action_weights={k: (None if v is None else float(v))
                            for k, v in (raw.get("action_weights") or {}).items()},
        )


class ScoreBasedBacktester:
    """对单个标的跑"逐日评分策略"回测。

    用法：ScoreBasedBacktester(pipeline).run("600519", start=..., end=..., offline=...)
    返回 dict：绩效指标 + 逐日 action/权重序列 + 净值曲线。
    """

    def __init__(self, pipeline: "AnalysisPipeline"):
        self.pipeline = pipeline
        self.settings = BacktestSettings.load(pipeline._root)
        self._regime_detector: "mc.PyRegimeDetector | None" = None

    def run(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        offline: bool = False,
        name: str | None = None,
    ) -> dict:
        entry, sym = self.pipeline._resolve(symbol, name=name)
        df = self._load_bars(entry, sym, start, end, offline)
        if len(df) <= self.settings.min_history_bars:
            raise DataError(
                f"{symbol} 回测窗口不足（{len(df)} 根，需 > {self.settings.min_history_bars}）"
            )

        engine = self.pipeline.engine(entry.asset_type)
        regime_cfg = RegimeConfig.load(self.pipeline._root)
        if self._regime_detector is None:
            self._regime_detector = mc.PyRegimeDetector(
                trend_ma_fast=regime_cfg.trend_ma_fast,
                trend_ma_slow=regime_cfg.trend_ma_slow,
                trend_band=regime_cfg.trend_band,
                drawdown_window=regime_cfg.drawdown_window,
                crisis_drawdown=regime_cfg.crisis_drawdown,
                atr_period=regime_cfg.atr_period,
                atr_pct_crisis=regime_cfg.atr_pct_crisis,
                atr_pct_high_vol=regime_cfg.atr_pct_high_vol,
            )

        dates = [d.isoformat() for d in df["date"]]
        opens, highs = df["open"].tolist(), df["high"].tolist()
        lows, closes = df["low"].tolist(), df["close"].tolist()
        volumes, amounts = df["volume"].tolist(), df["amount"].tolist()

        # 逐日评分：与实盘同一条 evaluate 路径（窗口 = 历史到当日为止）
        actions: list[str | None] = [None] * len(df)  # 窗口不足日无信号
        weights: list[float] = [float("nan")] * len(df)
        for i in range(self.settings.min_history_bars, len(df)):
            regime = self._daily_regime(df, i) if self.settings.warmup_regime else "Unknown"
            score = engine.evaluate(
                symbol=sym.symbol, name=sym.name, market=entry.market,
                asset_type=entry.asset_type, frequency=entry.frequency,
                dates=dates[: i + 1], opens=opens[: i + 1], highs=highs[: i + 1],
                lows=lows[: i + 1], closes=closes[: i + 1],
                volumes=volumes[: i + 1], amounts=amounts[: i + 1],
                regime=regime,
            )
            action = str(score["action"]["action"])
            actions[i] = action
            w = self.settings.action_weights.get(action)
            if w is not None:
                weights[i] = w  # None（维持现状）保持 NaN

        result = mc.PyBacktester().simulate(
            dates, opens, highs, lows, closes, volumes, amounts, weights,
            initial_cash=self.settings.initial_cash,
            commission_rate=self.settings.commission_rate,
            min_commission=self.settings.min_commission,
            slippage_rate=self.settings.slippage_rate,
            trading_days_per_year=self.settings.trading_days_per_year,
        )
        result["actions"] = actions
        result["dates"] = dates
        result["target_weights"] = weights
        result["symbol"], result["name"] = sym.symbol, sym.name
        return result

    def _daily_regime(self, df: pd.DataFrame, i: int) -> str:
        """逐日 regime（只用当日及之前数据——红线 3 在回测路径同样成立）。"""
        w = df.iloc[: i + 1]
        out = self._regime_detector.detect(
            dates=[d.isoformat() for d in w["date"]],
            opens=[float(v) for v in w["open"]], highs=[float(v) for v in w["high"]],
            lows=[float(v) for v in w["low"]], closes=[float(v) for v in w["close"]],
            volumes=[float(v) for v in w["volume"]], amounts=[float(v) for v in w["amount"]],
        )
        return str(out["regime"]) if out else "Unknown"

    def _load_bars(self, entry, sym, start, end, offline) -> pd.DataFrame:
        """复用管线的增量同步/缓存回退路径拿全窗口K线（persist 语义与 analyze 一致）。"""
        from datetime import date as date_cls, timedelta

        end_date = date_cls.fromisoformat(end) if end else date_cls.today() - timedelta(days=1)
        start_date = date_cls.fromisoformat(start) if start else end_date - timedelta(days=500)

        if self.pipeline.persist and not offline:
            try:
                self.pipeline._sync_store(entry, sym, end_date)
            except DataError as exc:
                warnings.warn(f"回测前增量同步失败，用本地库: {exc}")
            df = self.pipeline._read_cache(entry, sym, start_date, end_date)
        else:
            df = self.pipeline._read_cache(entry, sym, start_date, end_date)
            if df.empty and not offline:
                df = self.pipeline._source_for(entry).fetch_daily(
                    FetchRequest(sym.symbol, start_date.isoformat(), end_date.isoformat())
                )
        if df.empty:
            raise DataError(f"{sym.symbol} 无可用K线（回测）")
        return df.reset_index(drop=True)
