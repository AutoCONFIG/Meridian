"""报告配图：K线 + MA20/MA60 + BOLL(20,2) + 成交量。

纯展示层：指标用 pandas 现算现画，不参与评分链路（评分仍走 Rust indicators，
红线 5 的可追溯性不受影响）。生成失败由调用方捕获降级为无图报告。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无头渲染，写文件不出窗

# 中文字体回退链：Windows/Linux/macOS 常见中文字体，逐个探测已安装项
_CJK_FONTS = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "PingFang SC",
    "WenQuanYi Micro Hei",
    "Arial Unicode MS",
]

# A股配色习惯：红涨绿跌
_UP_COLOR = "#d62728"
_DOWN_COLOR = "#2ca02c"
_BOLL_COLOR = "#1f77b4"
_MA20_COLOR = "#ff7f0e"
_MA60_COLOR = "#9467bd"


def plot_daily_chart(df, symbol: str, name: str, out_path: Path) -> Path:
    """画日K组合图（上：蜡烛+均线+BOLL，下：成交量），保存 PNG。

    df 需含 date/open/high/low/close/volume 列，按日期升序；窗口不足的均线自动缺省。
    """
    import matplotlib.pyplot as plt

    _apply_cjk_font()

    n = len(df)
    x = range(n)
    opens, closes = df["open"].to_numpy(), df["close"].to_numpy()
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    dates = [str(d) for d in df["date"]]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10.5, 6.2), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.06},
    )

    # ---- 上图：蜡烛 ----
    up = closes >= opens
    for mask, color in ((up, _UP_COLOR), (~up, _DOWN_COLOR)):
        if mask.any():
            ax1.vlines([i for i in x if mask[i]], lows[mask], highs[mask],
                       color=color, linewidth=0.7)
            ax1.bar([i for i in x if mask[i]], closes[mask] - opens[mask],
                    bottom=opens[mask], width=0.65, color=color, zorder=3)

    close_s = df["close"]
    ma20 = close_s.rolling(20).mean()
    ma60 = close_s.rolling(60).mean()
    std20 = close_s.rolling(20).std()
    boll_mid, boll_up, boll_low = ma20, ma20 + 2 * std20, ma20 - 2 * std20

    ax1.fill_between(x, boll_low, boll_up, color=_BOLL_COLOR, alpha=0.10, linewidth=0,
                     label="BOLL(20,2)")
    ax1.plot(x, boll_mid, color=_BOLL_COLOR, linewidth=0.9, label="BOLL中轨=MA20")
    ax1.plot(x, ma20, color=_MA20_COLOR, linewidth=1.0, label="MA20")
    ax1.plot(x, ma60, color=_MA60_COLOR, linewidth=1.0, label="MA60")

    ax1.set_title(f"{name} ({symbol}) 日K · {dates[0]} ~ {dates[-1]} · MA / BOLL(20,2)",
                  fontsize=11)
    ax1.legend(loc="upper left", fontsize=8, ncol=4, framealpha=0.6)
    ax1.grid(True, linewidth=0.3, alpha=0.5)
    ax1.set_ylabel("价格")

    # ---- 下图：成交量 ----
    volume = df["volume"].to_numpy()
    for mask, color in ((up, _UP_COLOR), (~up, _DOWN_COLOR)):
        if mask.any():
            ax2.bar([i for i in x if mask[i]], volume[mask], width=0.65,
                    color=color, alpha=0.75)
    ax2.set_ylabel("成交量")
    ax2.grid(True, linewidth=0.3, alpha=0.5)

    # x 轴日期刻度（约 10 个，防重叠）
    step = max(1, n // 10)
    ticks = list(range(0, n, step))
    ax2.set_xticks(ticks)
    ax2.set_xticklabels([dates[i] for i in ticks], rotation=30, fontsize=8)
    ax2.set_xlim(-1, n)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _apply_cjk_font() -> str:
    """选一个已安装的中文字体（找不到则回退默认，负号显示单独修复）。"""
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((f for f in _CJK_FONTS if f in installed), None)
    matplotlib.rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"] if chosen else ["DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    return chosen or ""
