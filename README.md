# Meridian

**量化负责可信，AI 负责理解，人负责决策。**

Meridian 是一个 AI 增强型量化投资研究与决策辅助平台。评分、建议全部由**可追溯的规则引擎**生成；AI（LLM / 预测模型）只以"信息报告"与"受控通道加分"两种方式参与，永远无法绕过风控或直接给出操作建议。最终决策权在人。

- 完整计划：[docs/PLAN.md](docs/PLAN.md)（含「13. 执行适配记录」）
- 当前阶段：**Phase 0 完成**（工程骨架 + 核心量化闭环 + Python 桥接）

## 架构一览（Phase 0 已实现部分）

```text
python/meridian  (CLI / 编排 / 数据适配)          crates/
  cli.py  ── analyze --symbol 600519 ─┐   core         类型/校验/三层评分定义（26 测试）
  orchestrator/pipeline.py            │   indicators   纯函数指标库（21 测试）
    ├─ data/cn_stock.py  akshare 拉数 │   quant_engine 规则模型+综合引擎（20 测试）
    ├─ meridian.meridian_core  ◄──────┼── pybind      PyO3 桥接（Python 模型可注册）
    └─ reports/*.md  Markdown 报告    │   storage     DuckDB + Parquet Hive 分区（7 测试）
                                      │   backtest    占位（Phase 2）
config/  app / markets / data_sources / scoring/{asset_type}.yaml
```

三层评分：**Opportunity 机会 0-100** ／ **Risk 风险 0-100（独立维度，非扣分项）** ／ **Action 建议**（只能由 `action_rules` 规则匹配生成）。每次评分带 `model_version` + `config_fingerprint`（sha256 前 16 位），落库可追溯。

## 快速开始（Windows 开发机；Linux 同理，工具链换成 gcc/clang）

前置：Rust（MSVC 目标）+ VS Build Tools（C++ 工作负载）、Python 3.12（或 uv）、maturin。

```bash
# 1. Python 环境
python -m venv .venv                       # 或: uv venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python

# 2. 构建 Rust 扩展并挂进 venv（editable）
.venv/Scripts/python -m maturin develop

# 3. 离线测试（不依赖网络）
.venv/Scripts/python -m pytest tests/

# 4. Rust 测试（全 workspace）
cargo test

# 5. 端到端：分析一只 A 股，输出 Markdown 报告
.venv/Scripts/python -m meridian.cli analyze --symbol 600519
#   → reports/600519_<日期>.md，同时落库 data/meridian.duckdb
#   数据源不可用时自动回退本地缓存（报告会标注数据来源）；--offline 强制离线读缓存
```

> 代理提示：akshare 直连国内数据源；若系统代理拦截了 eastmoney，可 `NO_PROXY=*` 后重试。

## 设计红线（改动前必读）

1. **AI 模型只能注册进 Opportunity / Risk 两个评分通道**（`AnalysisModel` trait 架构上保证），action 建议只能由规则匹配生成——AI 不可干预；
2. ResearchAgent 只输出信息报告，**没有评分字段**，与量化结论并排呈现；
3. 指标禁止未来函数：只用 t 及之前数据；窗口不足返回 `None`（不用 NaN）；
4. 软件名 / 权重 / 标的池 / 数据源只存在于 `config/`，代码零硬编码。

## 项目状态与路线图

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 工程骨架 + 规则模型 + 三层评分 + 存储 + Python 桥接 + CLI 报告 | ✅ |
| 1 | RegimeDetector 真实逻辑 / 多市场数据源 / regime_history | 待开始 |
| 2 | 回测引擎（backtest crate）+ LLM Summary Agent | 待开始 |
| 3 | 组合管理 + 基本面模型 + 规则式仓位 | 待开始 |
| 4 | AI 预测模型（批量推理协议已预埋：`analyze(payload)`） | 待开始 |
| 5 | 8 个 Research Agents | 待开始 |
| 6 | FastAPI Web + Tauri v2 桌面客户端（同一套 Web UI） | 待开始 |

## License

MIT
