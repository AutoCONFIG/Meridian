use chrono::NaiveDate;
use serde::{Deserialize, Serialize};

use crate::error::{MeridianError, Result};

/// 单根K线。`volume` 单位为股/份，`amount` 单位为元。
/// `amount` 允许 NaN：部分数据源（腾讯日K/期货）无成交额字段，宁缺毋错；
/// 价格与 volume 仍要求有限（下游指标与模型依赖）。
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub date: NaiveDate,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub amount: f64,
}

impl Bar {
    /// 构造并校验合法性。
    pub fn new(
        date: NaiveDate,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        volume: f64,
        amount: f64,
    ) -> Result<Self> {
        let bar = Self {
            date,
            open,
            high,
            low,
            close,
            volume,
            amount,
        };
        bar.validate()?;
        Ok(bar)
    }

    /// 合法性校验：数值有限、价格为正、成交量额非负、OHLC 大小关系一致
    /// （high >= max(open, close) 且 low <= min(open, close)）。
    pub fn validate(&self) -> Result<()> {
        let prices = [self.open, self.high, self.low, self.close];
        if prices.iter().any(|v| !v.is_finite()) {
            return Err(MeridianError::InvalidBar(format!(
                "{} 存在非有限价格",
                self.date
            )));
        }
        if prices.iter().any(|v| *v <= 0.0) {
            return Err(MeridianError::InvalidBar(format!(
                "{} 存在非正价格: open={} high={} low={} close={}",
                self.date, self.open, self.high, self.low, self.close
            )));
        }
        // amount: NaN = 缺失（部分源无成交额字段），±inf 仍视为非法
        let amount_ok = self.amount.is_nan() || (self.amount.is_finite() && self.amount >= 0.0);
        if !self.volume.is_finite() || self.volume < 0.0 || !amount_ok {
            return Err(MeridianError::InvalidBar(format!(
                "{} 成交量/成交额非法: volume={} amount={}",
                self.date, self.volume, self.amount
            )));
        }
        let body_top = self.open.max(self.close);
        let body_bottom = self.open.min(self.close);
        if self.high < body_top || self.low > body_bottom || self.high < self.low {
            return Err(MeridianError::InvalidBar(format!(
                "{} OHLC关系非法: high={} low={} open={} close={}",
                self.date, self.high, self.low, self.open, self.close
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(y: i32, m: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, day).unwrap()
    }

    #[test]
    fn valid_bar_passes() {
        let bar =
            Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.5, 1_000_000.0, 10_500_000.0).unwrap();
        assert_eq!(bar.close, 10.5);
    }

    #[test]
    fn high_must_dominate_body() {
        // high < close → 非法
        let err = Bar::new(d(2024, 1, 2), 10.0, 10.4, 9.5, 10.5, 1.0, 1.0).unwrap_err();
        assert!(matches!(err, MeridianError::InvalidBar(_)));
        // low > open → 非法
        let err = Bar::new(d(2024, 1, 2), 10.0, 11.0, 10.1, 9.9, 1.0, 1.0).unwrap_err();
        assert!(matches!(err, MeridianError::InvalidBar(_)));
        // high < low → 非法
        let err = Bar::new(d(2024, 1, 2), 10.0, 9.0, 9.5, 9.5, 1.0, 1.0).unwrap_err();
        assert!(matches!(err, MeridianError::InvalidBar(_)));
    }

    #[test]
    fn nan_and_nonpositive_rejected() {
        assert!(Bar::new(d(2024, 1, 2), 10.0, f64::NAN, 9.5, 10.0, 1.0, 1.0).is_err());
        assert!(Bar::new(d(2024, 1, 2), 0.0, 11.0, 9.5, 10.0, 1.0, 1.0).is_err());
        assert!(Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.0, -1.0, 1.0).is_err());
        assert!(Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.0, 1.0, f64::INFINITY).is_err());
    }

    #[test]
    fn nan_amount_allowed_inf_rejected() {
        // 期货主连等无成交额源 → NaN 合法
        let bar = Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.5, 1.0, f64::NAN).unwrap();
        assert!(bar.amount.is_nan());
        // ±inf 仍非法（第 114 行既有断言依赖此行为）
        assert!(Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.5, 1.0, f64::INFINITY).is_err());
        assert!(Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.5, 1.0, f64::NEG_INFINITY).is_err());
    }

    #[test]
    fn serde_roundtrip_keeps_date() {
        let bar = Bar::new(d(2024, 1, 2), 10.0, 11.0, 9.5, 10.5, 1.0, 1.0).unwrap();
        let json = serde_json::to_string(&bar).unwrap();
        assert!(json.contains("\"date\":\"2024-01-02\""));
        assert_eq!(serde_json::from_str::<Bar>(&json).unwrap(), bar);
    }
}
