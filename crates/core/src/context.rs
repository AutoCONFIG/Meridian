use serde::{Deserialize, Serialize};

use crate::asset::Asset;
use crate::bar::Bar;
use crate::signal::RegimeState;

/// 由 indicators crate 预计算并填充的指标快照。
/// 所有序列与 `bars` 等长、按日期升序对齐；窗口不足处为 `None`（不用 NaN）。
///
/// 注：类型定义在 core（依赖方向 core ← indicators，core 不能反向依赖），
/// 由 `indicators::snapshot::build_snapshot` 构造填充。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct IndicatorSnapshot {
    pub sma20: Vec<Option<f64>>,
    pub sma60: Vec<Option<f64>>,
    pub ema12: Vec<Option<f64>>,
    pub ema26: Vec<Option<f64>>,
    pub macd_dif: Vec<Option<f64>>,
    pub macd_dea: Vec<Option<f64>>,
    pub macd_hist: Vec<Option<f64>>,
    pub rsi14: Vec<Option<f64>>,
    pub atr14: Vec<Option<f64>>,
    pub adx14: Vec<Option<f64>>,
    pub plus_di14: Vec<Option<f64>>,
    pub minus_di14: Vec<Option<f64>>,
    pub boll_upper: Vec<Option<f64>>,
    pub boll_mid: Vec<Option<f64>>,
    pub boll_lower: Vec<Option<f64>>,
    pub obv: Vec<f64>,
    pub vol_ma20: Vec<Option<f64>>,
    /// 5日收盘涨跌幅
    pub ret_5d: Vec<Option<f64>>,
    /// 20日收盘涨跌幅
    pub ret_20d: Vec<Option<f64>>,
    /// 20日窗口年化波动率
    pub annual_vol_20: Vec<Option<f64>>,
    /// 相对历史最高收盘的回撤（<= 0）
    pub drawdown: Vec<f64>,
    /// 截至前一日的滚动20日最高价（不含当日，窗口 [i-20, i)，供突破判定）
    pub high_20d: Vec<Option<f64>>,
    /// 截至前一日的滚动20日最低价（不含当日，窗口 [i-20, i)，供跌破判定）
    pub low_20d: Vec<Option<f64>>,
}

impl IndicatorSnapshot {
    /// 取序列中最靠后的一个有效值（跳过末尾的 None）。
    pub fn last(series: &[Option<f64>]) -> Option<f64> {
        series.iter().rev().find_map(|v| *v)
    }

    /// 序列长度（各序列应与 bars 等长）。
    pub fn len(&self) -> usize {
        self.sma20.len()
    }

    pub fn is_empty(&self) -> bool {
        self.sma20.is_empty()
    }
}

/// 一次分析的全部输入（不可变快照）。
/// regime 贯穿其中；Phase 0 恒 Unknown，接口就位（验收标准 5）。
/// fundamentals / macro：Phase 1、3 以 Option 字段扩展。
#[derive(Debug)]
pub struct AnalysisContext<'a> {
    pub asset: &'a Asset,
    pub regime: RegimeState,
    /// 升序日频序列
    pub bars: &'a [Bar],
    pub indicators: &'a IndicatorSnapshot,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn last_skips_none() {
        let s = vec![None, Some(1.0), None, Some(3.5)];
        assert_eq!(IndicatorSnapshot::last(&s), Some(3.5));
        assert_eq!(IndicatorSnapshot::last(&[]), None);
        assert_eq!(IndicatorSnapshot::last(&[None, None]), None);
    }

    #[test]
    fn snapshot_default_is_empty() {
        let snap = IndicatorSnapshot::default();
        assert!(snap.is_empty());
        assert_eq!(snap.len(), 0);
    }

    #[test]
    fn snapshot_serde_roundtrip() {
        let mut snap = IndicatorSnapshot::default();
        snap.sma20 = vec![None, Some(2.0)];
        snap.drawdown = vec![0.0, -0.1];
        let json = serde_json::to_string(&snap).unwrap();
        let back: IndicatorSnapshot = serde_json::from_str(&json).unwrap();
        assert_eq!(back, snap);
    }
}
