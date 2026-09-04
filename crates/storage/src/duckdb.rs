//! DuckDB 连接与读写（热数据）：K 线与三层评分。

use std::path::Path;

use chrono::NaiveDate;
use duckdb::{params, Connection};
use meridian_core::{
    Action, ActionOutput, Asset, Bar, CompositeScore, MeridianError, OpportunityScore, Result,
    RiskScore,
};

use crate::schema::SCHEMA_SQL;

/// 决策台账查询行（decision_ledger）。
#[derive(Debug, Clone, PartialEq)]
pub struct LedgerRow {
    pub id: i64,
    pub ts: String, // %Y-%m-%d %H:%M:%S
    pub symbol: String,
    pub name: String,
    pub market: String,
    pub asset_type: String,
    pub frequency: String,
    pub data_start: NaiveDate,
    pub data_end: NaiveDate,
    pub bar_count: i64,
    pub data_source: String,
    pub fallback_reason: Option<String>,
    pub regime: String,
    pub opportunity: f64,
    pub risk: f64,
    pub action: String,
    pub position_hint: Option<f64>,
    pub rule_triggers: String, // JSON 数组字符串
    pub model_version: String,
    pub config_fingerprint: String,
    pub report_path: Option<String>,
}

/// 人工决策/成交日志行（trade_journal）。
#[derive(Debug, Clone, PartialEq)]
pub struct TradeRow {
    pub id: i64,
    pub ts: String, // %Y-%m-%d %H:%M:%S
    pub symbol: String,
    pub market: String,
    pub side: String,
    pub quantity: Option<f64>,
    pub price: Option<f64>,
    pub note: Option<String>,
    pub ledger_id: Option<i64>,
}

/// 时间戳解析：接受 "YYYY-MM-DD HH:MM:SS" 与 ISO "T" 分隔两种写法。
fn parse_ts(s: &str) -> Result<chrono::NaiveDateTime> {
    chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S"))
        .map_err(|e| MeridianError::Storage(format!("时间戳格式非法（YYYY-MM-DD HH:MM:SS）: {s}: {e}")))
}

/// DuckDB 封装：建表 + K 线/评分读写。
pub struct MeridianDb {
    conn: Connection,
}

