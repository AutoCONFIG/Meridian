# Meridian

**量化负责可信，AI 负责理解，人负责决策。**

Meridian 是一个 AI 增强型量化投资研究与决策辅助平台。评分、建议全部由**可追溯的规则引擎**生成；AI（LLM 摘要 / 预测模型）只以"信息报告"与"受控通道加分"两种方式参与，永远无法绕过风控或直接给出操作建议。最终决策权在人。

- 完整计划：[docs/PLAN.md](docs/PLAN.md)（含「13. 执行适配记录」）
- **开发台账（当前状态/交接/坑）**：[docs/DEVLOG.md](docs/DEVLOG.md) ← 接手前必读
- 当前阶段：**Phase 0-3 主体完成**（详见下方状态表）

## 现在就能用

```bash
# Web 看板（K线主画面：建议色带/交易标记/触发明细）
.venv/Scripts/python -m uvicorn meridian.webapp:app --port 8300
#   → 浏览器打开 http://127.0.0.1:8300 （桌面版 = 同一页面套 Tauri 壳）

# 命令行
.venv/Scripts/python -m meridian.cli analyze  --symbol 600519          # 单标的报告
.venv/Scripts/python -m meridian.cli analyze-all                      # 批量分析标的池
.venv/Scripts/python -m meridian.cli backtest --symbol 600519         # 逐日评分回测
.venv/Scripts/python -m meridian.cli portfolio                        # 组合分析（集中度/相关性/仓位）
.venv/Scripts/python -m meridian.cli daily                            # 一条龙（定时任务入口）
.venv/Scripts/python -m meridian.cli ledger                           # 决策台账导出
```

桌面可执行：`desktop/src-tauri/target/debug/meridian-desktop.exe`（窗口自动拉起/回收后端；安装包 `bundle/nsis/Meridian_0.1.0_x64-setup.exe`；macOS/Linux 用同一代码 `npx tauri build` 出 .dmg/.AppImage）。

## 架构

```text
python/meridian                          crates/（Rust，依赖 core ← indicators ← quant_engine）
  cli.py            命令入口                core         类型/三层评分定义/Regime（27 测试）
  orchestrator/     分析管线（拉数→评分→报告） indicators  纯函数指标库（21）
  data/             多渠道数据层           quant_engine 规则模型+综合引擎+regime检测（28）
    cn_stock/global_stock/futures/index/  storage     DuckDB（bars/评分/台账/regime/基本面，14）
    fundamentals/sync/realtime/...        backtest    事件驱动回测（6）
  backtest.py       逐日评分回测           pybind      PyO3 桥（模型/检测器/回测全暴露）
  portfolio.py      组合分析
  research.py       Research Agents（无评分）
  summary_agent.py  LLM 摘要（无评分无建议）
  webapp.py         FastAPI 只读服务层 + 看板页
  models/forecast.py AI 预测模型（经桥接进评分通道）
config/  app/markets/data_sources/scoring/{asset_type}/regime/backtest/models/portfolio.yaml
desktop/ Tauri 2 壳（Windows/macOS/Linux）
```

**三层评分**：Opportunity 机会 0-100 ／ Risk 风险 0-100（独立维度）／ Action 建议（只能由 `action_rules` 生成，可带 `position` 规则仓位）。每次评分带 `model_version` + `config_fingerprint`（sha256 前 16 位）落库可追溯。报告五层结构：结论 → AI 摘要（可选）→ 研究视角 → 三层评分+触发明细 → 基本面。

## 快速开始（Windows 开发机；Linux/macOS 工具链换 gcc/clang、路径换 /）

前置：Rust（MSVC 目标）+ VS Build Tools（C++ 工作负载）、Python 3.12（或 uv）、maturin、Node 18+（仅桌面壳需要）。

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m maturin develop --release   # 构建 Rust 扩展挂进 venv
.venv/Scripts/python -m pytest tests/               # Python 107 测试（离线）
cargo test                                          # Rust 96 测试
cd desktop && npm install && npx tauri build        # 桌面壳（可选）
```

> 代理提示：国内数据源直连，系统代理拦截时加 `NO_PROXY=*`。

## 设计红线（改动前必读，违反=返工）

1. **AI 模型只能注册进 Opportunity / Risk 两个评分通道**（`AnalysisModel` trait 架构保证），action 建议只能由规则匹配生成——AI 不可干预；
2. ResearchAgent / SummaryAgent 只输出信息文本，**没有评分字段**；
3. 指标禁止未来函数：只用 t 及之前数据；窗口不足返回 `None`（不用 NaN）；
4. 软件名 / 权重 / 阈值 / 标的池 / 数据源只存在于 `config/`，代码零硬编码；
5. 评分可追溯：factors + model_version + config_fingerprint 必须落库。

## 项目状态与路线图

| Phase | 内容 | 状态 |
| --- | --- | --- |
| 0 | 工程骨架 + 规则模型 + 三层评分 + 存储 + Python 桥接 + CLI 报告 | ✅ |
| 1 | 多渠道数据层 + regime 检测（trend_vol_v1+指数输入）+ 权重档 + regime_history | ✅ |
| 2 | 事件驱动回测（T+1 撮合/成本/绩效）+ LLM Summary Agent | ✅ |
| 3 | 规则仓位 + 组合分析 + Research Agents + 基本面速览 + AI 预测脚手架 + FastAPI 看板 | ✅ 主体 |
| 3 剩余 | fundamental_model 评分接入（需 AnalysisContext 扩展）、消息面/行业 Agent 扩展 | 待开始 |
| 6 | Tauri 桌面壳 | ✅ Windows（macOS/Linux 待对应机器打包）；正式分发需 PyInstaller sidecar |
| 4/5 | 批量推理 AI 预测模型、8 个 Research Agents | 部分（脚手架+3 Agent 已就位） |

**接手必读**：[docs/DEVLOG.md](docs/DEVLOG.md) 的「交接清单」——未完成事项的起点提示、环境坑、并发会话协作规则。

## License

MIT
