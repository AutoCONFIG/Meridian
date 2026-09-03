# Meridian 工程 Plan v2.1 — AI增强型量化投资研究与决策辅助平台

> 本文档自包含，不依赖任何历史对话。执行 AI 的任务：按本计划实施 **Phase 0（工程骨架 + 核心层）**，
> 并为 Phase 1-6 预留好接口与目录结构。本计划作为基准文档（项目创建后移入 `meridian/docs/PLAN.md`）。
> 执行适配：见文末「13. 执行适配记录」，与正文冲突时以适配记录为准。

---

## 0. 执行环境说明

- 操作系统：Windows 11 (win32)，shell 为 Git Bash
- 工作区：`C:\Users\maoer\.zcode\workspace\default`，项目创建于其下 `meridian/` 子目录
- 开工第一步先检查工具链：`rustc --version` / `cargo --version` / `python --version`（需 3.11+）；
  缺 Rust 则用 rustup 安装（默认 msvc 目标，需 VS Build Tools）；缺 maturin 则 `pip install maturin`
- Python 依赖统一用 venv 管理；Rust 侧不引入 PyO3 之外的 Python 依赖

## 1. 项目定位

- **名称**：Meridian（可配置项，只出现在 `config/app.yaml` 和 README，代码零硬编码；
  Rust 产物名 `meridian_core`、Python 包名 `meridian` 不随改名变化）
- **定位**：个人投资研究终端（不是交易软件）
- **核心关系**：量化规则 = 骨架，AI预测 = 第二意见，AI Agent = 研究助手，**人 = 最终决策者**
- **明确不做**：自动交易、自动下单、AI 直接决定买卖、黑箱预测
- **做**：市场状态判断、股票/基金/商品筛选、趋势分析、风险监控、研究辅助、模拟交易验证

## 2. 核心设计原则（不可违背）

1. **量化规则最高优先级**：核心判断必须可计算、可回测、可解释、可复现
2. **AI 只增强不替代**：AI 不能修改核心评分、不能绕过风控规则、不能直接生成交易指令；
   建议只能由综合引擎按可配置规则生成
3. **所有模型统一接口**（Rust trait）：规则模型与 AI 预测模型输出同格式
4. **评分可追溯**：每个输出带 factors 明细 + model_version + 配置指纹
5. **回测与实盘同构**：同一 Strategy trait（Phase 2 实现）
6. 设计宣言写入 README 第一行：**量化负责可信，AI 负责理解，人负责决策**

## 3. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 核心层 | Rust（workspace，6 crate） | 编译器保证正确性、内存安全、长期维护；中低频性能足够 |
| 胶水层 | Python 3.11+ | 数据源生态（akshare 等）、AI/LLM 生态、快速迭代 |
| 绑定 | PyO3 + maturin | Rust 编译为 Python 模块 `meridian_core`；PyO3 代码全部隔离在 pybind crate |
| 存储 | DuckDB + Parquet | 日频/分钟级够用；预留 QuestDB 迁移路径 |
| 前端 | FastAPI + Web 驾驶舱 | Phase 6 才做，MVP 用 CLI + Markdown 报告 |
| AI | 大模型 API（可切供应商） | Phase 2 轻量解释起步，Phase 5 完整 Agent |

## 4. 系统架构（数据流）

```text
数据中心（多市场多源） → 数据处理与特征工程
        ↓
Market Regime 检测（Bull/Bear/Sideways/HighVol/Crisis/Unknown + 置信度）
        ↓
AnalysisContext（regime + 资产 + 行情/指标快照 + 版本）
        ↓
智能分析核心层：Rule Models(Rust) + AI Prediction Models(Python桥接)
                ← 同一 AnalysisModel trait，注册时显式指定通道
        ↓
综合研究决策引擎（三层评分，按 regime 权重档合成）
  Opportunity Score 机会 0-100 / Risk Score 风险 0-100 / Action 建议
  + factors 明细 + model_version + config_fingerprint
        ↓
SummaryAgent 轻量解释（Phase 2 起，LLM 把 factors 转成一句话，不碰分数）
        ↓
投资驾驶舱 + Journal 记录 → 人（最终决策）
```

Research Agents（Phase 5）：独立 ResearchAgent trait，输出**信息报告**（事件/逻辑/异常），
不产生 score、不进综合引擎加权，与量化结论**并排呈现**。

## 5. 项目结构

