//! DuckDB 连接与读写（热数据）：K 线与三层评分。

use std::path::Path;

use chrono::NaiveDate;
use duckdb::{params, Connection};
use meridian_core::{
    Action, ActionOutput, Asset, Bar, CompositeScore, MeridianError, OpportunityScore, Result,
    RiskScore,
};

use crate::schema::SCHEMA_SQL;

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
}
