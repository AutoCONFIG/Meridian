# Meridian 开发台账（DEVLOG）

> **本文档是给后续维护者（人或 AI）看的开发账本。**
> 结构：**状态快照 → 当前阶段任务（看板）→ 开发日记（按天）→ 参考区**（环境备忘/常用命令/架构红线）。
> 约定：每完成一项开发——任务看板打勾、日记当天小节追加一段、状态快照更新；动红线或踩环境坑必须记。完整计划见 [PLAN.md](PLAN.md)。

## 状态快照（每次提交后更新）

- **分支**：`main`（远程 `origin` = github.com:AutoCONFIG/Meridian.git）
- **最新提交**：`c8d0be9` 报告改造 C K线配图（2026-09-04）
- **测试基线**：Rust 81（core 27 + indicators 21 + quant_engine 21 + storage 12）+ Python 63，全绿
- **数据状态**：DuckDB（`data/meridian.duckdb`）已入库标的：600519 / 300750 / 600547 / 002475 / 00700 / AAPL / RB0 / 601318 / 300059（日K到 2026-09-03）
- **当前阶段**：Phase 0 完成，报告可读性改造三项全部完成；下一步 Phase 1（regime 检测真实逻辑）
- **并行会话**：另一会话在做 Python 层 LedgerBook 门面 + CLI 台账命令（`python/meridian/ledger.py` / `tests/test_ledger.py` / `cli.py` / storage WIP，未提交）；各自提交时严格只含自己的文件

## 当前阶段任务

### ✅ 已完成

- [x] **Phase 0：工程骨架 + 核心量化闭环**（`89a25bf`，09-03）——Rust 六 crate、三层评分、DuckDB、pyo3 桥、四组配置 YAML
- [x] **多渠道数据层**（`68e06e9`，09-04）——MultiDailySource failover、跨源对账、增量同步、港美/期货源
- [x] **离线回退 + 市场路由 + 即兴分析**（`500d921`/`bee2212`/`af3eb86`）——缓存回退、按 (market, asset_type) 路由、标的池外自动识别
- [x] **决策台账存储层**（`82b0cca`）——analysis_ledger / trade_journal 表（另一会话 WIP 由本会话收尾）
- [x] **报告可读性改造**（`2330aaf`/`ddf6b0e`/`c8d0be9`）
  - [x] A：模型内部触发明细透传 + 「触发原因」表（回答"分数为什么"）
  - [x] B：结论先行摘要段（机会方向一览 + 风险 Top 触发 + 综合一句话）
  - [x] C：K线 + MA20/MA60 + BOLL + 成交量配图嵌入报告

### 🔨 进行中（并行会话）

- [ ] Python 层 `LedgerBook` 门面 + CLI 台账命令（查询/导出做账文档）——见日记 09-04「并行动态」

### 📋 待办（按 PLAN.md 路线）

- [ ] **Phase 1：市场状态检测**——RegimeDetector 真实逻辑（替换 NullDetector 恒 unknown）、regime_history 落库
- [ ] **Phase 2：回测 + AI 摘要**——回测引擎激活（backtest crate 占位中）、LLM Summary Agent（把规则报告转译成人话，不产分数不碰 action）
- [ ] **Phase 3+**：组合管理、基本面数据、AI 预测模型（只进 Opportunity/Risk 通道）、Research Agents、FastAPI Web + Tauri
- [ ] （低优先）Mimosa 完整安全审计补跑（钩子一直提示"扫描未完整"）

## 开发日记

### 2026-09-04

今天主线是**数据韧性 + 报告可读性**，从"能跑"推进到"能看懂"。

- **多渠道数据层**（另一会话完成，`68e06e9`）：`MultiDailySource` 链式 failover + 跨源收盘价对账（>1% 判脏）+ `SourceHealth` 失败冷却；腾讯港美日K、akshare 期货；`DailySyncer` 增量同步（库内最新日期为游标）。期货独立 scoring yaml，不再错套股票权重。
- **离线回退 + 市场路由**（`500d921`/`8e1e9d2`/`bee2212`）：东财间歇断连期间全靠这套撑住——数据源失败自动回退本地 DuckDB，报告标注数据来源与回退原因；CLI `--offline`；修复 00700/AAPL/RB0 错走 A 股链的路由 bug（改为按 `(market, asset_type)` 惰性路由）。
- **即兴分析**（`af3eb86`）：响应"看一只股票还要改配置？"的反馈——`find_or_auto` 按代码模式自动识别市场（6位0/3/6→cn股、5位→hk、纯字母→us、字母+数字→期货），CLI 加 `--name`。002475 立讯精密入池（`572beaf`）。
- **决策台账存储层**（`82b0cca`）：另一会话的 storage WIP（analysis_ledger append-only + trade_journal）由本会话验证收尾；期间发现对方测试断言写错（期望 Add 实际 Watch），对方会话随后自行修好——并发协作实据。
- **报告改造 A 触发明细**（`2330aaf`）：根因是 RiskModel 在 Rust 里算好了完整触发原因（实际值+人话+贡献），综合层却丢弃。`Factor` 加 `details: Vec<Factor>` 嵌套字段，pybind 递归透传，报告新增「触发原因」表。立讯风险 100 分从此可解释：基线50 + ATR占比4.79%(+15) + 年化波动42.4%(+5) + 回撤-29.8%(+20) + 空头占优(+10)。
- **报告改造 B 结论先行**（`ddf6b0e`）：评分表之前加「## 结论」段——机会端各模型方向一览、风险端按贡献排序 Top3 触发、综合一句话（分数 → Action + 命中规则）。纯规则模板拼接，不涉及 AI，不碰红线。
- **报告改造 C K线配图**（`c8d0be9`）：新增 `orchestrator/chart.py`——上面板红涨绿跌蜡烛 + MA20/MA60 + BOLL(20,2) 带、下面板成交量；`AnalysisResult` 挂 `df`，`write_report` 出 `reports/charts/<symbol>_<date>.png` 并在 meta 后引用；画图失败告警降级无图。中文字体回退链实测选中 Microsoft YaHei。requirements.txt 加 matplotlib。
- **并行动态**：另一会话开工 Python 层 `LedgerBook` 门面 + CLI（`python/meridian/ledger.py`、`tests/test_ledger.py` 未提交 WIP，其测试当时 3 红——属对方进行中状态，与本会话改动无关）。本会话提交严格只含自己文件，避免踩脚。
- **过程教训**：①matplotlib `tight_layout` 与 gridspec `hspace` 冲突告警——去掉 `tight_layout`（hspace 已够）；②judge/主模型当前不支持图像输入，K线图质量以程序化验证代替（颜色分布统计 + 字体选择断言）。