```text
meridian/
├── Cargo.toml                     # workspace: members = crates/*
├── pyproject.toml                 # maturin 构建 meridian_core
├── README.md                      # 首行=设计宣言
├── docs/PLAN.md                   # 本计划
├── crates/
│   ├── core/src/
│   │   ├── asset.rs               # Market/Asset/Frequency
│   │   ├── bar.rs                 # Bar（含合法性校验：high>=max(open,close) 等）
│   │   ├── order.rs               # Order/Side/Type/Trade/Position（Phase 2 用，先定义+单测）
│   │   ├── signal.rs              # Direction/Factor/ModelOutput/三层评分/RegimeState
│   │   ├── context.rs             # AnalysisContext（含 regime）
│   │   ├── model.rs               # trait AnalysisModel + ModelCategory + Channel
│   │   ├── agent.rs               # trait ResearchAgent（预留，Phase 5 实现）
│   │   └── lib.rs
│   ├── indicators/src/            # 纯函数：sma/ema/macd/rsi/atr/boll/obv/adx/回撤/年化波动
│   ├── quant_engine/src/
│   │   ├── trend_model.rs         # TrendModel: MA结构/ADX/突破 → 机会通道
│   │   ├── momentum_model.rs      # RSI/MACD/20日收益 → 机会通道
│   │   ├── risk_model.rs          # ATR/波动率/回撤 → 风险通道
│   │   ├── capital_model.rs       # 量价/OBV → 机会通道
│   │   ├── fundamental_model.rs   # 占位，Phase 3 实现
│   │   ├── market_regime.rs       # RegimeDetector trait + NullDetector(返回Unknown)；Phase 1 填逻辑
│   │   ├── composite.rs           # 三层综合引擎 + regime权重档 + 配置指纹
│   │   └── lib.rs
│   ├── backtest/                  # Phase 0 仅建空 crate 占位；Phase 2 实现
│   │   └── src/                   #   broker.rs portfolio.rs engine.rs metrics.rs walk_forward.rs
│   ├── storage/src/
│   │   ├── duckdb.rs              # 连接/建表/读写
│   │   ├── parquet.rs             # Hive 分区写入（COPY TO PARTITION_BY）
│   │   ├── schema.rs              # 全部建表 SQL
│   │   └── lib.rs
│   └── pybind/src/
│       ├── lib.rs                 # #[pymodule] meridian_core
│       ├── py_model.rs            # PyAnalysisModel：Python模型 → Rust trait 桥接
│       └── conversions.rs         # Rust↔Python 类型转换
├── python/meridian/
│   ├── __init__.py                # 版本从包元数据读；软件名从 config/app.yaml 读
│   ├── config.py                  # pydantic-settings：APP_NAME/路径/数据源
│   ├── data/
│   │   ├── base.py                # DataSource 抽象: fetch_daily(symbol,start,end)->统一schema DataFrame
│   │   └── cn_stock.py            # akshare 实现（stock_zh_a_hist），独立适配层（接口常变）
│   │                              # us_stock/hk_stock/commodity/fund/fundamental/macro = Phase 1
│   │                              # news.py = 占位，Phase 2 激活
│   ├── ai_quant/                  # Phase 4：base/registry/calibration(isotonic,platt)/prediction/ranking_model
│   │                              #   实现准则：批量推理（Rust算特征→Python批量预测→回填），禁止逐bar跨界
│   ├── agents/
│   │   ├── base.py                # ResearchAgent 抽象基类（Phase 0 只留此文件）
│   │   └── llm_client.py          # Phase 2；summary.py 轻量解释 Phase 2；8个Agent Phase 5
│   ├── portfolio/                 # Phase 3：holdings/analysis/sizing(规则式仓位)/journal
│   ├── ranking/                   # Phase 3：universe/screener（支持价值/成长/趋势风格profile）
│   ├── orchestrator/
│   │   ├── pipeline.py            # 拉数→存库→Rust指标→三层评分→报告结构
│   │   └── scheduler.py           # 定时任务（Phase 1）
│   └── cli.py                     # meridian analyze --symbol 600519 → Markdown 报告
├── api/                           # Phase 6：FastAPI + routes(dashboard/analysis/backtest/portfolio)
├── config/
│   ├── app.yaml                   # name: Meridian
│   ├── markets.yaml               # 标的池（首批3只A股见 Step 8）
│   ├── data_sources.yaml
│   └── scoring/                   # 按资产类型：stock.yaml / gold.yaml / etf.yaml / index.yaml
│                                  # 每文件：model_version + weights(默认+按regime覆盖) + action_rules
├── data/                          # duckdb/ parquet/（gitignore，运行时数据）
└── tests/                         # Rust测试在各自crate内；Python测试在tests/
```

