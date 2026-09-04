"""决策台账（做账）：系统建议自动留痕 + 人工操作补记 → 可导出的做账文档。

三张凭证的关系（详见 docs/LEDGER.md）：
- decision_ledger（append-only）：每次 analyze() 成功即自动追加一行 ——
  记录"系统在何时、基于哪个数据窗口/数据来源、给出什么建议"，附配置指纹；
  事后凭 指纹 + 数据窗口 重跑即可复现该条建议；
- trade_journal（append-only）：实际决策/成交由用户手动补记（本软件不做交易），
  ledger_id 回链台账行，形成"系统建议 → 人工操作"的证据链；
- 做账文档 = export_markdown / export_csv：给人看、可归档、可审计。
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from meridian.orchestrator.pipeline import DATA_SOURCE_LABEL, AnalysisResult
from meridian import meridian_core as mc

# 人工操作允许的方向（实际做了什么，而非系统建议了什么）
TRADE_SIDES = ("buy", "sell", "hold", "watch")
TRADE_SIDES_CN = {"buy": "买入", "sell": "卖出", "hold": "持仓不动", "watch": "观望"}

# "系统建议 × 实际操作" → 一致（其余组合即背离）
CONSISTENT_PAIRS = {
    ("Add", "buy"),
    ("Hold", "hold"),
    ("Reduce", "sell"),
    ("Avoid", "sell"),
    ("Watch", "watch"),
}


class LedgerError(Exception):
    """台账操作错误（非法 side / 回链 id 不存在等）。"""


class LedgerBook:
    """decision_ledger + trade_journal 的读写门面（两表均 append-only）。"""

    def __init__(self, db: mc.PyDb):
        self._db = db

    # ---- 系统侧：分析成功即自动留痕 ----

    def record_analysis(self, result: AnalysisResult, report_path: str | None = None) -> int:
        """一条分析结果 → 台账一行。返回台账 id（append-only，永不覆盖）。"""
        action = result.score["action"]
        return self._db.record_ledger(
            ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol=result.symbol,
            name=result.name,
            market=result.market,
            asset_type=result.asset_type,
            frequency=result.frequency,
            data_start=str(result.start),
            data_end=str(result.end),
            bar_count=int(result.bar_count),
            data_source=result.data_source,
            regime=result.regime,
            opportunity=float(result.opportunity),
            risk=float(result.risk),
            action=result.action,
            rule_triggers=[str(t) for t in action.get("rule_triggers", [])],
            model_version=str(result.score.get("model_version", "")),
            config_fingerprint=str(result.score.get("config_fingerprint", "")),
            fallback_reason=result.fallback_reason,
            position_hint=action.get("position_hint"),
            report_path=report_path,
        )

    # ---- 人工侧：实际操作手动补记 ----

    def record_trade(
        self,
        symbol: str,
        market: str,
        side: str,
        *,
        quantity: float | None = None,
        price: float | None = None,
        note: str | None = None,
        ledger_id: int | None = None,
    ) -> int:
        """补记一笔实际操作。side ∈ {buy, sell, hold, watch}；返回流水 id。"""
        side = side.strip().lower()
        if side not in TRADE_SIDES:
            raise LedgerError(f"side 非法: {side}（应为 {'/'.join(TRADE_SIDES)}）")
        if ledger_id is not None and not self._ledger_exists(ledger_id):
            raise LedgerError(f"回链台账 #{ledger_id} 不存在（先 meridian ledger 查看）")
        return self._db.record_trade(
            ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol=symbol,
            market=market,
            side=side,
            quantity=quantity,
            price=price,
            note=note,
            ledger_id=ledger_id,
        )

    def _ledger_exists(self, ledger_id: int) -> bool:
        return any(e["id"] == ledger_id for e in self._db.ledger_entries(None, None, 10_000))

    # ---- 查询（新→旧）----

    def entries(self, market: str | None = None, symbol: str | None = None, limit: int = 200) -> list[dict]:
        return [dict(e) for e in self._db.ledger_entries(market, symbol, limit)]

    def trades(self, market: str | None = None, symbol: str | None = None, limit: int = 200) -> list[dict]:
        return [dict(e) for e in self._db.trade_entries(market, symbol, limit)]

    # ---- 做账文档导出 ----

    def export_markdown(
        self, out: str | Path, *, market: str | None = None, symbol: str | None = None,
        limit: int = 200,
    ) -> Path:
        """做账文档（Markdown）：系统建议 + 人工操作 + 一致性对照，可归档可审计。"""
        entries = self.entries(market, symbol, limit)
        trades = self.trades(market, symbol, limit)
        ledger_by_id = {e["id"]: e for e in self.entries(None, None, 10_000)}

        scope = f"{symbol}（{market or '全部市场'}）" if symbol else (market or "全部标的")
        lines = [
            "# Meridian 决策台账（做账文档）",
            "",
            f"- 导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 范围：{scope}",
            "- 说明：系统建议由 analyze 自动留痕（append-only，永不改写）；"
            "人工操作经 `meridian journal` 手动补记，ledger_id 回链系统建议。",
            "- 复现方式：同 config_fingerprint 的评分配置 + 表内数据窗口重跑 analyze，"
            "机会/风险/建议应逐位一致。",
            "",
            "## 一、系统建议（decision_ledger）",
            "",
        ]
        if entries:
            lines += [
                "| 台账号 | 记录时刻 | 标的 | 数据窗口 | 来源 | 机会 | 风险 | 建议 | 触发规则 | 指纹 |",
                "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
            ]
            for e in entries:
                window = f"{e['data_start']} ~ {e['data_end']}（{e['bar_count']}根）"
                source = DATA_SOURCE_LABEL.get(e["data_source"], e["data_source"])
                triggers = "；".join(json.loads(e["rule_triggers"])) or "—"
                lines.append(
                    f"| #{e['id']} | {e['ts']} | {e['symbol']} {e['name']} | {window} "
                    f"| {source} | {e['opportunity']:.1f} | {e['risk']:.1f} | {e['action']} "
                    f"| {triggers} | `{e['config_fingerprint']}` |"
                )
            report_refs = [
                f"- #{e['id']} → {_clean(e['report_path'])}"
                for e in entries
                if e.get("report_path")
            ]
            if report_refs:
                lines += ["", "关联分析报告（因子明细证据）：", *report_refs]
        else:
            lines.append("（暂无记录 —— 运行 `meridian analyze` 后自动留痕）")
        lines.append("")

        lines += ["## 二、人工决策与成交（trade_journal）", ""]
        if trades:
            lines += [
                "| 流水号 | 记录时刻 | 标的 | 操作 | 数量 | 价格 | 回链 | 台账建议 | 一致性 | 备注 |",
                "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
            ]
            for t in trades:
                linked = t.get("ledger_id")
                linked_action = ledger_by_id[linked]["action"] if linked in ledger_by_id else None
                consistency = "—"
                if linked_action is not None:
                    consistency = "一致" if (linked_action, t["side"]) in CONSISTENT_PAIRS else "背离"
                lines.append(
                    f"| #{t['id']} | {t['ts']} | {t['symbol']} | {TRADE_SIDES_CN.get(t['side'], t['side'])} "
                    f"| {t['quantity'] if t['quantity'] is not None else '—'} "
                    f"| {t['price'] if t['price'] is not None else '—'} "
                    f"| {f'#{linked}' if linked else '—'} | {linked_action or '—'} "
                    f"| {consistency} | {_clean(t['note'])} |"
                )
        else:
            lines.append("（暂无人工流水 —— 实际下单后经 `meridian journal` 补记，软件不做交易）")
        lines.append("")

        lines += [
            "## 三、纪律",
            "",
            "- 两表均 append-only：行只增不改；补记错误时追加更正流水并在备注注明，不改历史。",
            "- 背离不是错误：系统建议仅供参考，人工是否跟随如实记录即可 —— 台账的价值在于事后可复盘。",
            "",
            "---",
            "",
            "本台账由规则引擎自动生成留痕，仅供个人决策复盘，不构成投资建议。",
            "",
        ]
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def export_csv(
        self, out: str | Path, *, market: str | None = None, symbol: str | None = None,
        limit: int = 5000,
    ) -> Path:
        """系统建议表 → CSV（记账/Excel 消费）。人工流水在 trade_journal，见 export_trades_csv。"""
        entries = self.entries(market, symbol, limit)
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "ledger_id", "ts", "symbol", "name", "market", "asset_type", "frequency",
                "data_start", "data_end", "bar_count", "data_source", "fallback_reason",
                "regime", "opportunity", "risk", "action", "rule_triggers",
                "model_version", "config_fingerprint", "report_path",
            ])
            for e in entries:
                writer.writerow([
                    e["id"], e["ts"], e["symbol"], e["name"], e["market"], e["asset_type"],
                    e["frequency"], e["data_start"], e["data_end"], e["bar_count"],
                    e["data_source"], e["fallback_reason"] or "", e["regime"],
                    e["opportunity"], e["risk"], e["action"], e["rule_triggers"],
                    e["model_version"], e["config_fingerprint"], e["report_path"] or "",
                ])
        return path

    def export_trades_csv(
        self, out: str | Path, *, market: str | None = None, symbol: str | None = None,
        limit: int = 5000,
    ) -> Path:
        """人工操作流水 → CSV。"""
        trades = self.trades(market, symbol, limit)
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["trade_id", "ts", "symbol", "market", "side", "quantity", "price", "note", "ledger_id"])
            for t in trades:
                writer.writerow([
                    t["id"], t["ts"], t["symbol"], t["market"], t["side"],
                    t["quantity"] if t["quantity"] is not None else "",
                    t["price"] if t["price"] is not None else "",
                    t["note"] or "", t["ledger_id"] if t["ledger_id"] is not None else "",
                ])
        return path


def open_ledger(root: Path | None = None) -> LedgerBook:
    """按 config/app.yaml 打开默认库 → LedgerBook（独立进程用，如 CLI journal 命令）。

    注意：与 AnalysisPipeline 同进程使用时应传 pipeline.db() 复用连接，
    避免 DuckDB 文件锁冲突。
    """
    from meridian.config import AppConfig

    app = AppConfig.load(root)
    app.data_dir.mkdir(parents=True, exist_ok=True)
    return LedgerBook(mc.PyDb.open(str(app.data_dir / "meridian.duckdb")))


def _clean(text: str | None) -> str:
    """表格单元格清洗：竖线与换行转义，防破坏 Markdown 表。"""
    if not text:
        return "—"
    return str(text).replace("|", "\\|").replace("\n", " ")
