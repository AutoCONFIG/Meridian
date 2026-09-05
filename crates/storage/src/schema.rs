//! 全部建表 SQL（幂等）。Phase 0：bars + trend_scores；
//! 后续 phase 增量扩展（regime_history / fundamentals / journal 等），只加不改。

/// 建表语句集合，`MeridianDb::init` 时整体执行。
pub const SCHEMA_SQL: &str = r#"
-- 行情 K 线（热数据）。主键含 market：不同市场 symbol 可能撞码。
CREATE TABLE IF NOT EXISTS bars (
    market      VARCHAR NOT NULL,
    symbol      VARCHAR NOT NULL,
    frequency   VARCHAR NOT NULL,
    date        DATE    NOT NULL,
    open        DOUBLE  NOT NULL,
    high        DOUBLE  NOT NULL,
    low         DOUBLE  NOT NULL,
    close       DOUBLE  NOT NULL,
    volume      DOUBLE  NOT NULL,
    amount      DOUBLE  NOT NULL,
    PRIMARY KEY (market, symbol, frequency, date)
);

-- 三层评分结果（CompositeScore 落库；factors/triggers 以 JSON 存，读回可追溯）。
CREATE TABLE IF NOT EXISTS trend_scores (
    market              VARCHAR NOT NULL,
    symbol              VARCHAR NOT NULL,
    date                DATE    NOT NULL,
    opportunity         DOUBLE  NOT NULL,
    risk                DOUBLE  NOT NULL,
    action              VARCHAR NOT NULL,
    position_hint       DOUBLE,
    rule_triggers       VARCHAR NOT NULL,
    opportunity_factors VARCHAR NOT NULL,
    risk_factors        VARCHAR NOT NULL,
    model_version       VARCHAR NOT NULL,
    config_fingerprint  VARCHAR NOT NULL,
    PRIMARY KEY (market, symbol, date)
);

-- 决策台账（做账，append-only）：每次分析自动留痕一条 —— 记录"系统在何时、
-- 基于哪个数据窗口/数据来源、给出什么建议"，凭 config_fingerprint + 数据窗口可复现。
-- 与 trend_scores 的区别：trend_scores 按 (market, symbol, date) UPSERT 存最新；
-- 台账每次分析追加一行，历史建议永不覆盖（审计语义）。
CREATE TABLE IF NOT EXISTS decision_ledger (
    id                 BIGINT   NOT NULL,
    ts                 TIMESTAMP NOT NULL,
    symbol             VARCHAR NOT NULL,
    name               VARCHAR NOT NULL,
    market             VARCHAR NOT NULL,
    asset_type         VARCHAR NOT NULL,
    frequency          VARCHAR NOT NULL,
    data_start         DATE    NOT NULL,
    data_end           DATE    NOT NULL,
    bar_count          BIGINT  NOT NULL,
    data_source        VARCHAR NOT NULL,
    fallback_reason    VARCHAR,
    regime             VARCHAR NOT NULL,
    opportunity        DOUBLE  NOT NULL,
    risk               DOUBLE  NOT NULL,
    action             VARCHAR NOT NULL,
    position_hint      DOUBLE,
    rule_triggers      VARCHAR NOT NULL,
    model_version      VARCHAR NOT NULL,
    config_fingerprint VARCHAR NOT NULL,
    report_path        VARCHAR,
    PRIMARY KEY (id)
);

-- 人工决策/成交日志（append-only）：本软件不做交易，实际操作由用户手动补记；
-- ledger_id 回链 decision_ledger.id，形成"系统建议 → 人工决策"证据链。
CREATE TABLE IF NOT EXISTS trade_journal (
    id         BIGINT   NOT NULL,
    ts         TIMESTAMP NOT NULL,
    symbol     VARCHAR NOT NULL,
    market     VARCHAR NOT NULL,
    side       VARCHAR NOT NULL,
    quantity   DOUBLE,
    price      DOUBLE,
    note       VARCHAR,
    ledger_id  BIGINT,
    PRIMARY KEY (id)
);

-- 市场状态快照（append-only）：每次分析时的 regime 判定，basis_json 为人话判定依据
-- （对应 RegimeState.basis），detector 记录检测器名+可复现。一期输入为标的自身K线（代理），
-- 后续接指数渠道后同一张表直接复用。
CREATE TABLE IF NOT EXISTS regime_history (
    id         BIGINT    NOT NULL,
    ts         TIMESTAMP NOT NULL,
    symbol     VARCHAR NOT NULL,
    name       VARCHAR NOT NULL,
    market     VARCHAR NOT NULL,
    asset_type VARCHAR NOT NULL,
    frequency  VARCHAR NOT NULL,
    regime     VARCHAR NOT NULL,
    confidence DOUBLE  NOT NULL,
    basis_json VARCHAR NOT NULL,
    detector   VARCHAR NOT NULL,
    PRIMARY KEY (id)
);
"#;