impl MeridianDb {
    /// 打开（或创建）文件库并初始化 schema。
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let conn = Connection::open(path)
            .map_err(|e| MeridianError::Storage(format!("打开 DuckDB {} 失败: {e}", path.display())))?;
        Self::init(conn)
    }

    /// 内存库（测试 / 一次性分析）。
    pub fn open_in_memory() -> Result<Self> {
        let conn =
            Connection::open_in_memory().map_err(|e| MeridianError::Storage(format!("打开内存库失败: {e}")))?;
        Self::init(conn)
    }

    fn init(conn: Connection) -> Result<Self> {
        conn.execute_batch(SCHEMA_SQL)
            .map_err(|e| MeridianError::Storage(format!("初始化 schema 失败: {e}")))?;
        Ok(Self { conn })
    }

    /// 连接句柄（供 parquet 导出等模块复用）。
    pub fn conn(&self) -> &Connection {
        &self.conn
    }

    /// 批量写入 K 线（UPSERT：同 market+symbol+frequency+date 覆盖）。返回写入行数。
    pub fn insert_bars(&self, asset: &Asset, bars: &[Bar]) -> Result<usize> {
        let mut written = 0;
        for bar in bars {
            written += self
                .conn
                .execute(
                    "INSERT INTO bars
                        (market, symbol, frequency, date, open, high, low, close, volume, amount)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
                     ON CONFLICT (market, symbol, frequency, date) DO UPDATE SET
                        open = excluded.open, high = excluded.high, low = excluded.low,
                        close = excluded.close, volume = excluded.volume, amount = excluded.amount",
                    params![
                        asset.market.as_str(),
                        asset.symbol,
                        asset.frequency.as_str(),
                        bar.date,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.amount,
                    ],
                )
                .map_err(|e| MeridianError::Storage(format!("写入 bars 失败: {e}")))?;
        }
        Ok(written)
    }

    /// 读取 K 线：某标的某频率、日期区间 [start, end]（升序）。
    pub fn read_bars(&self, asset: &Asset, start: NaiveDate, end: NaiveDate) -> Result<Vec<Bar>> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT date, open, high, low, close, volume, amount
                 FROM bars
                 WHERE market = ?1 AND symbol = ?2 AND frequency = ?3
                   AND date BETWEEN ?4 AND ?5
                 ORDER BY date ASC",
            )
            .map_err(|e| MeridianError::Storage(format!("查询 bars 失败: {e}")))?;

        let rows = stmt
            .query_map(
                params![
                    asset.market.as_str(),
                    asset.symbol,
                    asset.frequency.as_str(),
                    start,
                    end,
                ],
                |row| {
                    Ok((
                        row.get::<_, NaiveDate>(0)?,
                        row.get::<_, f64>(1)?,
                        row.get::<_, f64>(2)?,
                        row.get::<_, f64>(3)?,
                        row.get::<_, f64>(4)?,
                        row.get::<_, f64>(5)?,
                        row.get::<_, f64>(6)?,
                    ))
                },
            )
            .map_err(|e| MeridianError::Storage(format!("查询 bars 失败: {e}")))?;

        rows.map(|row| {
            let (date, open, high, low, close, volume, amount) =
                row.map_err(|e| MeridianError::Storage(format!("读取 bars 行失败: {e}")))?;
            Bar::new(date, open, high, low, close, volume, amount)
        })
        .collect()
    }

    /// 某标的最新K线日期（增量同步游标）；无数据返回 None。
    pub fn latest_bar_date(
        &self,
        market: &str,
        symbol: &str,
        frequency: &str,
    ) -> Result<Option<NaiveDate>> {
        let mut stmt = self
            .conn
            .prepare("SELECT max(date) FROM bars WHERE market = ?1 AND symbol = ?2 AND frequency = ?3")
            .map_err(|e| MeridianError::Storage(format!("查询最新日期失败: {e}")))?;
        let mut rows = stmt
            .query_map(params![market, symbol, frequency], |row| {
                row.get::<_, Option<NaiveDate>>(0)
            })
            .map_err(|e| MeridianError::Storage(format!("查询最新日期失败: {e}")))?;
        match rows.next() {
            None => Ok(None),
            Some(row) => row.map_err(|e| MeridianError::Storage(format!("读取最新日期失败: {e}"))),
        }
    }

    /// 写入三层评分（UPSERT：同 market+symbol+date 覆盖）。
    pub fn insert_composite_score(
        &self,
        asset: &Asset,
        date: NaiveDate,
        score: &CompositeScore,
    ) -> Result<()> {
        let triggers = serde_json::to_string(&score.action.rule_triggers)
            .map_err(|e| MeridianError::Storage(format!("序列化 rule_triggers 失败: {e}")))?;
        let opp_factors = serde_json::to_string(&score.opportunity.factors)
            .map_err(|e| MeridianError::Storage(format!("序列化 opportunity_factors 失败: {e}")))?;
        let risk_factors = serde_json::to_string(&score.risk.factors)
            .map_err(|e| MeridianError::Storage(format!("序列化 risk_factors 失败: {e}")))?;

        self.conn
            .execute(
                "INSERT INTO trend_scores
                    (market, symbol, date, opportunity, risk, action, position_hint,
                     rule_triggers, opportunity_factors, risk_factors,
                     model_version, config_fingerprint)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
                 ON CONFLICT (market, symbol, date) DO UPDATE SET
                    opportunity = excluded.opportunity, risk = excluded.risk,
                    action = excluded.action, position_hint = excluded.position_hint,
                    rule_triggers = excluded.rule_triggers,
                    opportunity_factors = excluded.opportunity_factors,
                    risk_factors = excluded.risk_factors,
                    model_version = excluded.model_version,
                    config_fingerprint = excluded.config_fingerprint",
                params![
                    asset.market.as_str(),
                    asset.symbol,
                    date,
                    score.opportunity.score,
                    score.risk.score,
                    score.action.action.as_str(),
                    score.action.position_hint,
                    triggers,
                    opp_factors,
                    risk_factors,
                    score.model_version,
                    score.config_fingerprint,
                ],
            )
            .map_err(|e| MeridianError::Storage(format!("写入 trend_scores 失败: {e}")))?;
        Ok(())
    }

    /// 最近一条评分（按日期倒序取一）。
    pub fn latest_composite_score(&self, asset: &Asset) -> Result<Option<(NaiveDate, CompositeScore)>> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT date, opportunity, risk, action, position_hint,
                        rule_triggers, opportunity_factors, risk_factors,
                        model_version, config_fingerprint
                 FROM trend_scores
                 WHERE market = ?1 AND symbol = ?2
                 ORDER BY date DESC LIMIT 1",
            )
            .map_err(|e| MeridianError::Storage(format!("查询 trend_scores 失败: {e}")))?;

        let mut rows = stmt
            .query_map(params![asset.market.as_str(), asset.symbol], |row| {
                Ok((
                    row.get::<_, NaiveDate>(0)?,
                    row.get::<_, f64>(1)?,
                    row.get::<_, f64>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, Option<f64>>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, String>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                    row.get::<_, String>(9)?,
                ))
            })
            .map_err(|e| MeridianError::Storage(format!("查询 trend_scores 失败: {e}")))?;

        match rows.next() {
            None => Ok(None),
            Some(row) => {
                let (date, opp, risk, action, hint, triggers, opp_f, risk_f, ver, fp) = row
                    .map_err(|e| MeridianError::Storage(format!("读取 trend_scores 行失败: {e}")))?;
                let action: Action = action
                    .parse()
                    .map_err(|e| MeridianError::Storage(format!("还原 Action 失败: {e}")))?;
                let score = CompositeScore {
                    opportunity: OpportunityScore {
                        score: opp,
                        factors: parse_json(&opp_f, "opportunity_factors")?,
                    },
                    risk: RiskScore {
                        score: risk,
                        factors: parse_json(&risk_f, "risk_factors")?,
                    },
                    action: ActionOutput {
                        action,
                        position_hint: hint,
                        rule_triggers: parse_json(&triggers, "rule_triggers")?,
                    },
                    model_version: ver,
                    config_fingerprint: fp,
                };
                Ok(Some((date, score)))
            }
        }
    }

    /// 决策台账追加一行（append-only，审计语义：永不 UPSERT）。返回分配的行 id。
    #[allow(clippy::too_many_arguments)]
    pub fn insert_ledger_entry(
        &self,
        ts: &str,
        asset: &Asset,
        data_start: NaiveDate,
        data_end: NaiveDate,
        bar_count: i64,
        data_source: &str,
        fallback_reason: Option<&str>,
        regime: &str,
        score: &CompositeScore,
        report_path: Option<&str>,
    ) -> Result<i64> {
        let id: i64 = self
            .conn
            .query_row(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM decision_ledger",
                [],
                |r| r.get(0),
            )
            .map_err(|e| MeridianError::Storage(format!("分配台账 id 失败: {e}")))?;
        let triggers = serde_json::to_string(&score.action.rule_triggers)
            .map_err(|e| MeridianError::Storage(format!("序列化 rule_triggers 失败: {e}")))?;
        self.conn
            .execute(
                "INSERT INTO decision_ledger
                    (id, ts, symbol, name, market, asset_type, frequency,
                     data_start, data_end, bar_count, data_source, fallback_reason,
                     regime, opportunity, risk, action, position_hint,
                     rule_triggers, model_version, config_fingerprint, report_path)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                         ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21)",
                params![
                    id,
                    parse_ts(ts)?,
                    asset.symbol,
                    asset.name,
                    asset.market.as_str(),
                    asset.asset_type.as_str(),
                    asset.frequency.as_str(),
                    data_start,
                    data_end,
                    bar_count,
                    data_source,
                    fallback_reason,
                    regime,
                    score.opportunity.score,
                    score.risk.score,
                    score.action.action.as_str(),
                    score.action.position_hint,
                    triggers,
                    score.model_version,
                    score.config_fingerprint,
                    report_path,
                ],
            )
            .map_err(|e| MeridianError::Storage(format!("写入 decision_ledger 失败: {e}")))?;
        Ok(id)
    }

    /// 查询决策台账（按 id 倒序，id 越大越新）。market/symbol 传 None 表示不过滤。
    pub fn query_ledger(
        &self,
        market: Option<&str>,
        symbol: Option<&str>,
        limit: i64,
    ) -> Result<Vec<LedgerRow>> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, strftime(ts, '%Y-%m-%d %H:%M:%S') AS ts_str,
                        symbol, name, market, asset_type, frequency,
                        data_start, data_end, bar_count, data_source, fallback_reason,
                        regime, opportunity, risk, action, position_hint,
                        rule_triggers, model_version, config_fingerprint, report_path
                 FROM decision_ledger
                 WHERE (?1 IS NULL OR market = ?1) AND (?2 IS NULL OR symbol = ?2)
                 ORDER BY id DESC
                 LIMIT ?3",
            )
            .map_err(|e| MeridianError::Storage(format!("查询 decision_ledger 失败: {e}")))?;

        let rows = stmt
            .query_map(params![market, symbol, limit], |row| {
                Ok(LedgerRow {
                    id: row.get(0)?,
                    ts: row.get(1)?,
                    symbol: row.get(2)?,
                    name: row.get(3)?,
                    market: row.get(4)?,
                    asset_type: row.get(5)?,
                    frequency: row.get(6)?,
                    data_start: row.get(7)?,
                    data_end: row.get(8)?,
                    bar_count: row.get(9)?,
                    data_source: row.get(10)?,
                    fallback_reason: row.get(11)?,
                    regime: row.get(12)?,
                    opportunity: row.get(13)?,
                    risk: row.get(14)?,
                    action: row.get(15)?,
                    position_hint: row.get(16)?,
                    rule_triggers: row.get(17)?,
                    model_version: row.get(18)?,
                    config_fingerprint: row.get(19)?,
                    report_path: row.get(20)?,
                })
            })
            .map_err(|e| MeridianError::Storage(format!("查询 decision_ledger 失败: {e}")))?;

        rows.map(|r| r.map_err(|e| MeridianError::Storage(format!("读取台账行失败: {e}"))))
            .collect()
    }

    /// 人工决策/成交日志追加一行（append-only）。返回分配的流水 id。
    #[allow(clippy::too_many_arguments)]
    pub fn insert_trade_entry(
        &self,
        ts: &str,
        symbol: &str,
        market: &str,
        side: &str,
        quantity: Option<f64>,
        price: Option<f64>,
        note: Option<&str>,
        ledger_id: Option<i64>,
    ) -> Result<i64> {
        let id: i64 = self
            .conn
            .query_row(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM trade_journal",
                [],
                |r| r.get(0),
            )
            .map_err(|e| MeridianError::Storage(format!("分配流水 id 失败: {e}")))?;
        self.conn
            .execute(
                "INSERT INTO trade_journal
                    (id, ts, symbol, market, side, quantity, price, note, ledger_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![id, parse_ts(ts)?, symbol, market, side, quantity, price, note, ledger_id],
            )
            .map_err(|e| MeridianError::Storage(format!("写入 trade_journal 失败: {e}")))?;
        Ok(id)
    }

    /// 查询人工决策/成交日志（按 id 倒序）。market/symbol 传 None 表示不过滤。
    pub fn query_trades(
        &self,
        market: Option<&str>,
        symbol: Option<&str>,
        limit: i64,
    ) -> Result<Vec<TradeRow>> {
        let mut stmt = self
            .conn
            .prepare(
                "SELECT id, strftime(ts, '%Y-%m-%d %H:%M:%S') AS ts_str,
                        symbol, market, side, quantity, price, note, ledger_id
                 FROM trade_journal
                 WHERE (?1 IS NULL OR market = ?1) AND (?2 IS NULL OR symbol = ?2)
                 ORDER BY id DESC
                 LIMIT ?3",
            )
            .map_err(|e| MeridianError::Storage(format!("查询 trade_journal 失败: {e}")))?;

        let rows = stmt
            .query_map(params![market, symbol, limit], |row| {
                Ok(TradeRow {
                    id: row.get(0)?,
                    ts: row.get(1)?,
                    symbol: row.get(2)?,
                    market: row.get(3)?,
                    side: row.get(4)?,
                    quantity: row.get(5)?,
                    price: row.get(6)?,
                    note: row.get(7)?,
                    ledger_id: row.get(8)?,
                })
            })
            .map_err(|e| MeridianError::Storage(format!("查询 trade_journal 失败: {e}")))?;

        rows.map(|r| r.map_err(|e| MeridianError::Storage(format!("读取流水行失败: {e}"))))
            .collect()
    }
}

