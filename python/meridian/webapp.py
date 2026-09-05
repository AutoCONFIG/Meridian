"""Meridian Web API（Phase 3 v1，FastAPI 只读服务层）。

职责边界：Web 层只暴露管线已有产物（分析/组合/台账/报告文件），
不新增任何评分逻辑（红线：评分只在 Rust 引擎，AI 只在理解层）。
启动：.venv/Scripts/python -m uvicorn meridian.webapp:app --port 8300
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from meridian.config import AppConfig, MarketsConfig
from meridian.orchestrator.pipeline import AnalysisPipeline

app = FastAPI(
    title="Meridian API",
    description="AI 增强型量化投资研究与决策辅助平台（量化负责可信，AI 负责理解，人负责决策）",
    version="0.1.0",
)


def create_app(pipeline: AnalysisPipeline | None = None) -> FastAPI:
    """应用工厂（测试注入内存库管线）。"""
    app.state.pipeline = pipeline or AnalysisPipeline()
    return app


create_app()  # 默认实例（生产用真实管线；测试用 create_app(pipeline) 覆盖 app.state.pipeline）


class AnalyzeRequest(BaseModel):
    symbol: str
    name: str | None = None
    start: str | None = None
    end: str | None = None
    offline: bool = False


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "meridian"}


@app.get("/api/symbols")
def symbols():
    """标的池（市场分组）。"""
    cfg = MarketsConfig.load()
    return {
        "markets": [
            {
                "market": e.market,
                "asset_type": e.asset_type,
                "frequency": e.frequency,
                "symbols": [{"symbol": s.symbol, "name": s.name} for s in e.symbols],
            }
            for e in cfg.markets
        ]
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    """单标的分析（同步执行；离线模式读本地库）。返回评分摘要 + markdown 报告。"""
    from meridian.research import ResearchTeam

    pipeline: AnalysisPipeline = app.state.pipeline
    try:
        result = pipeline.analyze(
            req.symbol, start=req.start, end=req.end, offline=req.offline, name=req.name
        )
    except Exception as exc:  # noqa: BLE001 —— 边界转 4xx/5xx
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result.research_notes = ResearchTeam().investigate(result)

    report_path = pipeline.write_report(result)
    action = result.score["action"]
    return {
        "symbol": result.symbol,
        "name": result.name,
        "market": result.market,
        "regime": result.regime,
        "opportunity": result.opportunity,
        "risk": result.risk,
        "action": result.action,
        "position_hint": action.get("position_hint"),
        "rule_triggers": action.get("rule_triggers", []),
        "data_source": result.data_source,
        "fundamentals": result.fundamentals,
        "research_notes": [
            {"agent": n.agent, "title": n.title, "body": n.body} for n in result.research_notes
        ],
        "report": result.to_markdown(),
        "report_path": str(report_path),
    }


@app.get("/api/portfolio")
def portfolio(symbols: str | None = Query(default=None, description="逗号分隔，缺省标的池")):
    """组合分析（离线读本地库）。"""
    from meridian.portfolio import PortfolioAnalyzer

    pipeline: AnalysisPipeline = app.state.pipeline
    try:
        out = PortfolioAnalyzer(pipeline).analyze(
            symbols=symbols.split(",") if symbols else None
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    corr = out["correlation"]
    return {
        "rows": [
            {
                "symbol": r.symbol, "name": r.name, "market": r.market,
                "weight": r.weight, "opportunity": r.opportunity, "risk": r.risk,
                "action": r.action, "position_hint": r.position_hint, "regime": r.regime,
            }
            for r in out["rows"]
        ],
        "concentration_hhi": out["concentration_hhi"],
        "effective_holdings": out["effective_holdings"],
        "risk_exposure": out["risk_exposure"],
        "position_suggestion": out["position_suggestion"],
        "correlation": {"symbols": list(corr.columns),
                         "matrix": corr.values.tolist()},
    }


@app.get("/api/ledger")
def ledger(symbol: str | None = None, market: str | None = None, limit: int = 50):
    """决策台账（系统建议留痕，倒序）。"""
    pipeline: AnalysisPipeline = app.state.pipeline
    try:
        entries = pipeline.ledger.entries(market, symbol, limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"entries": entries, "count": len(entries)}


@app.get("/api/reports")
def reports():
    """reports/ 目录报告文件列表（新→旧）。"""
    app_cfg = AppConfig.load()
    files = sorted(
        app_cfg.report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return {
        "reports": [
            {"file": p.name, "path": str(p), "size": p.stat().st_size,
             "modified": p.stat().st_mtime}
            for p in files
        ]
    }


@app.get("/api/reports/{name}")
def report_content(name: str):
    """读取单份报告 markdown。"""
    app_cfg = AppConfig.load()
    path = app_cfg.report_dir / Path(name).name  # 防路径穿越
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"报告不存在: {name}")
    return {"file": name, "content": path.read_text(encoding="utf-8")}
