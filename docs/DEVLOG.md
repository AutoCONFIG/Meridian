# Meridian 开发台账（DEVLOG）

> **本文档是给后续维护者（人或 AI）看的开发账本。**
> 结构：**状态快照 → 当前阶段任务（看板）→ 开发日记（按天）→ 参考区**（环境备忘/常用命令/架构红线）。
> 约定：每完成一项开发——任务看板打勾、日记当天小节追加一段、状态快照更新；动红线或踩环境坑必须记。完整计划见 [PLAN.md](PLAN.md)。

## 状态快照（每次提交后更新）

- **分支**：`main`（远程 `origin` = github.com:AutoCONFIG/Meridian.git）
- **最新提交**：见 `git log`；本节随每次提交更新
- **测试基线**：Rust 88（core 27 + indicators 21 + quant_engine 27 + storage 13）+ Python 78，全绿
- **数据状态**：DuckDB（`data/meridian.duckdb`）已入库标的：600519 / 300750 / 600547 / 002475 / 00700 / AAPL / RB0 / 601318 / 300059（日K到 2026-09-04）
- **当前阶段**：Phase 1 进行中——RegimeDetector 真实逻辑（trend_vol_v1）✅ 落地；下一步 regime 权重档配置扩展 / 回测引擎
- **并行会话**：另一会话在做 Python 层 LedgerBook 门面 + CLI 台账命令（已随 `bb47ed5` 入库并全绿）；各自提交时严格只含自己的文件

## 当前阶段任务

### ✅ 已完成

- [x] **Phase 0：工程骨架 + 核心量化闭环**（`89a25bf`，09-03）——Rust 六 crate、三层评分、DuckDB、pyo3 桥、四组配置 YAML
- [x] **数据渠道实测 + 多渠道数据层**（`8e1e9d2`/`68e06e9`，09-03~09-04，sess_b6f551ca 会话）——13 渠道实测记录 DATA_SOURCES.md + probe 体检脚本；MultiDailySource failover、跨源对账、失败冷却、增量同步、港美/期货源、实时快照补强（详见日记 09-03/09-04 两节）
- [x] **离线回退 + 市场路由 + 即兴分析**（`500d921`/`bee2212`/`af3eb86`）——缓存回退、按 (market, asset_type) 路由、标的池外自动识别
- [x] **决策台账存储层**（`82b0cca`）——analysis_ledger / trade_journal 表（另一会话 WIP 由本会话收尾）
- [x] **报告可读性改造**（`2330aaf`/`ddf6b0e`/`c8d0be9`）
  - [x] A：模型内部触发明细透传 + 「触发原因」表（回答"分数为什么"）
  - [x] B：结论先行摘要段（机会方向一览 + 风险 Top 触发 + 综合一句话）
  - [x] C：K线 + MA20/MA60 + BOLL + 成交量配图嵌入报告

- [x] **决策台账 Python 层**（`bb47ed5`，并行会话完成）——`LedgerBook` 门面 + CLI 台账命令 + `LEDGER.md` 使用文档；与 DEVLOG 重构同提交入库（见日记 09-04「bb47ed5 提交说明」），Rust 81 + Python 74 全绿
- [x] **Phase 1·市场状态检测 trend_vol_v1**（09-05）——RegimeState 加 basis、TrendVolDetector 规则检测、`config/regime.yaml` 阈值、`regime_history` 表落库、报告"市场状态"区（中文+置信度+判定依据）；002475 实测 Bear 95%（详见日记 09-05）

### 🔨 进行中（并行会话）

- （当前无——并行会话的 ledger 功能已随 `bb47ed5` 收尾入库）

### 📋 待办（按 PLAN.md 路线）

- [ ] **Phase 1 剩余**：指数渠道接入（检测输入从标的自身K线切到沪深300/标普500/VIX）、regime 权重档扩展（by_regime 目前只有 Bear 档）、scheduler 定时任务
- [ ] **Phase 2：回测 + AI 摘要**——回测引擎激活（backtest crate 占位中）、LLM Summary Agent（把规则报告转译成人话，不产分数不碰 action）
- [ ] **Phase 3+**：组合管理、基本面数据、AI 预测模型（只进 Opportunity/Risk 通道）、Research Agents、FastAPI Web + Tauri
- [ ] （低优先）Mimosa 完整安全审计补跑（钩子一直提示"扫描未完整"）

## 开发日记

