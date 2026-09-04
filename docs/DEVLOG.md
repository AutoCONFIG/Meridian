# Meridian 开发台账（DEVLOG）

> **本文档是给后续维护者（人或 AI）看的开发账本。**
> 每完成一项开发就追加一节：做了什么、为什么、验证结果。改动架构红线或环境适配时必须记。
> 阅读顺序：先看「状态快照」，再按时间线查细节。完整计划见 [PLAN.md](PLAN.md)。

## 状态快照（每次提交后更新）

- **分支**：`main`（远程 `origin` = github.com:AutoCONFIG/Meridian.git）
- **最新提交**：见 `git log`；本节随每次提交更新
- **测试基线**：Rust 81（core 27 + indicators 21 + quant_engine 21 + storage 12）+ Python 62，全绿
- **数据状态**：DuckDB（`data/meridian.duckdb`）已入库标的：600519 / 300750 / 600547 / 002475 / 00700 / AAPL / RB0 / 601318 / 300059（日K到 2026-09-03）
- **当前阶段**：Phase 0 完成；报告可读性改造进行中（A 触发明细 ✅、B 结论先行 ✅），之后进 Phase 1（regime 真实逻辑）

## 时间线

### 2026-09-04 · 报告改造 B：结论先行的规则模板摘要段

- 报告 meta 头之后、评分表之前新增「## 结论」段：机会端各模型方向一览（趋势/动量/资金 + 向上/向下/中性）、风险端按贡献排序取前 3 条触发原因、综合一句话（机会分/风险分 → Action + 命中规则）。全部来自落库字段的规则模板拼接，**不涉及 AI**，不碰红线 1/2。
- 渲染顺序修正：结论段插在三层评分表之前（结论先行，细节在后）。
- 验证：pytest 62 全绿（含 `"## 结论" in report` 断言）；002475 重生成目检通过——「机会端：趋势向下、动量中性、资金中性 / 风险端：距高点回撤超25%… / 综合：机会 39.5、风险 100.0 → Watch（规则：机会≥35 → Watch）」。

### 2026-09-04 下午 · 决策台账存储层（`82b0cca`，另一会话 WIP 收尾）+ 触发明细透传

- **ledger/trade_journal**（`82b0cca`）：另一会话未提交的存储层 WIP 由本会话验证并收尾提交——`analysis_ledger`（每次分析一行，数据来源/回退原因/评分/指纹/报告路径，append-only）+ `trade_journal`（人工决策记录，可关联 ledger_id）。修复一处错误断言（Add/Watch）。
- **触发明细透传**（本节随后提交）：`Factor` 增加 `details: Vec<Factor>` 嵌套字段，综合层不再丢弃模型内部规则触发；pybind 递归透传；报告新增「触发原因」明细表（每条触发带实际值+贡献+人话判定）。此前报告只有"方向x置信度y权重z"，分数为什么全靠猜——立讯 002475 风险 100 分实际由 ATR占比4.79%(+15)/年化波动42.4%(+5)/回撤-29.8%(+20)/空头占优(+10) 构成，明细原来算了但被扔掉。

### 2026-09-03 · Phase 0：工程骨架 + 核心量化闭环（`89a25bf`）

第一个工作会话完成。内容：

- Rust workspace 六 crate（`core`/`indicators`/`quant_engine`/`storage`/`pybind`/`backtest`占位），依赖方向 `core ← indicators ← quant_engine`、`core ← storage`、pybind 依赖全部
- 三层评分：Opportunity(0-100) / Risk(0-100 **独立维度非扣分项**) / Action（只能由 `action_rules` 规则匹配生成）
- 合成公式 `Σ(模型分×权重) ÷ Σ权重`，未登记模型用 `unknown_model_weight=0.2` 兜底保证贡献可见
- 指标约定（**红线**）：窗口不足返回 `None`（不用 NaN）、只用 t 及之前数据、`high_20d/low_20d` 不含当日（突破判定语义）
- 配置指纹：BTreeMap + serde_json + sha256 前 16 位（HashMap 键序随机会破坏指纹稳定性）
- duckdb 1.3 bundled（MSVC 首次编译 ~40min）、UPSERT、Hive 分区 parquet（month 补零）
- pyo3 0.26：maturin mixed 布局 `module-name = "meridian.meridian_core"`；Python 模型桥接协议 `analyze(payload: dict) -> dict`
- 配置系统：app/markets/data_sources/scoring 四组 YAML，代码零硬编码（验收标准 6/7）

