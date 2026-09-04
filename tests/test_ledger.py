"""决策台账（做账）测试：自动留痕 / 人工补记 / 回链校验 / 做账文档导出。

全部离线：合成K线 + 内存库，不发起网络请求。
"""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import ROOT, CsvSource, make_uptrend_frame
from meridian import meridian_core as mc
from meridian.ledger import LedgerBook, LedgerError, open_ledger
from meridian.orchestrator.pipeline import AnalysisPipeline


def _pipeline_with_ledger(tmp_path, persist=True) -> AnalysisPipeline:
    """CSV 合成源 + 预灌内存库的管线（离线分析走 cache 路径）。"""
    pipeline = AnalysisPipeline(
        root=ROOT, source=CsvSource(make_uptrend_frame(130), tmp_path / "bars.csv"),
        persist=persist,
    )
    db = mc.PyDb.open_in_memory()
    df = make_uptrend_frame(130)
    db.insert_bars(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock", frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(), lows=df["low"].tolist(),
        closes=df["close"].tolist(), volumes=df["volume"].tolist(), amounts=df["amount"].tolist(),
    )
    pipeline._db = db
    return pipeline


def _result(pipeline: AnalysisPipeline):
    return pipeline.analyze("600519", start="2026-01-05", end="2026-12-31", offline=True)


def _book() -> LedgerBook:
    return LedgerBook(mc.PyDb.open_in_memory())


# ---- LedgerBook：留痕 / 补记 / 回链 ----


def test_record_analysis_appends_and_reads_back(tmp_path):
    pipeline = _pipeline_with_ledger(tmp_path, persist=True)
    result = _result(pipeline)  # persist=True → analyze 内部已自动留痕

    entries = pipeline.ledger.entries()
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == 1
    assert e["symbol"] == "600519" and e["name"] == "贵州茅台"
    assert e["market"] == "cn" and e["asset_type"] == "stock"
    assert e["data_source"] == "cache"  # offline=True
    assert e["bar_count"] == 130
    assert e["action"] == result.action
    assert e["opportunity"] == pytest.approx(result.opportunity)
    assert e["risk"] == pytest.approx(result.risk)
    assert e["config_fingerprint"] == result.score["config_fingerprint"]
    assert json.loads(e["rule_triggers"]) == result.score["action"]["rule_triggers"]
    assert e["ts"]  # 记录时刻已写


def test_ledger_is_append_only_ids_never_reused(tmp_path):
    pipeline = _pipeline_with_ledger(tmp_path)
    result = _result(pipeline)  # 自动留痕 #1
    id2 = pipeline.ledger.record_analysis(result)  # 显式再记一条

    assert id2 == 2
    assert [e["id"] for e in pipeline.ledger.entries()] == [2, 1]  # 倒序：新在前
    assert pipeline.ledger.entries(symbol="600519") == pipeline.ledger.entries()
    assert pipeline.ledger.entries(symbol="AAPL") == []


def test_record_trade_rejects_bad_side_and_broken_link(tmp_path):
    book = _book()
    with pytest.raises(LedgerError, match="side 非法"):
        book.record_trade("600519", "cn", "allin")
    with pytest.raises(LedgerError, match="不存在"):
        book.record_trade("600519", "cn", "buy", ledger_id=99)


def test_record_trade_and_link_consistency(tmp_path):
    pipeline = _pipeline_with_ledger(tmp_path, persist=True)
    _result(pipeline)  # 自动留痕 #1
    ledger_id = pipeline.ledger.entries()[0]["id"]

    book = pipeline.ledger
    trade_id = book.record_trade(
        "600519", "cn", "buy", quantity=100.0, price=1299.16,
        note="跟随建议", ledger_id=ledger_id,
    )
    trades = book.trades()
    assert len(trades) == 1
    t = trades[0]
    assert t["id"] == trade_id
    assert t["side"] == "buy" and t["quantity"] == 100.0 and t["price"] == 1299.16
    assert t["ledger_id"] == ledger_id
    assert t["note"] == "跟随建议"

    # 回链的台账建议确实是 Add（合成强势序列），buy 与 Add 一致
    ledger_by_id = {e["id"]: e for e in book.entries()}
    assert ledger_by_id[ledger_id]["action"] == "Add"


# ---- 管线集成：analyze 自动留痕 ----


def test_pipeline_analyze_auto_records_when_persist(tmp_path):
    pipeline = _pipeline_with_ledger(tmp_path, persist=True)
    _result(pipeline)

    entries = pipeline.ledger.entries()
    assert len(entries) == 1
    assert entries[0]["symbol"] == "600519"


def test_pipeline_no_persist_records_nothing(tmp_path):
    """--no-persist 语义：不写库，自然不留痕。"""
    pipeline = _pipeline_with_ledger(tmp_path, persist=False)
    _result(pipeline)
    assert pipeline.ledger.entries() == []