### 2026-09-05

- **Phase 1·市场状态检测落地**（trend_vol_v1，全链路一次打通）：
  - **Rust**：`RegimeState` 加 `basis: Vec<String>`（人话判定依据，去 Copy 改 Clone + `normalized()`）；`market_regime.rs` 实现 `TrendVolDetector`——趋势（MA20/60 快慢线+偏离带）× 波动（ATR14 占比）× 急跌（20 日窗内回撤），判定优先级 **Crisis（急跌+高波动同时成立）> Bear/Bull（趋势成立）> HighVol > Sideways**，窗口不足恒 Unknown（同指标红线"宁缺毋滥"）。6 个单测（强涨→Bull/缓跌→Bear/崩跌+高波动→Crisis/横盘→Sideways/短窗→Unknown/阈值覆盖改变判定）。
  - **pybind**：`PyRegimeDetector`（8 个阈值参数全默认可覆盖 + 非法值校验）；`PyDb.insert_regime_history` / `latest_regime_history`。
  - **storage**：`regime_history` 表（append-only：ts/symbol/regime/confidence/basis_json/detector）+ roundtrip 测试。
  - **config**：`config/regime.yaml`（阈值全量可配，红线 4）+ `RegimeConfig.load`（缺失文件容忍回落默认——增强配置不挡管线）。
  - **pipeline 接线**：analyze 前检测 → regime 传入 evaluate（by_regime 权重档生效）→ result 带置信度/依据/检测器名 → persist 时写 regime_history（失败告警不阻断）。报告 meta 显示中文状态+置信度，依据以引用行展示。
  - **一期代理输入说明**：检测输入暂用标的自身K线（数据层尚无指数渠道）；PLAN 的指数版（沪深300/标普500/VIX）后续只换输入不换代码（trait 接口不变）。
  - **实测**：002475 立讯精密 → **Bear 95%**，依据"MA20/60 = 56.05/60.50 偏离 -10.2%；20日回撤 -7.7%；ATR14/收盘 5.0%"，regime_history 落库可读回。
  - **过程教训**：①duckdb-rs 的 `rows.next()` 返回 `Result<Option<&Row>>`（rusqlite 是 Option），match 分支要 Ok(None)/Ok(Some)/Err；②pyclass 方法调 trait 方法必须 import trait 本身；③测试预灌内存库必须无条件做（persist=False 也会经 `_read_cache` 触碰真实 data/ 库文件，撞文件锁）。

### 2026-09-04

今天主线是**数据韧性 + 报告可读性**，从"能跑"推进到"能看懂"。

- **数据渠道实测收尾 + 多渠道数据层落地**（sess_b6f551ca 会话，`8e1e9d2` 于 09-03、`68e06e9` 于本日 10:48）：
  - **渠道实测**：13 个数据渠道逐一实跑验证（东财K线/快照、新浪快照/期货K线、通达信 pytdx、CTP（本期未实施）、腾讯港美 ifzq、东财港美等），接口规范、字段位、踩坑全记入 `docs/DATA_SOURCES.md`；配套 `scripts/probe_data_sources.py` 渠道体检脚本（后经 Mimosa 拦截加了出口域名白名单）。
  - **港/美/期货数据源**：新增 `data/global_stock.py`（腾讯 ifzq 港美日K + smartbox/searchapi 代码解析）、`data/futures.py`（akshare 期货主连/合约日K）；`realtime.py` 补新浪 rt_hk/gb_ 与腾讯港股快照，实测字段位（港股 成交额=f11/成交量=f12，美股 昨收=f26——报文自洽 + 双源交叉验证）。
  - **增量落库同步**：`data/sync.py` `DailySyncer`——库内最新日期为游标只拉缺口，UPSERT 幂等；`PyDb.latest_bar_date`（Rust 新增）支撑游标。
  - **韧性**：`SourceHealth` 连续失败冷却排链尾，接入三个多源组合；跨源收盘价对账 >1% 判脏。
  - **管线集成**：增量同步 → 读库分析（`data_source=store`），源失败回退本地库；engine 按 asset_type 加载对应 scoring yaml（`config/scoring/futures.yaml` 独立权重，期货不再套用股票权重）。
  - **Rust 侧**：`AssetType::Futures`、`Bar.amount` 允许 NaN（±inf 仍非法）。
  - **验证**：test_sync / test_global / test_realtime 离线全绿（现并入 Python 74 总基线）；probe 13 渠道实跑全绿（含港股双源对账、美股自洽、腾讯日K与快照对账）。