### 2026-09-04 上午 · 多渠道数据层（`68e06e9`，另一会话完成）

- 新增 `data/global_stock.py`（腾讯 ifzq 港美日K）、`data/futures.py`（akshare 期货）、`data/sync.py`（DailySyncer 增量同步：库内最新日期为游标，只拉缺口，UPSERT）
- `MultiDailySource` 链式 failover + 跨源收盘价对账（偏差 >1% 判脏数据）；`SourceHealth` 连续失败冷却排链尾
- Rust：`AssetType::Futures`、`Bar.amount` 允许 NaN、`PyDb.latest_bar_date`
- 期货独立 `scoring/futures.yaml`，不再错套股票权重
- 新增 test_sync / test_global / test_realtime；probe 脚本 13 渠道实跑全绿

### 2026-09-04 · 离线回退 + 市场路由修复（`500d921`/`8e1e9d2`/`bee2212`/`572beaf`/`af3eb86`）

- **缓存回退**（`500d921`）：数据源拉数失败自动回退本地 DuckDB，报告标注数据来源与回退原因（可追溯）；CLI `--offline` 强制离线
- **probe 脚本 SSRF 加固**（同上）：Mimosa 安全钩子拦截后加了出口域名白名单 `_guard()`
- **市场路由修复**（`bee2212`）：`_default_source` 原写死 A 股链，00700/AAPL/RB0 会错走 akshare+tencentA股；改为按 `(market, asset_type)` 惰性路由组合源
- **即兴分析**（`af3eb86`）：标的池外代码不再要求先改配置——`find_or_auto` 按代码模式自动识别市场（6位0/3/6开头→cn股、5位→hk、纯字母→us、字母+数字→期货），CLI 加 `--name`
- 实测：东财持续断连期间，A 股链腾讯备源完成增量同步；四市场（cn/hk/us/期货）全部跑通

## 环境备忘（踩过的坑）

| 坑 | 处理 |
| --- | --- |
| Windows 下 cargo 不在 PATH | `export PATH="$HOME/.cargo/bin:$PATH"`（Git Bash） |
| maturin develop 构建扩展 | `export PYO3_PYTHON="D:\\Meridian\\.venv\\Scripts\\python.exe" && export VIRTUAL_ENV="D:\\Meridian\\.venv"` 后再跑 |
| 东财（push2his.eastmoney.com）间歇性掐连接 | akshare 请求用 `NO_PROXY="*"` 直连；UA 必须完整浏览器串；管线已有多源 failover + 本地库回退兜底 |
| duckdb bundled 首次编译慢 | ~40min/4.6GB，MSVC；增量重编只动改动 crate |
| Git 的 LF/CRLF 警告 | 无害，忽略 |
| Mimosa 安全钩子 | commit 前扫描；"扫描未完整结论"警告不拦截，高危会拦截 |

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

## 下一步（按 PLAN.md 路线）

- [ ] 报告可读性改造：~~保留模型内部触发明细渲染~~ ✅、~~结论先行摘要~~ ✅、K线图（进行中）
- [ ] Phase 1：RegimeDetector 真实逻辑（替换 NullDetector 恒 unknown）、regime_history
- [ ] Phase 2：回测引擎 + LLM Summary Agent
- [ ] Phase 3+：组合管理、基本面、AI 预测模型、Research Agents、FastAPI Web + Tauri
