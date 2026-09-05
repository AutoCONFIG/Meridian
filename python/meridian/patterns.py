"""K线形态识别（经典单根/双根/三根形态，纯 pandas 规则，无 TA-Lib 依赖）。

只识别与标注形态本身，不预测涨跌（形态的统计含义写进 desc，由读者自行判断）。
阈值默认值来自通用 TA 教材口径（实体/影线比例），可在识别函数参数覆盖。
"""

from __future__ import annotations

import pandas as pd


def _body(o: pd.Series, c: pd.Series) -> pd.Series:
    return (c - o).abs()


def detect_patterns(df: pd.DataFrame) -> list[dict]:
    """识别常见K线形态 → [{date, name, bias, desc}]（按时间序）。

    bias: bullish（偏多形态）/ bearish（偏空形态）/ neutral（中性）。
    只做形态几何判定；不结合前后趋势确认，不构成信号。
    """
    if df is None or len(df) < 3:
        return []
    o, c = df["open"].astype(float), df["close"].astype(float)
    h, l = df["high"].astype(float), df["low"].astype(float)
    dates = [str(d) for d in df["date"]]
    body = (c - o).abs()
    rng = (h - l).replace(0, pd.NA)
    up_shadow = h - pd.concat([o, c], axis=1).max(axis=1)
    low_shadow = pd.concat([o, c], axis=1).min(axis=1) - l
    bull = c > o
    bear = c < o

    found: list[dict] = []

    def add(i: int, name: str, bias: str, desc: str):
        found.append({"date": dates[i], "name": name, "bias": bias, "desc": desc})

    for i in range(2, len(df)):
        b, r = body.iloc[i], rng.iloc[i]
        if pd.isna(r) or r <= 0:
            continue
        us, ls = up_shadow.iloc[i], low_shadow.iloc[i]

        # ---- 单根形态 ----
        if b / r < 0.1:
            add(i, "十字星", "neutral", "开收几乎持平，多空拉锯；出现在趋势末端常被视为转折预警")
        if b / r > 0.9:
            add(i, "光头光脚", "bullish" if bull.iloc[i] else "bearish",
                "开收即最高/最低，一方全天主导" + ("（阳线强势）" if bull.iloc[i] else "（阴线强势）"))
        if ls >= 2 * b and us <= 0.5 * b:
            add(i, "锤子线", "bullish", "下影远长于实体，盘中有大幅下探被买回；低位出现偏多，高位为上吊线需警惕")
        if us >= 2 * b and ls <= 0.5 * b:
            add(i, "流星线", "bearish", "上影远长于实体，冲高大幅回落；高位出现偏空")

        # ---- 双根形态 ----
        if i >= 1:
            pb, pc, po = body.iloc[i - 1], c.iloc[i - 1], o.iloc[i - 1]
            engulf = b > pb * 1.05 and min(o.iloc[i], c.iloc[i]) <= min(po, pc) and max(o.iloc[i], c.iloc[i]) >= max(po, pc)
            if engulf and bull.iloc[i] and bear.iloc[i - 1]:
                add(i, "阳包阴（吞没）", "bullish", "阳线实体完全包住前一根阴线，买方反夺主导权")
            elif engulf and bear.iloc[i] and bull.iloc[i - 1]:
                add(i, "阴包阳（吞没）", "bearish", "阴线实体完全包住前一根阳线，卖方反夺主导权")

        # ---- 三根形态 ----
        c1, c2, c3 = c.iloc[i - 2], c.iloc[i - 1], c.iloc[i]
        o1, o3 = o.iloc[i - 2], o.iloc[i]
        small_mid = body.iloc[i - 1] < body.iloc[i - 2] * 0.5
        if (
            c.iloc[i - 2] < o.iloc[i - 2] and small_mid and bull.iloc[i]
            and c3 > (o.iloc[i - 2] + c.iloc[i - 2]) / 2 and b > body.iloc[i - 1] * 1.5
        ):
            add(i, "启明星", "bullish", "阴线—小实体—阳线三连，跌势衰竭转升的底部形态")
        if (
            c.iloc[i - 2] > o.iloc[i - 2] and small_mid and bear.iloc[i]
            and c3 < (o.iloc[i - 2] + c.iloc[i - 2]) / 2 and b > body.iloc[i - 1] * 1.5
        ):
            add(i, "黄昏之星", "bearish", "阳线—小实体—阴线三连，涨势衰竭转跌的顶部形态")
        if (
            bull.iloc[i] and bull.iloc[i - 1] and bull.iloc[i - 2]
            and c3 > c2 > c1 and o3 > o.iloc[i - 1] > o1 and b > body.iloc[i - 2] * 0.6
        ):
            add(i, "红三兵", "bullish", "三连阳逐级抬升，买方持续主导")
        if (
            bear.iloc[i] and bear.iloc[i - 1] and bear.iloc[i - 2]
            and c3 < c2 < c1 and o3 < o.iloc[i - 1] < o1 and b > body.iloc[i - 2] * 0.6
        ):
            add(i, "三只乌鸦", "bearish", "三连阴逐级下探，卖方持续主导")

    return found