crate 依赖方向：core ← indicators ← quant_engine；core ← storage；core+indicators ← backtest；
pybind 依赖全部。除 pybind 外全部纯 Rust，不依赖 PyO3。

## 6. 核心接口设计（Rust 草案）

```rust
// crates/core/src/model.rs
pub enum ModelCategory { Rule, AiPrediction }          // 注意：无 Agent
pub enum Channel { Opportunity, Risk }                 // 注册时显式指定，与Category正交
                                                       // AI模型无论类别都只能进这两个通道，无法绕过风控

pub trait AnalysisModel: Send {
    fn name(&self) -> &str;
    fn version(&self) -> &str;
    fn category(&self) -> ModelCategory;
    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput>;
}

// crates/core/src/agent.rs（预留，Phase 5；形状届时可微调）
pub trait ResearchAgent: Send {
    fn name(&self) -> &str;
    fn investigate(&self, ctx: &ResearchContext) -> Result<AgentReport>;
}

// crates/core/src/signal.rs —— 三层评分（v2.1 关键修订）
pub struct Factor {
    pub name: String,        // 如 "均线多头排列"
    pub value: f64,          // 因子原始值
    pub contribution: f64,   // 对该层得分的贡献
    pub description: String, // 人话解释
}

pub struct ModelOutput {
    pub score: f64,           // 0-100（综合引擎统一 clamp）
    pub direction: Direction, // Up / Down / Neutral
    pub confidence: f64,      // 0-1
    pub factors: Vec<Factor>,
}

pub struct OpportunityScore { pub score: f64, pub factors: Vec<Factor> }
pub struct RiskScore       { pub score: f64, pub factors: Vec<Factor> } // 高=风险高

pub enum Action { Add, Hold, Reduce, Watch, Avoid }

pub struct ActionOutput {
    pub action: Action,
    pub position_hint: Option<f64>, // 规则式仓位参考，Phase 3 起有值
    pub rule_triggers: Vec<String>, // 触发了哪条规则（可追溯）
}

pub struct CompositeScore {
    pub opportunity: OpportunityScore,
    pub risk: RiskScore,
    pub action: ActionOutput,
    pub model_version: String,      // 如 "rule-v0.1"
    pub config_fingerprint: String, // 生效配置内容 sha256 前16位
}

pub enum Regime { Bull, Bear, Sideways, HighVol, Crisis, Unknown }
pub struct RegimeState { pub regime: Regime, pub confidence: f64 }
```

```rust
// crates/core/src/context.rs
pub struct AnalysisContext<'a> {
    pub asset: &'a Asset,
    pub regime: RegimeState,               // Phase 0 恒为 Unknown
    pub bars: &'a [Bar],                   // 升序日频序列
    pub indicators: &'a IndicatorSnapshot, // indicators crate 预计算的快照
    // fundamentals / macro: Phase 1、3 以 Option 字段扩展
}

// crates/quant_engine/src/composite.rs
pub struct CompositeEngine { config: ScoringConfig } // 从 scoring/{asset}.yaml 加载

impl CompositeEngine {
    // 模型注册时绑定通道：Vec<RegisteredModel{ model: Box<dyn AnalysisModel>, channel: Channel }>
    pub fn evaluate(&self, models: &[RegisteredModel], ctx: &AnalysisContext) -> Result<CompositeScore> {
        // opportunity = Σ(机会通道模型分 × weights.opportunity[按regime档])  → clamp 0-100
        // risk        = Σ(风险通道模型分 × weights.risk)                     → 独立维度，非扣分项
        // action      = action_rules 按 (opportunity, risk) 规则匹配生成，AI 不可干预
    }
}
```

## 7. Python↔Rust 桥接（关键实现，nautilus_trader 生产验证过的模式）

