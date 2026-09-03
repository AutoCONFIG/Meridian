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
"#;
