"""短期预判（信息性统计外推）。

回答"明天/短期可能怎么走"：基于 ATR 的次日波动区间、60 日动量斜率的
20 日外推、关键价位。全部由规则从现有 K 线计算——不是 AI、不是 action
建议（红线 1/2），输出必须带"统计外推不构成预测保证"的免责。
"""

from __future__ import annotations

import pandas as pd

from meridian.models.forecast import MomentumForecastModel


def short_term_outlook(df: pd.DataFrame) -> dict | None:
    """从日K计算短期预判所需统计量。数据不足（< 60 根）返回 None。"""
    if df is None or len(df) < 60:
        return None
    closes = df["close"].astype(float)
    highs, lows = df["high"].astype(float), df["low"].astype(float)
    prev_close = closes.shift(1)
    tr = pd.concat(
        [highs - lows, (highs - prev_close).abs(), (lows - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    last = float(closes.iloc[-1])

    # 动量外推：复用评分通道里的预测模型，反解 20 日预期收益（score = 50 + 250×expected）
    mom = MomentumForecastModel().analyze({"closes": closes.tolist()})
    expected_20d = (float(mom["score"]) - 50.0) / 250.0

    ma20 = float(closes.tail(20).mean())
    ma60 = float(closes.tail(60).mean())
    high20, low20 = float(highs.tail(20).max()), float(lows.tail(20).min())
    return {
        "last_close": last,
        "atr": atr,
        "range_68": (last - atr, last + atr),
        "range_95": (last - 2 * atr, last + 2 * atr),
        "expected_20d": expected_20d,
        "momentum_direction": str(mom["direction"]),
        "ma20": ma20,
        "ma60": ma60,
        "high20": high20,
        "low20": low20,
    }


def render_outlook(o: dict) -> list[str]:
    """预判 → markdown「短期预判」节。"""
    lo68, hi68 = o["range_68"]
    lo95, hi95 = o["range_95"]
    exp = o["expected_20d"]
    skew = {"up": "偏多", "down": "偏空", "neutral": "中性"}.get(o["momentum_direction"], o["momentum_direction"])
    target_lo = o["last_close"] * (1 + exp - o["atr"] / o["last_close"])
    target_hi = o["last_close"] * (1 + exp + o["atr"] / o["last_close"])
    return [
        "## 短期预判",
        "",
        f"**明日（下一交易日）波动区间**（前一收盘 {o['last_close']:.2f}，ATR14={o['atr']:.2f}）：",
        f"- 约 68% 概率落在 **{lo68:.2f} ~ {hi68:.2f}**（±1 ATR）",
        f"- 约 95% 概率落在 **{lo95:.2f} ~ {hi95:.2f}**（±2 ATR）",
        "",
        f"**20 日动量外推**（近 60 日收盘最小二乘斜率，若当前趋势延续）：预期收益 **{exp:+.1%}**（{skew}），"
        f"对应 20 日后参考区间约 **{target_lo:.2f} ~ {target_hi:.2f}**（外推 ±1 ATR）。",
        "",
        "**关键价位**：",
        f"- MA20 = {o['ma20']:.2f}（当前价{'上方' if o['last_close'] < o['ma20'] else '下方'} "
        f"{abs(o['last_close'] / o['ma20'] - 1):.1%}）",
        f"- MA60 = {o['ma60']:.2f}（当前价{'上方' if o['last_close'] < o['ma60'] else '下方'} "
        f"{abs(o['last_close'] / o['ma60'] - 1):.1%}）",
        f"- 近 20 日高点/低点：{o['high20']:.2f} / {o['low20']:.2f}",
        "",
        "> 本节为历史数据的统计外推，**不是对未来的预测或保证**，与建议无关，不构成投资建议。",
        "",
    ]