- **离线回退 + 市场路由**（`500d921`/`8e1e9d2`/`bee2212`）：东财间歇断连期间全靠这套撑住——数据源失败自动回退本地 DuckDB，报告标注数据来源与回退原因；CLI `--offline`；修复 00700/AAPL/RB0 错走 A 股链的路由 bug（改为按 `(market, asset_type)` 惰性路由）。
- **即兴分析**（`af3eb86`）：响应"看一只股票还要改配置？"的反馈——`find_or_auto` 按代码模式自动识别市场（6位0/3/6→cn股、5位→hk、纯字母→us、字母+数字→期货），CLI 加 `--name`。002475 立讯精密入池（`572beaf`）。
- **决策台账存储层**（`82b0cca`）：另一会话的 storage WIP（analysis_ledger append-only + trade_journal）由本会话验证收尾；期间发现对方测试断言写错（期望 Add 实际 Watch），对方会话随后自行修好——并发协作实据。
- **报告改造 A 触发明细**（`2330aaf`）：根因是 RiskModel 在 Rust 里算好了完整触发原因（实际值+人话+贡献），综合层却丢弃。`Factor` 加 `details: Vec<Factor>` 嵌套字段，pybind 递归透传，报告新增「触发原因」表。立讯风险 100 分从此可解释：基线50 + ATR占比4.79%(+15) + 年化波动42.4%(+5) + 回撤-29.8%(+20) + 空头占优(+10)。
- **报告改造 B 结论先行**（`ddf6b0e`）：评分表之前加「## 结论」段——机会端各模型方向一览、风险端按贡献排序 Top3 触发、综合一句话（分数 → Action + 命中规则）。纯规则模板拼接，不涉及 AI，不碰红线。
- **报告改造 C K线配图**（`c8d0be9`）：新增 `orchestrator/chart.py`——上面板红涨绿跌蜡烛 + MA20/MA60 + BOLL(20,2) 带、下面板成交量；`AnalysisResult` 挂 `df`，`write_report` 出 `reports/charts/<symbol>_<date>.png` 并在 meta 后引用；画图失败告警降级无图。中文字体回退链实测选中 Microsoft YaHei。requirements.txt 加 matplotlib。
- **并行动态**：另一会话开工 Python 层 `LedgerBook` 门面 + CLI（`python/meridian/ledger.py`、`tests/test_ledger.py`），其测试一度 3 红——属对方进行中状态，与本会话改动无关。
- **bb47ed5 提交说明**（如实记录）：该提交消息写的是"DEVLOG 重构"，但实际还包含了并行会话刚 add 进暂存区的 ledger 功能全套（`ledger.py`/`test_ledger.py`/`cli.py` 台账命令/`LEDGER.md` 文档/storage + py_engine.rs Rust 侧）——共享暂存区所致，且已推送，不重写历史。提交后即时验证：Rust 81 全绿、test_ledger 11 全过（对方会话已自行修好）、Python 全量 74 全绿。**该提交实际 = DEVLOG 重构 + 决策台账 Python 层收尾，代码可用**。教训：并行会话共享 git 暂存区，`git commit` 前先 `git status` 看暂存区归属，或用 `git commit -- <自己的文件>` 路径限定。
- **过程教训**：①matplotlib `tight_layout` 与 gridspec `hspace` 冲突告警——去掉 `tight_layout`（hspace 已够）；②judge/主模型当前不支持图像输入，K线图质量以程序化验证代替（颜色分布统计 + 字体选择断言）。

### 2026-09-03

- **数据渠道摸底实测**（sess_b6f551ca 会话，当日部分 `8e1e9d2`）：为"数据从哪来、靠不靠谱"做实证——13 个渠道逐一实跑，结果固化成 `docs/DATA_SOURCES.md`（每渠道：接口/字段/实测样例/坑）+ `scripts/probe_data_sources.py` 一键体检。结论：A 股日K主走东财、港美走腾讯 ifzq、期货走 akshare/新浪，通达信可做 tick 备选，CTP 暂缓。这份实测直接喂给次日（09-04）的多渠道数据层实现（`68e06e9`）。
- **Phase 0：工程骨架 + 核心量化闭环**（`89a25bf`，本会话前身）——第一个工作会话，从零到可运行：
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