```rust
// crates/pybind/src/py_model.rs
pub struct PyAnalysisModel {
    py_self: Py<PyAny>, // 用弱引用持有，防 Rust wrapper↔Python对象 循环引用
}

impl AnalysisModel for PyAnalysisModel {
    fn analyze(&self, ctx: &AnalysisContext) -> Result<ModelOutput> {
        Python::attach(|py| {
            let snapshot = ctx.to_python_snapshot(py)?;   // 序列化成 dict，不传借用
            let out = self.py_self.call_method1(py, "analyze", (snapshot,))?;
            out.extract::<ModelOutput>(py)                 // schema 不符→明确报错
        }).map_err(|e| anyhow!("Python model analyze failed: {e}"))
    }
}
```

Python 侧协议（Phase 0 的哑模型、Phase 4 的 AI 模型都按此实现）：

```python
class AnalysisModel(Protocol):
    name: str
    version: str
    def analyze(self, context: dict) -> dict: ...
# context dict: {"symbol","name","market","regime",
#   "bars":[{"date","open","high","low","close","volume","amount"}],
#   "indicators":{"sma20":[...], ...}}
# return dict: {"score":0-100, "direction":"up|down|neutral", "confidence":0-1,
#   "factors":[{"name","value","contribution","description"}]}
```

PyEngine 暴露给 Python：`add_rust_model(name)` / `add_python_model(obj, channel)` /
`evaluate(symbol, bars) -> dict`（CompositeScore 序列化）。

性能注意：传参用 Rust tuple 走 vectorcall；长时间纯 Rust 计算 `Python::detach` 释放 GIL；
日频场景单次跨界开销可忽略，但 Phase 4 起 AI 模型必须走批量推理
（Rust 算完特征 → Python 一次批量预测 → 回填），禁止逐 bar 跨界。

## 8. 存储设计

| 表 | 阶段 | 关键字段 |
|---|---|---|
| bars_daily / bars_minute | P0 | symbol, date, ohlc, volume, amount, source, UNIQUE(symbol,date) |
| trend_scores | P0 | symbol, timestamp, opportunity, risk, action, factors_json, **model_version, config_fingerprint** |
| regime_history | P1 | timestamp, regime, confidence, basis_json |
| macro_data | P1 | indicator_name, value, timestamp |
| news_analysis | P2 | 原文 + AI 结构化分析 |
| model_registry | P2 | name, version, parameters_json, created_at |
| sim_orders / sim_trades / sim_positions | P2 | 模拟交易流水 |
| fundamentals | P3 | PE/PB/ROE/营收/利润（按报告期） |
| portfolio_holdings | P3 | 用户持仓 |
| journal_decisions | P3 | 日期/标的/理由/当时评分概率/风险/复盘 |

Parquet 分区：daily/minute 按 `symbol=X/year=Y/month=M/`；tick（未来）按 `symbol=X/date=D/`。
单文件红线 128MB-1GB，row group ~128MB。DuckDB 为热数据与元数据层，K线冷数据入 Parquet。
QuestDB 迁移触发条件（满足其一）：tick>100GB / 写入>10万行/s / 需实时 ASOF JOIN。
QuestDB 有官方 Rust client（推荐）；TDengine（Rust 连接器弱）、ArcticDB（仅 Python API）不选。

## 9. Phase 路线图（0-6）

- **Phase 0 工程骨架+核心层**（本次执行，详见第 10 节）
- **Phase 1 量化核心闭环+Regime**：RegimeDetector 实现（沪深300/标普500/VIX/美元指数/利率 → 规则版状态+置信度）；
  regime 权重档生效；多市场数据源（us_stock/hk_stock/commodity/fund）；宏观指数入库；
  regime_history 落库；报告加市场状态区；scheduler 定时任务
- **Phase 2 回测+模拟+版本管理+轻量AI解释**：事件驱动回测（滑点/手续费/仓位/绩效：年化/回撤/夏普/胜率/盈亏比）；
  Walk Forward 模式；ScoreBasedStrategy；模拟账户（虚拟 100 万）；model_registry 完整版；
  llm_client.py + SummaryAgent（factors→一句话解释，不碰分数）
- **Phase 3 股票筛选+组合管理**：fundamental_model+财务数据；universe/screener+投资风格 profile（价值/成长/趋势）；
  组合分析（集中度/相关性/风险暴露）；**规则式仓位建议**（基于风险分）；Investment Journal。MVO 优化器列后续可选
- **Phase 4 AI预测模型**：**Return Ranking 优先**（未来 N 日截面收益排名，不做涨跌点位预测）；
  波动预测；市场状态分类；**概率校准（isotonic/platt）必做**；批量推理接入统一引擎