def test_pipeline_ledger_failure_warns_but_does_not_block(tmp_path):
    """台账写失败只告警，分析结果照常返回。"""
    pipeline = _pipeline_with_ledger(tmp_path, persist=True)

    class BrokenBook:
        def record_analysis(self, result):
            raise RuntimeError("模拟台账故障")

    import meridian.orchestrator.pipeline as pl

    original = pl.AnalysisPipeline.ledger
    pl.AnalysisPipeline.ledger = property(lambda self: BrokenBook())
    try:
        with pytest.warns(UserWarning, match="决策台账写入失败"):
            result = _result(pipeline)
    finally:
        pl.AnalysisPipeline.ledger = original

    assert result.symbol == "600519" and result.bar_count == 130


# ---- 做账文档导出 ----


def _seed_add_entry(book: LedgerBook, ts: str, symbol: str, name: str, market: str) -> None:
    book._db.record_ledger(
        ts=ts, symbol=symbol, name=name, market=market,
        asset_type="stock", frequency="daily",
        data_start="2026-05-01", data_end="2026-09-02", bar_count=130,
        data_source="store", regime="unknown",
        opportunity=72.3, risk=35.1, action="Add",
        rule_triggers=["机会>=70", "风险<=40"],
        model_version="rule-v0.1", config_fingerprint="abcdef0123456789",
        report_path="reports/x.md",
    )


def test_export_markdown_contains_both_ledgers_and_consistency(tmp_path):
    book = _book()
    _seed_add_entry(book, "2026-09-03 15:30:00", "600519", "贵州茅台", "cn")
    book._db.record_ledger(
        ts="2026-09-04 15:30:00", symbol="00700", name="腾讯控股", market="hk",
        asset_type="stock", frequency="daily",
        data_start="2026-05-01", data_end="2026-09-03", bar_count=128,
        data_source="live", regime="unknown",
        opportunity=45.0, risk=55.0, action="Watch",
        rule_triggers=[], model_version="rule-v0.1", config_fingerprint="abcdef0123456789",
    )
    book.record_trade("600519", "cn", "buy", quantity=100.0, price=1299.16,
                      note="跟随 Add", ledger_id=1)
    book.record_trade("00700", "hk", "buy", quantity=200.0, price=433.0,
                      note="逆建议抄底", ledger_id=2)

    path = book.export_markdown(tmp_path / "ledger.md")
    text = path.read_text(encoding="utf-8")

    assert "# Meridian 决策台账（做账文档）" in text
    assert "600519 贵州茅台" in text and "00700 腾讯控股" in text
    assert "abcdef0123456789" in text  # 指纹可追溯
    assert "机会>=70；风险<=40" in text
    assert "reports/x.md" in text
    # 一致性对照：buy×Add=一致；buy×Watch=背离
    assert "一致" in text and "背离" in text
    assert "跟随 Add" in text and "逆建议抄底" in text


def test_export_markdown_empty_ledgers(tmp_path):
    book = _book()
    path = book.export_markdown(tmp_path / "empty.md")
    text = path.read_text(encoding="utf-8")
    assert "暂无记录" in text and "暂无人工流水" in text


def test_export_csv_roundtrip(tmp_path):
    book = _book()
    _seed_add_entry(book, "2026-09-04 15:30:00", "600519", "贵州茅台", "cn")
    book.record_trade("600519", "cn", "sell", quantity=100.0, price=1300.0)

    import csv as _csv

    assert book.export_csv(tmp_path / "ledger.csv").exists()
    assert book.export_trades_csv(tmp_path / "trades.csv").exists()

    with (tmp_path / "ledger.csv").open(encoding="utf-8-sig") as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519" and rows[0]["action"] == "Add"
    assert rows[0]["report_path"] == "reports/x.md"

    with (tmp_path / "trades.csv").open(encoding="utf-8-sig") as f:
        trows = list(_csv.DictReader(f))
    assert len(trows) == 1 and trows[0]["side"] == "sell"


# ---- CLI 闭环 ----


def test_cli_journal_and_ledger_roundtrip(tmp_path, monkeypatch):
    """journal 补记 → ledger 导出：CLI 闭环冒烟（项目根 monkeypatch 到 tmp_path）。"""
    shutil.copytree(ROOT / "config", tmp_path / "config")

    import meridian.config as mcfg
    from meridian import cli

    monkeypatch.setattr(mcfg, "_project_root", lambda: tmp_path)

    # 预置台账 #1（直接写库后释放连接，避免同文件双开）
    book = open_ledger(tmp_path)
    _seed_add_entry(book, "2026-09-04 15:30:00", "600519", "贵州茅台", "cn")
    del book

    # 回链存在的台账 → 成功
    assert cli.main(["journal", "--symbol", "600519", "--side", "buy",
                     "--quantity", "100", "--price", "1299.16",
                     "--note", "跟随 Add 建议", "--ledger-id", "1"]) == 0
    # 回链不存在的台账号 → 退出码 1
    assert cli.main(["journal", "--symbol", "600519", "--side", "sell",
                     "--ledger-id", "42"]) == 1
    # 导出做账文档：含系统建议 + 人工流水 + 一致性
    out = tmp_path / "reports" / "out.md"
    assert cli.main(["ledger", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "跟随 Add 建议" in text and "一致" in text
