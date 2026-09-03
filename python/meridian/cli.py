"""Meridian 命令行入口。

用法：
    meridian analyze --symbol 600519 [--start 2026-01-01] [--end 2026-08-31]
                     [--output report.md] [--no-persist] [--offline]
"""

from __future__ import annotations

import argparse
import sys
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
    p_analyze.add_argument("--symbol", required=True, help="标的代码，如 600519（须在 config/markets.yaml 标的池内）")
    p_analyze.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（默认近 240 自然日）")
    p_analyze.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认昨日）")
    p_analyze.add_argument("--output", default=None, help="Markdown 输出路径（缺省打印到 stdout，同时写入 reports/）")
    p_analyze.add_argument("--no-persist", action="store_true", help="不写入本地 DuckDB")
    p_analyze.add_argument(
        "--offline", action="store_true", help="离线模式：跳过数据源，直接读本地 DuckDB 缓存"
    )

    return parser


def cmd_analyze(args: argparse.Namespace) -> int:
    from meridian.orchestrator.pipeline import AnalysisPipeline

    pipeline = AnalysisPipeline(persist=not args.no_persist)
    result = pipeline.analyze(args.symbol, start=args.start, end=args.end, offline=args.offline)

    report = result.to_markdown()
    report_path = pipeline.write_report(result, args.output)

    if args.output or not sys.stdout.isatty():
        print(f"报告已写入: {report_path}")
    else:
        print(report)
        print(f"---\n报告已写入: {report_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            return cmd_analyze(args)
    except Exception as exc:  # noqa: BLE001 —— CLI 边界统一转退出码
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
