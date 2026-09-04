"""Meridian 命令行入口。

用法：
    meridian analyze --symbol 600519 [--start 2026-01-01] [--end 2026-08-31]
                     [--output report.md] [--no-persist] [--offline]
    meridian ledger   [--symbol 600519] [--market cn] [--format md|csv|trades]
                      [--limit 200] [--out path]
    meridian journal  --symbol 600519 --side buy [--quantity 100] [--price 1299.0]
                      [--note "跟随 Add 建议"] [--ledger-id 3] [--market cn]
"""

from __future__ import annotations

import argparse
import sys
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

    pipeline = AnalysisPipeline(persist=not args.no_persist)
    result = pipeline.analyze(
        args.symbol, start=args.start, end=args.end, offline=args.offline, name=args.name
    )

    report = result.to_markdown()
    report_path = pipeline.write_report(result, args.output)

    if args.output or not sys.stdout.isatty():
        print(f"报告已写入: {report_path}")
    else:
        print(report)
        print(f"---\n报告已写入: {report_path}")
    return 0


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
    handlers = {"analyze": cmd_analyze, "ledger": cmd_ledger, "journal": cmd_journal}
    try:
        return handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 —— CLI 边界统一转退出码
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
