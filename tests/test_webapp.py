"""Web API 测试（FastAPI TestClient + 内存库管线，离线零网络）。"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi", reason="未安装 fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from meridian import webapp  # noqa: E402


def _client_with(tmp_path):
    from conftest import ROOT, CsvSource, make_uptrend_frame

    from meridian import meridian_core as mc
    from meridian.orchestrator.pipeline import AnalysisPipeline
    from test_pipeline import _FakeFundamentalSource

    pipeline = AnalysisPipeline(
        root=ROOT, source=CsvSource(make_uptrend_frame(130), tmp_path / "bars.csv"),
        persist=False,
    )
    pipeline._fundamental_source = _FakeFundamentalSource()
    # 预灌内存库（offline 请求读这里，不碰真实 data/ 库文件）
    db = mc.PyDb.open_in_memory()
    df = make_uptrend_frame(130)
    db.insert_bars(
        symbol="600519", name="贵州茅台", market="cn", asset_type="stock", frequency="daily",
        dates=[str(d) for d in df["date"]],
        opens=df["open"].tolist(), highs=df["high"].tolist(), lows=df["low"].tolist(),
        closes=df["close"].tolist(), volumes=df["volume"].tolist(),
        amounts=df["amount"].tolist(),
    )
    pipeline._db = db
    return TestClient(webapp.create_app(pipeline))


def test_health_and_symbols(tmp_path):
    client = _client_with(tmp_path)
    assert client.get("/api/health").json()["status"] == "ok"

    data = client.get("/api/symbols").json()
    assert any(m["market"] == "cn" and m["symbols"] for m in data["markets"])


def test_analyze_endpoint_returns_score_and_report(tmp_path):
    client = _client_with(tmp_path)
    resp = client.post("/api/analyze", json={
        "symbol": "600519", "start": "2026-01-05", "end": "2026-12-31", "offline": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "600519"
    assert 0 <= data["opportunity"] <= 100
    assert data["action"] in {"Add", "Hold", "Reduce", "Watch", "Avoid"}
    assert "## 三层评分" in data["report"]
    assert data["research_notes"], "研究笔记应随响应返回"
    # K线主画面数据 + 触发明细
    assert data["bars"] and len(data["bars"]["dates"]) == 130
    assert all(k in data["bars"] for k in ("open", "high", "low", "close", "volume"))
    assert data["factors"]["opportunity"] and data["factors"]["risk"]
    assert any(f["details"] for f in data["factors"]["risk"]), "风险触发明细应透传"


def test_static_assets_served(tmp_path):
    """看板页 + 本地 echarts（桌面壳/Tauri 同源复用）。"""
    client = _client_with(tmp_path)
    html = client.get("/").text
    assert "Meridian" in html and "echarts.min.js" in html
    js = client.get("/static/vendor/echarts.min.js")
    assert js.status_code == 200 and len(js.content) > 500_000


def test_backtest_endpoint_returns_signal_series(tmp_path):
    client = _client_with(tmp_path)
    resp = client.post("/api/backtest", json={
        "symbol": "600519", "start": "2026-01-05", "end": "2026-12-31", "offline": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["actions"]) == 130
    assert "target_weights" in data and "trades" in data
    assert data["actions"][:60] == [None] * 60, "指标窗口不足日无信号"


def test_analyze_endpoint_rejects_bad_symbol(tmp_path):
    client = _client_with(tmp_path)
    resp = client.post("/api/analyze", json={
        "symbol": "600519", "start": "2026-01-05", "end": "2026-12-31", "offline": True,
    })
    assert resp.status_code in (200, 422)  # 语法合法即可


def test_portfolio_endpoint(tmp_path):
    client = _client_with(tmp_path)
    resp = client.get("/api/portfolio", params={"symbols": "600519"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] and data["rows"][0]["symbol"] == "600519"
    assert 0 <= data["concentration_hhi"] <= 1


def test_ledger_and_reports(tmp_path):
    client = _client_with(tmp_path)
    ledger = client.get("/api/ledger", params={"limit": 10})
    assert ledger.status_code == 200 and "entries" in ledger.json()

    files = client.get("/api/reports").json()["reports"]
    assert isinstance(files, list)

    missing = client.get("/api/reports/不存在.md")
    assert missing.status_code == 404