fn parse_json<T: serde::de::DeserializeOwned>(s: &str, field: &str) -> Result<T> {
    serde_json::from_str(s).map_err(|e| MeridianError::Storage(format!("解析 {field} 失败: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use meridian_core::{AssetType, Channel, Frequency, Market};
    use meridian_quant_engine::{CompositeEngine, ScoringConfig};

    const YAML: &str = r#"
model_version: "rule-test"
weights:
  opportunity:
    default:
      trend_model: 0.6
      momentum_model: 0.4
  risk:
    default:
      risk_model: 1.0
action_rules:
  - if: {opportunity_gte: 70, risk_lte: 40}
    then: Add
  - default: Watch
"#;

    fn asset() -> Asset {
        Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily)
    }

    fn bars(n: usize) -> Vec<Bar> {
        let mut out = Vec::with_capacity(n);
        let mut prev_close = 99.0;
        for i in 0..n {
            let date = NaiveDate::from_ymd_opt(2020, 1, 1).unwrap() + chrono::Duration::days(i as i64);
            let close = 100.0 + i as f64;
            out.push(
                Bar::new(date, prev_close, close + 0.5, prev_close - 0.5, close, 1000.0 + i as f64, 0.0)
                    .unwrap(),
            );
            prev_close = close;
        }
        out
    }

    fn make_score() -> CompositeScore {
        let engine = CompositeEngine::new(ScoringConfig::from_yaml_str(YAML).unwrap());
        let a = asset();
        let bs = bars(130);
        let snapshot = meridian_indicators::build_snapshot(&bs);
        let ctx = meridian_core::AnalysisContext {
            asset: &a,
            regime: meridian_core::RegimeState::unknown(),
            bars: &bs,
            indicators: &snapshot,
        };
        let models = vec![
            meridian_quant_engine::RegisteredModel::new(
                Box::new(meridian_quant_engine::TrendModel::new()),
                Channel::Opportunity,
            ),
            meridian_quant_engine::RegisteredModel::new(
                Box::new(meridian_quant_engine::MomentumModel::new()),
                Channel::Opportunity,
            ),
            meridian_quant_engine::RegisteredModel::new(
                Box::new(meridian_quant_engine::RiskModel::new()),
                Channel::Risk,
            ),
        ];
        engine.evaluate(&models, &ctx).unwrap()
    }

    #[test]
    fn bars_insert_read_roundtrip() {
        let db = MeridianDb::open_in_memory().unwrap();
        let a = asset();
        let bs = bars(30);

        let written = db.insert_bars(&a, &bs).unwrap();
        assert_eq!(written, 30);

        let start = NaiveDate::from_ymd_opt(2020, 1, 1).unwrap();
        let end = NaiveDate::from_ymd_opt(2020, 1, 30).unwrap();
        let read = db.read_bars(&a, start, end).unwrap();
        assert_eq!(read.len(), 30);
        assert_eq!(read, bs);
    }

    #[test]
    fn bars_upsert_overwrites_and_keeps_single_row() {
        let db = MeridianDb::open_in_memory().unwrap();
        let a = asset();
        let bs = bars(5);
        db.insert_bars(&a, &bs).unwrap();

        // 同日改收盘价再写 → 覆盖而非新增
        let mut updated = bs.clone();
        updated[4].close = 999.0;
        // 直接构造改价 bar（close 999 与 high 冲突校验：high 也要同步改）
        updated[4].high = 999.5;
        db.insert_bars(&a, &updated).unwrap();

        let start = NaiveDate::from_ymd_opt(2020, 1, 1).unwrap();
        let end = NaiveDate::from_ymd_opt(2020, 1, 5).unwrap();
        let read = db.read_bars(&a, start, end).unwrap();
        assert_eq!(read.len(), 5);
        assert_eq!(read[4].close, 999.0);
    }

    #[test]
    fn bars_scope_isolation_by_market() {
        let db = MeridianDb::open_in_memory().unwrap();
        let a_cn = asset();
        let a_hk = Asset::new("600519", "HK同码", Market::Hk, AssetType::Stock, Frequency::Daily);
        db.insert_bars(&a_cn, &bars(3)).unwrap();
        assert_eq!(
            db.read_bars(
                &a_hk,
                NaiveDate::from_ymd_opt(2020, 1, 1).unwrap(),
                NaiveDate::from_ymd_opt(2020, 1, 3).unwrap()
            )
            .unwrap()
            .len(),
            0
        );
    }

    #[test]
    fn latest_bar_date_tracks_cursor() {
        let db = MeridianDb::open_in_memory().unwrap();
        let a = asset();
        assert_eq!(db.latest_bar_date("cn", "600519", "daily").unwrap(), None);

        db.insert_bars(&a, &bars(5)).unwrap();
        let latest = db.latest_bar_date("cn", "600519", "daily").unwrap();
        assert_eq!(latest, Some(bars(5).last().unwrap().date));

        // 其他市场/频率隔离
        assert_eq!(db.latest_bar_date("hk", "600519", "daily").unwrap(), None);
        assert_eq!(db.latest_bar_date("cn", "600519", "minute").unwrap(), None);
    }

    #[test]
    fn composite_score_roundtrip() {
        let db = MeridianDb::open_in_memory().unwrap();
        let a = asset();
        let score = make_score();
        let date = NaiveDate::from_ymd_opt(2026, 9, 3).unwrap();

        db.insert_composite_score(&a, date, &score).unwrap();

        let (d2, back) = db.latest_composite_score(&a).unwrap().expect("应有记录");
        assert_eq!(d2, date);
        assert_eq!(back, score);
        // 因子明细完整还原（可追溯）
        assert_eq!(back.opportunity.factors.len(), 2);
        assert_eq!(back.opportunity.factors, score.opportunity.factors);
    }

    #[test]
    fn latest_score_none_when_empty() {
        let db = MeridianDb::open_in_memory().unwrap();
        assert!(db.latest_composite_score(&asset()).unwrap().is_none());
    }

    // ---- 决策台账（做账）----

    fn ts_at(day: u32, hms: &str) -> String {
        format!("2026-09-{day:02} {hms}")
    }

    #[test]
    fn ledger_insert_assigns_sequential_ids_and_query_reads_back() {
        let db = MeridianDb::open_in_memory().unwrap();
        let a = asset();
        let score = make_score();

        let id1 = db
            .insert_ledger_entry(
                &ts_at(3, "09:30:00"),
                &a,
                NaiveDate::from_ymd_opt(2026, 5, 1).unwrap(),
                NaiveDate::from_ymd_opt(2026, 9, 2).unwrap(),
                130,
                "live",
                None,
                "unknown",
                &score,
                Some("reports/600519_2026-09-03.md"),
            )
            .unwrap();
        let id2 = db
            .insert_ledger_entry(
                &ts_at(4, "09:30:00"),
                &a,
                NaiveDate::from_ymd_opt(2026, 5, 1).unwrap(),
                NaiveDate::from_ymd_opt(2026, 9, 3).unwrap(),
                131,
                "cache",
                Some("数据源拉取失败，自动回退: 模拟"),
                "unknown",
                &score,
                None,
            )
            .unwrap();
        assert_eq!(id1, 1);
        assert_eq!(id2, 2, "append-only：id 顺序分配，永不复用");

        // 不带过滤：新→旧
        let all = db.query_ledger(None, None, 10).unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].id, 2);
        assert_eq!(all[0].ts, "2026-09-04 09:30:00");
        assert_eq!(all[0].data_source, "cache");
        assert_eq!(all[0].fallback_reason.as_deref(), Some("数据源拉取失败，自动回退: 模拟"));
        assert_eq!(all[0].action, "Add"); // 合成强势序列触发 Add 档
        assert_eq!(all[0].report_path, None);
        assert_eq!(all[1].id, 1);
        assert_eq!(all[1].report_path.as_deref(), Some("reports/600519_2026-09-03.md"));

        // 标的过滤 + limit 生效
        assert_eq!(db.query_ledger(Some("cn"), Some("600519"), 10).unwrap().len(), 2);
        assert_eq!(db.query_ledger(Some("cn"), Some("300750"), 10).unwrap().len(), 0);
        assert_eq!(db.query_ledger(Some("hk"), None, 10).unwrap().len(), 0);
        assert_eq!(db.query_ledger(None, None, 1).unwrap().len(), 1);
    }

    #[test]
    fn ledger_preserves_rule_triggers_json() {
        let db = MeridianDb::open_in_memory().unwrap();
        db.insert_ledger_entry(
            &ts_at(3, "10:00:00"),
            &asset(),
            NaiveDate::from_ymd_opt(2026, 5, 1).unwrap(),
            NaiveDate::from_ymd_opt(2026, 9, 2).unwrap(),
            130,
            "store",
            None,
            "unknown",
            &make_score(),
            None,
        )
        .unwrap();
        let row = &db.query_ledger(None, None, 1).unwrap()[0];
        let triggers: Vec<String> = serde_json::from_str(&row.rule_triggers).unwrap();
        assert!(!triggers.is_empty());
        assert_eq!(row.config_fingerprint.len(), 16);
        assert_eq!(row.model_version, "rule-test");
    }

    #[test]
    fn trade_journal_roundtrip_and_link() {
        let db = MeridianDb::open_in_memory().unwrap();
        let t1 = db
            .insert_trade_entry(
                &ts_at(4, "10:05:00"),
                "600519",
                "cn",
                "buy",
                Some(100.0),
                Some(1299.16),
                Some("跟随 Add 建议"),
                Some(1),
            )
            .unwrap();
        db.insert_trade_entry(&ts_at(4, "10:30:00"), "00700", "hk", "watch", None, None, None, None)
            .unwrap();

        let all = db.query_trades(None, None, 10).unwrap();
        assert_eq!(all.len(), 2);
        assert_eq!(all[0].id, t1 + 1); // 倒序：最新在前
        assert_eq!(all[1].id, t1);
        assert_eq!(all[1].symbol, "600519");
        assert_eq!(all[1].side, "buy");
        assert_eq!(all[1].quantity, Some(100.0));
        assert_eq!(all[1].price, Some(1299.16));
        assert_eq!(all[1].ledger_id, Some(1));

        assert_eq!(db.query_trades(Some("hk"), None, 10).unwrap().len(), 1);
        assert_eq!(db.query_trades(None, Some("600519"), 10).unwrap().len(), 1);
    }

    #[test]
    fn parse_ts_accepts_space_and_iso_t() {
        assert_eq!(
            parse_ts("2026-09-04 09:30:00").unwrap(),
            chrono::NaiveDate::from_ymd_opt(2026, 9, 4)
                .unwrap()
                .and_hms_opt(9, 30, 0)
                .unwrap()
        );
        assert!(parse_ts("2026-09-04T09:30:00").is_ok());
        assert!(parse_ts("2026/09/04").is_err());
    }
}