- **Phase 5 完整Agent系统**：news/macro/sentiment/company/sector/portfolio/risk/anomaly 八个 ResearchAgent；
  报告与量化结论并排呈现
- **Phase 6 投资驾驶舱**：FastAPI + Web（市场状态→资产机会→股票排名→组合风险→AI报告）

后置池（按需，不做排期承诺）：Tick级+QuestDB、Alternative Data（公告/研报/情绪）、MVO、自动因子发现。

## 10. Phase 0 详细任务（本次执行，每步含验证）

1. **环境与骨架**：检查/安装 rustup(msvc)+Python3.11+`pip install maturin`；
   按第 5 节建目录树与占位文件；workspace Cargo.toml；
   pyproject.toml（`module-name="meridian_core"`, `python-source="python"`）；
   git init+.gitignore(target/,data/,__pycache__,.venv)；本计划存 docs/PLAN.md。
   验证：空 workspace `cargo build` 通过
2. **crates/core**：按第 6 节实现全部类型+单测（Bar 合法性校验、Action 映射、序列化）
3. **crates/indicators**：全部指标纯函数+单测。约定：窗口不足返回 None（不用 NaN）；
   用手工构造的已知序列断言精确值
4. **crates/storage**：schema.rs 全部建表 SQL；duckdb.rs（连接管理/append_bars/query_bars/save_trend_score）；
   parquet.rs（分区写+读）；测试：写入→读回一致
5. **crates/quant_engine**：四个 Rule 模型实现 AnalysisModel（逻辑真实基于指标，可从简）；
   RegimeDetector trait+NullDetector；CompositeEngine 三层合成+指纹；fundamental_model 占位。
   每个模型单测：构造上涨序列→direction=Up 且 score>60 等
6. **crates/pybind**：PyAnalysisModel+conversions+PyEngine；
   `maturin develop`；`python -c "import meridian_core"` 通过
