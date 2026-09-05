"""Meridian 命令行入口。

用法：
    meridian analyze --symbol 600519 [--start 2026-01-01] [--end 2026-08-31]
                     [--output report.md] [--no-persist] [--offline]
    meridian analyze-all [--market cn] [--start ...] [--end ...] [--offline]
    meridian ledger   [--symbol 600519] [--market cn] [--format md|csv|trades]
                      [--limit 200] [--out path]
    meridian journal  --symbol 600519 --side buy [--quantity 100] [--price 1299.0]
                      [--note "跟随 Add 建议"] [--ledger-id 3] [--market cn]
"""

from __future__ import annotations

import argparse
import sys
import warnings
from datetime import date
from pathlib import Path

from meridian import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meridian",
        description="Meridian — AI 增强型量化投资研究与决策辅助平台（量化负责可信，AI 负责理解，人负责决策）",
    )
    parser.add_argument("--version", action="version", version=f"meridian {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="分析标的并输出 Markdown 报告")
    p_analyze.add_argument("--symbol", required=True, help="标的代码，如 600519 / 00700 / AAPL / RB0（标的池外代码自动识别市场）")
    p_analyze.add_argument("--name", default=None, help="标的名称（可选；标的池内自动带名称）")
    p_analyze.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（默认近 240 自然日）")
    p_analyze.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认昨日）")
    p_analyze.add_argument("--output", default=None, help="Markdown 输出路径（缺省打印到 stdout，同时写入 reports/）")
    p_analyze.add_argument("--no-persist", action="store_true", help="不写入本地 DuckDB（也不留痕决策台账）")
    p_analyze.add_argument(
        "--offline", action="store_true", help="离线模式：跳过数据源，直接读本地 DuckDB 缓存"
    )

    p_analyze_all = sub.add_parser("analyze-all", help="批量分析标的池全部标的：每标的出报告 + 汇总表")
    p_analyze_all.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（默认近 240 自然日）")
    p_analyze_all.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认昨日）")
    p_analyze_all.add_argument("--market", default=None, help="只分析某市场（cn/hk/us，缺省全部）")
    p_analyze_all.add_argument("--no-persist", action="store_true", help="不写入本地 DuckDB（也不留痕）")
    p_analyze_all.add_argument(
        "--offline", action="store_true", help="离线模式：跳过数据源，直接读本地 DuckDB 缓存"
    )

    p_backtest = sub.add_parser("backtest", help="单标的回测：逐日评分策略（action→目标仓位）+ T+1 开盘撮合")
    p_backtest.add_argument("--symbol", required=True, help="标的代码")
    p_backtest.add_argument("--name", default=None, help="标的名称（可选）")
    p_backtest.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（默认近 500 自然日）")
    p_backtest.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认昨日）")
    p_backtest.add_argument("--offline", action="store_true", help="离线模式：只用本地 DuckDB 数据")
    p_backtest.add_argument("--out", default=None, help="报告输出路径（缺省 reports/backtest_<symbol>_<date>.md）")

    p_daily = sub.add_parser("daily", help="日常一条龙：analyze-all + portfolio + 台账导出（定时任务入口）")
    p_daily.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    p_daily.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    p_daily.add_argument("--no-persist", action="store_true", help="不写入本地 DuckDB")
    p_daily.add_argument("--offline", action="store_true", help="离线模式")

    p_portfolio = sub.add_parser("portfolio", help="组合分析：集中度/相关性/风险暴露/规则仓位（离线读本地库）")
    p_portfolio.add_argument("--symbols", default=None, help="逗号分隔的标的列表（缺省用标的池）")
    p_portfolio.add_argument("--market", default=None, help="只看某市场（cn/hk/us，仅标的池模式有效）")

    p_ledger = sub.add_parser("ledger", help="导出决策台账（做账文档）：系统建议留痕 + 人工操作对照")
    p_ledger.add_argument("--symbol", default=None, help="只看某标的")
    p_ledger.add_argument("--market", default=None, help="只看某市场（cn/hk/us）")
    p_ledger.add_argument(
        "--format", choices=("md", "csv", "trades"), default="md",
        help="md=做账文档（系统建议+人工对照，缺省）；csv=系统建议表；trades=人工流水表",
    )
    p_ledger.add_argument("--limit", type=int, default=200, help="最多导出多少条（按时间倒序取最近的）")
    p_ledger.add_argument("--out", default=None, help="输出路径（md 缺省 reports/ledger_日期.md；csv/trades 缺省同目录）")

    p_journal = sub.add_parser("journal", help="补记人工决策/成交（软件不做交易，实际操作由用户手动留痕）")
    p_journal.add_argument("--symbol", required=True, help="标的代码")
    p_journal.add_argument(
        "--side", required=True, choices=("buy", "sell", "hold", "watch"),
        help="实际操作：买入/卖出/持仓不动/观望",
    )
    p_journal.add_argument("--quantity", type=float, default=None, help="成交数量")
    p_journal.add_argument("--price", type=float, default=None, help="成交价格")
    p_journal.add_argument("--note", default=None, help="决策理由/备注（建议如实记录是否跟随系统）")
    p_journal.add_argument("--ledger-id", type=int, default=None, help="回链台账号（meridian ledger 查看，如 3）")
    p_journal.add_argument("--market", default=None, help="市场（缺省从标的池/代码规则推断）")

    return parser


def cmd_analyze(args: argparse.Namespace) -> int:
    from meridian.orchestrator.pipeline import AnalysisPipeline
    from meridian.research import ResearchTeam
    from meridian.summary_agent import SummaryAgent

    pipeline = AnalysisPipeline(persist=not args.no_persist)
    result = pipeline.analyze(
        args.symbol, start=args.start, end=args.end, offline=args.offline, name=args.name
    )

    result.research_notes = ResearchTeam().investigate(result)
    agent = SummaryAgent.from_env()
    if agent.enabled:
        result.ai_summary = agent.summarize(result.to_markdown())

    report = result.to_markdown()
    report_path = pipeline.write_report(result, args.output)

    if args.output or not sys.stdout.isatty():
        print(f"报告已写入: {report_path}")
    else:
        print(report)
        print(f"---\n报告已写入: {report_path}")
    return 0


def render_summary(results, failures) -> str:
    """批量分析汇总（markdown 表）。"""
    from meridian.orchestrator.pipeline import _REGIME_LABEL

    lines = [
        "# Meridian 标的池汇总",
        "",
        "| 代码 | 名称 | 市场 | 状态 | 机会 | 风险 | 建议 |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for r in results:
        lines.append(
            f"| {r.symbol} | {r.name} | {r.market} | {_REGIME_LABEL.get(r.regime, r.regime)} "
            f"| {r.opportunity:.1f} | {r.risk:.1f} | **{r.action}** |"
        )
    if failures:
        lines += ["", f"## 失败（{len(failures)} 个，不挡批量）", ""]
        lines += [f"- {s} {n}：{reason}" for s, n, reason in failures]
    lines += ["", "本表由规则引擎生成，仅供参考，不构成投资建议。"]
    return "\n".join(lines) + "\n"


def cmd_analyze_all(args: argparse.Namespace) -> int:
    from meridian.config import AppConfig
    from meridian.orchestrator.pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline(persist=not args.no_persist)
    results, failures = pipeline.analyze_universe(
        start=args.start, end=args.end, offline=args.offline, market=args.market
    )
    for r in results:
        pipeline.write_report(r)

    today = date.today().isoformat()
    out = AppConfig.load().report_dir / f"summary_{today}.md"
    out.write_text(render_summary(results, failures), encoding="utf-8")

    print(f"{'代码':<10}{'名称':<12}{'市场':<5}{'状态':<6}{'机会':>7}{'风险':>7}  建议")
    for r in results:
        from meridian.orchestrator.pipeline import _REGIME_LABEL

        regime = _REGIME_LABEL.get(r.regime, r.regime)
        print(f"{r.symbol:<10}{r.name:<12}{r.market:<5}{regime:<6}{r.opportunity:>7.1f}{r.risk:>7.1f}  {r.action}")
    for s, n, reason in failures:
        print(f"{s:<10}{n:<12}—— 失败: {reason}")
    print(f"\n汇总已写入: {out}（成功 {len(results)}，失败 {len(failures)}）")
    return 0 if results else 1


def render_backtest_report(result: dict) -> str:
    """回测结果 → markdown（绩效表 + 交易清单 + 净值图引用）。"""
    s = result["symbol"]
    plr = "∞" if result["profit_loss_ratio"] < 0 else f"{result['profit_loss_ratio']:.2f}"
    rows = [
        f"# 回测报告 — {result['name']} ({s})",
        "",
        f"- 回测区间：{result['dates'][0]} ~ {result['dates'][-1]}（{len(result['dates'])} 根日K）",
        f"- 策略：逐日三层评分 → action → 目标仓位（config/backtest.yaml），T+1 开盘撮合",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 总收益率 | {result['total_return']:+.2%} |",
        f"| 年化收益 | {result['annual_return']:+.2%} |",
        f"| 最大回撤 | {result['max_drawdown']:.2%} |",
        f"| 夏普 | {result['sharpe']:.2f} |",
        f"| 期末权益 | {result['final_equity']:,.0f} |",
        "",
        f"交易 {result['trade_count']} 笔，胜率 {result['win_rate']:.0%}，盈亏比 {plr}",
        "",
        "## 交易明细",
        "",
        "| 入场日 | 买入价 | 出场日 | 卖出价 | 数量 | 盈亏 | 收益率 |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ]
    for t in result["trades"]:
        rows.append(
            f"| {t['date_in']} | {t['price_in']:.2f} | {t['date_out']} | {t['price_out']:.2f} "
            f"| {t['shares']:.0f} | {t['pnl']:+,.0f} | {t['pnl_pct']:+.2%} |"
        )
    if not result["trades"]:
        rows.append("| — | — | — | — | — | — | — |")
    rows += [
        "",
        f"![{s} 回测净值](charts/backtest_{s}_{result['dates'][-1]}.png)",
        "",
        "---",
        "",
        "回测基于历史模拟，含滑点/佣金假设，不代表未来表现；不构成投资建议。",
    ]
    return "\n".join(rows) + "\n"


def plot_backtest_equity(result: dict, out_path: Path) -> Path:
    """净值曲线 + 回撤 + 逐日仓位（三面板）。"""
    import matplotlib.pyplot as plt

    from meridian.orchestrator.chart import _apply_cjk_font

    _apply_cjk_font()
    curve = result["equity_curve"]
    dates = [p[0] for p in curve]
    equity = [p[1] for p in curve]
    initial = equity[0]
    peak, drawdown = equity[0], [0.0]
    for e in equity[1:]:
        peak = max(peak, e)
        drawdown.append(e / peak - 1.0)
    weights = [0.0 if (v is None or v != v) else v for v in result["target_weights"]]

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(10.5, 7.5), sharex=True,
        gridspec_kw={"height_ratios": [3, 1, 1], "hspace": 0.08},
    )
    x = range(len(dates))
    ax1.plot(x, equity, color="#1f77b4", linewidth=1.2, label="策略净值")
    ax1.axhline(initial, color="gray", linewidth=0.7, linestyle="--", label="初始资金")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.set_title(
        f"{result['name']} ({result['symbol']}) 回测 · 收益 {result['total_return']:+.1%} · "
        f"回撤 {result['max_drawdown']:.1%} · 夏普 {result['sharpe']:.2f}",
        fontsize=11,
    )
    ax1.set_ylabel("权益（元）")
    ax1.grid(True, linewidth=0.3, alpha=0.5)

    ax2.fill_between(x, drawdown, 0, color="#d62728", alpha=0.4)
    ax2.set_ylabel("回撤")
    ax2.grid(True, linewidth=0.3, alpha=0.5)

    ax3.fill_between(x, weights, step="mid", color="#2ca02c", alpha=0.5)
    ax3.set_ylabel("目标仓位")
    ax3.set_ylim(-0.05, 1.1)
    ax3.grid(True, linewidth=0.3, alpha=0.5)

    step = max(1, len(dates) // 10)
    ticks = list(range(0, len(dates), step))
    ax3.set_xticks(ticks)
    ax3.set_xticklabels([dates[i] for i in ticks], rotation=30, fontsize=8)
    ax3.set_xlim(-1, len(dates))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    return out_path


def cmd_backtest(args: argparse.Namespace) -> int:
    from meridian.config import AppConfig
    from meridian.orchestrator.pipeline import AnalysisPipeline
    from meridian.backtest import ScoreBasedBacktester

    pipeline = AnalysisPipeline(persist=True)
    result = ScoreBasedBacktester(pipeline).run(
        args.symbol, start=args.start, end=args.end, offline=args.offline, name=args.name
    )

    app = AppConfig.load()
    out = Path(args.out) if args.out else (
        app.report_dir / f"backtest_{result['symbol']}_{result['dates'][-1]}.md"
    )
    chart_dir = app.report_dir / "charts"
    png = chart_dir / f"backtest_{result['symbol']}_{result['dates'][-1]}.png"
    try:
        plot_backtest_equity(result, png)
    except Exception as exc:  # noqa: BLE001 —— 图是增强项
        warnings.warn(f"回测图生成失败（报告降级为无图）: {exc}")

    out.write_text(render_backtest_report(result), encoding="utf-8")
    print(
        f"回测 {result['name']} ({result['symbol']}): 收益 {result['total_return']:+.2%}, "
        f"年化 {result['annual_return']:+.2%}, 回撤 {result['max_drawdown']:.2%}, "
        f"夏普 {result['sharpe']:.2f}, 交易 {result['trade_count']} 笔"
    )
    print(f"报告已写入: {out}")
    return 0


def render_portfolio_report(out: dict) -> str:
    """组合分析 → markdown（持仓表 + 相关性矩阵 + 组合指标）。"""
    rows = out["rows"]
    corr = out["correlation"]
    lines = [
        "# Meridian 组合分析",
        "",
        "| 代码 | 名称 | 市场 | 权重 | 状态 | 机会 | 风险 | 建议 | 规则仓位 |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | --- | ---: |",
    ]
    for r in rows:
        hint = "—" if r.position_hint is None else f"{r.position_hint:.0%}"
        lines.append(
            f"| {r.symbol} | {r.name} | {r.market} | {r.weight:.0%} | {r.regime} "
            f"| {r.opportunity:.1f} | {r.risk:.1f} | **{r.action}** | {hint} |"
        )
    hhi = out["concentration_hhi"]
    lines += [
        "",
        "## 组合指标",
        "",
        f"- **集中度 HHI**：{hhi:.3f}（有效持仓数 ≈ {out['effective_holdings']}；"
        f"{'≥0.5 偏集中，考虑分散' if hhi >= 0.5 else '较分散'}）",
        f"- **风险暴露**：加权平均风险分 {out['risk_exposure']:.1f}/100"
        + ("（>65 高暴露，规则建议整体降仓）" if out["risk_exposure"] > 65 else ""),
        f"- **规则仓位建议**：组合加权 {out['position_suggestion']:.0%}"
        + ("（来自各标的 position_hint × 权重；未配置 position 的标的两脚计入 0）" if out["weights_configured"] else ""),
        "",
        "## 收益率相关性（近 120 交易日）",
        "",
    ]
    syms = list(corr.columns)
    header = "| | " + " | ".join(syms) + " |"
    sep = "| --- |" + " ---: |" * len(syms)
    lines.append(header)
    lines.append(sep)
    for s in syms:
        cells = " | ".join(f"{corr.loc[s, t]:+.2f}" for t in syms)
        lines.append(f"| {s} | {cells} |")
    pairs = [
        (syms[i], syms[j], corr.loc[syms[i], syms[j]])
        for i in range(len(syms)) for j in range(i + 1, len(syms))
    ]
    high = [p for p in pairs if p[2] > 0.7]
    if high:
        lines += ["", "**高相关对（>0.7，同涨同跌风险大，注意分散）**："]
        lines += [f"- {a} ↔ {b}：{c:+.2f}" for a, b, c in high]
    lines += [
        "",
        "---",
        "",
        "本报告由规则引擎自动生成，仅供参考，不构成投资建议。",
    ]
    return "\n".join(lines) + "\n"


def cmd_portfolio(args: argparse.Namespace) -> int:
    from meridian.config import AppConfig
    from meridian.orchestrator.pipeline import AnalysisPipeline
    from meridian.portfolio import PortfolioAnalyzer

    pipeline = AnalysisPipeline(persist=True)
    symbols = args.symbols.split(",") if args.symbols else None
    out = PortfolioAnalyzer(pipeline).analyze(symbols=symbols, market=args.market)

    report = render_portfolio_report(out)
    today = date.today().isoformat()
    path = AppConfig.load().report_dir / f"portfolio_{today}.md"
    path.write_text(report, encoding="utf-8")

    for r in out["rows"]:
        hint = "—" if r.position_hint is None else f"{r.position_hint:.0%}"
        print(f"{r.symbol:<10}{r.name:<12}权重 {r.weight:>5.0%}  风险 {r.risk:>5.1f}  {r.action:<7} 仓位 {hint}")
    print(
        f"\nHHI {out['concentration_hhi']:.3f}（有效持仓 {out['effective_holdings']}）"
        f"，风险暴露 {out['risk_exposure']:.1f}/100，组合仓位建议 {out['position_suggestion']:.0%}"
    )
    print(f"报告已写入: {path}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    """日常一条龙（定时任务入口）：批量分析 → 组合分析 → 台账导出。

    调度由系统任务计划程序/cron 触发本命令（见 DEVLOG「常用命令」）。
    """
    ns_all = argparse.Namespace(
        start=args.start, end=args.end, market=None,
        no_persist=args.no_persist, offline=args.offline,
    )
    ns_pf = argparse.Namespace(symbols=None, market=None)
    ns_led = argparse.Namespace(symbol=None, market=None, format="md", limit=500, out=None)

    rc1 = cmd_analyze_all(ns_all)
    rc2 = cmd_portfolio(ns_pf)
    rc3 = cmd_ledger(ns_led)
    return max(rc1, rc2, rc3)


def cmd_ledger(args: argparse.Namespace) -> int:
    from meridian.config import AppConfig
    from meridian.ledger import open_ledger

    app = AppConfig.load()
    book = open_ledger()
    today = date.today().isoformat()
    kw = {"market": args.market, "symbol": args.symbol, "limit": args.limit}

    if args.format == "md":
        out = Path(args.out) if args.out else app.report_dir / f"ledger_{today}.md"
        path = book.export_markdown(out, **kw)
        n_sys = len(book.entries(args.market, args.symbol, 10_000))
        n_man = len(book.trades(args.market, args.symbol, 10_000))
        print(f"做账文档已写入: {path}（系统建议 {n_sys} 条，人工流水 {n_man} 笔）")
    elif args.format == "csv":
        out = Path(args.out) if args.out else app.report_dir / f"ledger_{today}.csv"
        path = book.export_csv(out, **kw)
        print(f"系统建议表已写入: {path}")
    else:
        out = Path(args.out) if args.out else app.report_dir / f"trades_{today}.csv"
        path = book.export_trades_csv(out, **kw)
        print(f"人工流水表已写入: {path}")
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    from meridian.config import MarketsConfig
    from meridian.ledger import open_ledger

    market = args.market
    if market is None:
        market = MarketsConfig.load().find_or_auto(args.symbol)[0].market
    book = open_ledger()
    trade_id = book.record_trade(
        args.symbol, market, args.side,
        quantity=args.quantity, price=args.price, note=args.note, ledger_id=args.ledger_id,
    )
    linked = f"（回链台账 #{args.ledger_id}）" if args.ledger_id else ""
    print(f"已补记流水 #{trade_id}：{args.symbol} {args.side}{linked} —— 台账见 meridian ledger")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "analyze": cmd_analyze,
        "analyze-all": cmd_analyze_all,
        "backtest": cmd_backtest,
        "portfolio": cmd_portfolio,
        "daily": cmd_daily,
        "ledger": cmd_ledger,
        "journal": cmd_journal,
    }
    try:
        return handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 —— CLI 边界统一转退出码
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