### 2026-09-03

- **Phase 0：工程骨架 + 核心量化闭环**（`89a25bf`）——第一个工作会话，从零到可运行：
  - Rust workspace 六 crate（`core`/`indicators`/`quant_engine`/`storage`/`pybind`/`backtest`占位），依赖方向 `core ← indicators ← quant_engine`、`core ← storage`
  - 三层评分：Opportunity(0-100) / Risk(0-100 **独立维度非扣分项**) / Action（只能由 `action_rules` 规则匹配生成）
  - 合成公式 `Σ(模型分×权重) ÷ Σ权重`，未登记模型用 `unknown_model_weight=0.2` 兜底保证贡献可见
  - 指标约定（**红线**）：窗口不足返回 `None`（不用 NaN）、只用 t 及之前数据、`high_20d/low_20d` 不含当日（突破判定语义）
  - 配置指纹：BTreeMap + serde_json + sha256 前 16 位（HashMap 键序随机会破坏指纹稳定性）
  - duckdb 1.3 bundled（MSVC 首次编译 ~40min）、UPSERT、Hive 分区 parquet
  - pyo3 0.26：maturin mixed 布局 `meridian.meridian_core`；Python 模型桥接协议 `analyze(payload) -> dict`
  - 配置系统：app/markets/data_sources/scoring 四组 YAML，代码零硬编码（验收标准 6/7）

## 环境备忘（踩过的坑）

| 坑 | 处理 |
| --- | --- |
| Windows 下 cargo 不在 PATH | `export PATH="$HOME/.cargo/bin:$PATH"`（Git Bash） |
| maturin develop 构建扩展 | `export PYO3_PYTHON="D:\\Meridian\\.venv\\Scripts\\python.exe" && export VIRTUAL_ENV="D:\\Meridian\\.venv"` 后再跑 |
| 东财（push2his.eastmoney.com）间歇性掐连接 | akshare 请求用 `NO_PROXY="*"` 直连；UA 必须完整浏览器串；管线已有多源 failover + 本地库回退兜底 |
| duckdb bundled 首次编译慢 | ~40min/4.6GB，MSVC；增量重编只动改动 crate |
| matplotlib 中文字体 | 回退链在 `chart.py::_CJK_FONTS`，Windows 选中 Microsoft YaHei；`axes.unicode_minus=False` 防负号方框 |
| Git 的 LF/CRLF 警告 | 无害，忽略 |
| Mimosa 安全钩子 | commit 前扫描；"扫描未完整结论"警告不拦截，高危会拦截（曾拦 probe 脚本 SSRF，已加白名单） |
| 并行会话提交 | 工作区可能有另一会话 WIP，`git add` 只加自己的文件，提交前 `git diff <file>` 确认归属 |

## 常用命令

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cargo test                                   # Rust 全量
.venv/Scripts/python -m pytest tests/ -q     # Python 全量（离线）
.venv/Scripts/python -m meridian.cli analyze --symbol 600519   # 分析（数据源失败自动回退）
.venv/Scripts/python -m meridian.cli analyze --symbol 600519 --offline  # 纯离线
NO_PROXY="*" .venv/Scripts/python scripts/probe_data_sources.py  # 数据渠道体检
```

## 架构红线（改动前必读，违反=返工）

1. AI 模型只能注册进 Opportunity / Risk 两通道（`AnalysisModel` trait 架构保证）；action 建议只能由 `action_rules` 规则匹配生成，AI 不可干预
2. ResearchAgent 只输出信息报告，没有评分字段
3. 指标禁未来函数：只用 t 及之前数据；窗口不足返回 `None`
4. 软件名/权重/标的池/数据源只存在于 `config/`，代码零硬编码
5. 评分可追溯：factors + model_version + config_fingerprint 必须落库