7. **Python 骨架**：config.py（pydantic-settings 读 config/*.yaml）；
   data/base.py+cn_stock.py（akshare→统一 schema date/open/high/low/close/volume/amount，
   失败重试+明确报错）；orchestrator/pipeline.py；cli.py（--symbol/--output）；
   tests 用本地 CSV，不依赖网络
8. **配置文件**：

```yaml
# config/scoring/stock.yaml 示例
model_version: "rule-v0.1"
weights:
  opportunity:
    default: { trend_model: 0.40, momentum_model: 0.30, capital_model: 0.30 }
    by_regime:
      Bear:  { trend_model: 0.30, momentum_model: 0.20, capital_model: 0.50 }  # P1 生效
  risk:
    default: { risk_model: 1.0 }
action_rules:                       # (机会,风险)→建议，按序匹配
  - if: { opportunity_gte: 75, risk_lte: 40 } then: Add
  - if: { opportunity_gte: 50 } then: Hold
  - if: { opportunity_gte: 35 } then: Watch
  - default: Avoid
```

   其余配置：`app.yaml`（name: Meridian）；`markets.yaml` 首批标的：600519 贵州茅台、
   300750 宁德时代、600547 山东黄金（覆盖股票+黄金相关，指数留 Phase 1）；`data_sources.yaml`。

9. **端到端验证**：CLI 对 3 只标的输出 Markdown 报告（三层评分 + factors + model_version + 指纹）；
   Python 哑模型（固定输出 score=55）经 `add_python_model` 注册进 PyEngine，
   evaluate 结果体现其贡献 → 桥接闭环；人工核对评分与 factors 合理性
10. **收尾**：README（首行设计宣言 + 快速开始 + 复现安装步骤）；`cargo test` 全绿；
    requirements 清单；git 提交

## 11. Phase 0 验收标准

1. `cargo build/test` 全绿，core/indicators/quant_engine/storage/pybind 各有单元测试
2. `maturin develop` 后 `import meridian_core` 可调用指标计算与三层评分
3. 端到端：3 只 A股日频数据 → 指标 → Opportunity/Risk/Action 三层评分 →
   含 factors 明细 + model_version + 配置指纹的 Markdown 报告
4. Python 哑模型经 PyAnalysisModel 注册进综合引擎，与 Rust 原生模型统一出分（桥接闭环）
5. regime 字段贯穿 AnalysisContext（Phase 0 恒 Unknown，接口就位），NullDetector 可替换
6. 软件名/权重/标的池仅存在于 `config/`，代码零硬编码
7. 新增资产类型 = 新增一个 scoring yaml + markets 条目，不改核心代码

## 12. 关键风险与规避

| 风险 | 规避 |
|---|---|
| PyO3 桥接：GIL/生命周期/循环引用 | 全部隔离在 pybind crate；弱引用持 Python 对象；哑模型早期验证闭环 |
| akshare 接口经常变动 | cn_stock.py 独立适配层 + 统一 schema + 失败明确报错（不静默吞错） |
| 未来函数泄露 | 指标只使用 t 及之前数据；回测（P2）信号在 bar 收盘确认、撮合在下一 bar 开盘 |
| Windows MSVC 编译环境 | rustup 默认 msvc 需 VS Build Tools；受阻可退 gnu 目标 |
| NaN/浮点传播 | 指标窗口不足返回 None；评分统一 clamp 0-100；f64 对中低频足够 |
| AI 喧宾夺主 | 架构上不可能：建议只能由 CompositeEngine 按 action_rules 生成，AI 模型输出仅是加权输入 |

---

## 13. 执行适配记录

> 基准计划（第 0-12 节）保持原文；对执行环境的适配决策按时间记录于此，与正文冲突时以本节为准。

### 2026-09-03 · Phase 0 开工

1. **项目根目录**：`D:\Meridian`（第 0 节所述 workspace 路径按实际环境调整）。
2. **平台定位**：主要使用环境为 Linux；Windows / macOS 同样需要完整可用（含本地客户端）。
   - 代码零平台分支：路径全部相对项目根，运行时数据目录由代码自动创建，shell 统一 bash；
   - 各平台各用其最舒适的工具链：Windows = MSVC（Tier 1，且为 duckdb/PyO3/Tauri 的官方支持组合），Linux = gcc/clang。
3. **Rust 工具链（Windows 开发机）**：采用 MSVC 目标（决策理由：性能与生态舒适区；第 12 节 gnu 降级预案仅记录备查）。
   需一次性安装 VS Build Tools（"使用 C++ 的桌面开发"工作负载），已交由用户执行。
4. **前端形态（第 3/9 节 Phase 6 修订）**：一套 Web UI、两种壳——
   - Web 版：FastAPI 同源提供 API + 页面，可本机运行亦可部署服务器；
   - 本地客户端：Tauri v2 桌面壳复用同一套 Web UI（Windows/Linux/macOS 三端；Windows 10/11 自带 WebView2），
     数据统一来自本机 FastAPI 服务。
   - Phase 0（CLI + Markdown 报告）不受影响。
5. **Python**：3.12（uv 管理 venv），依赖清单见 `requirements.txt`。
6. **依赖版本基线**：pyo3 0.26 / duckdb 1.3 (bundled) / maturin 1.7+ / serde_yaml 0.9。

### 2026-09-03 · Phase 0 中期（pybind / 存储 / 配置落地适配）

7. **maturin mixed 布局约束**：`python-source` 与扩展模块并存时，maturin 要求
   `module-name = "<python包>.<rust模块>"`，故定为 `meridian.meridian_core`；
   Python 侧导入方式为 `from meridian import meridian_core`（第 7 节验收命令
   `import meridian_core` 相应调整为 `from meridian import meridian_core`）。
8. **pybind 模块拆分**：原列 lib.rs / py_model.rs / conversions.rs 三文件，
   实际增加 py_engine.rs（PyEngine 三层评分门面 + PyDb DuckDB 门面）；
   `AnalysisModel` trait 约束由 Send 提升为 Send + Sync（pyclass 要求）。
9. **Python 模型协议**：`analyze(payload: dict) -> dict`，payload 含
   asset / regime / bars_count / last_close / indicators（各指标序列末值）；
   返回 {"score","direction","confidence"}。哑模型与 Phase 4 AI 模型同协议。
10. **storage 细节**：bars 主键 (market, symbol, frequency, date) UPSERT 幂等；
    Parquet Hive 分区键 (year, month)，month 以 lpad 补零两位（Hive 生态惯例）；
    DuckDB SQL 字符串中路径统一正斜杠（反斜杠会被当作转义符）。
11. **评分权重容器**：WeightSpec 用 BTreeMap（HashMap 序列化键序随机，
    会导致配置指纹不稳定）。
