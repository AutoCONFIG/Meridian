//! 基于 Bar 的指标：TR / ATR / OBV / ADX（Wilder 平滑族）。

use meridian_core::Bar;

fn true_range(bar: &Bar, prev_close: f64) -> f64 {
    (bar.high - bar.low)
        .max((bar.high - prev_close).abs())
        .max((bar.low - prev_close).abs())
}

/// 真实波幅序列（首根为 high − low）。
pub fn true_ranges(bars: &[Bar]) -> Vec<f64> {
    let mut out = Vec::with_capacity(bars.len());
    for (i, b) in bars.iter().enumerate() {
        if i == 0 {
            out.push(b.high - b.low);
        } else {
            out.push(true_range(b, bars[i - 1].close));
        }
    }
    out
}

/// ATR（Wilder 平滑）：首个值在索引 period-1（首 period 个 TR 的均值），随后递推。
pub fn atr(bars: &[Bar], period: usize) -> Vec<Option<f64>> {
    let n = bars.len();
    let mut out = vec![None; n];
    if period == 0 || n < period {
        return out;
    }
    let tr = true_ranges(bars);
    let p = period as f64;
    let mut prev = tr[..period].iter().sum::<f64>() / p;
    out[period - 1] = Some(prev);
    for i in period..n {
        prev = (prev * (p - 1.0) + tr[i]) / p;
        out[i] = Some(prev);
    }
    out
}

/// OBV 能量潮：obv[0] = 0，其后按收盘涨跌累计成交量（涨加跌减）。
pub fn obv(bars: &[Bar]) -> Vec<f64> {
    let mut out = Vec::with_capacity(bars.len());
    let mut acc = 0.0;
    for (i, b) in bars.iter().enumerate() {
        if i > 0 {
            let prev_close = bars[i - 1].close;
            if b.close > prev_close {
                acc += b.volume;
            } else if b.close < prev_close {
                acc -= b.volume;
            }
        }
        out.push(acc);
    }
    out
}

/// ADX / ±DI 结果集。
pub struct AdxSeries {
    pub plus_di: Vec<Option<f64>>,
    pub minus_di: Vec<Option<f64>>,
    pub adx: Vec<Option<f64>>,
}

struct DirMoves {
    plus: f64,
    minus: f64,
}

fn directional_moves(cur: &Bar, prev: &Bar) -> DirMoves {
    let up = cur.high - prev.high;
    let down = prev.low - cur.low;
    DirMoves {
        plus: if up > down && up > 0.0 { up } else { 0.0 },
        minus: if down > up && down > 0.0 { down } else { 0.0 },
    }
}

/// 平均趋向指数（Wilder）。
/// 首个 ±DI 在索引 period-1；首个 ADX 在索引 2·period−2（首 period 个 DX 均值），随后递推。
/// 全程无 NaN：TR 累计为 0（全部一字板）时 DI/DX 记 0。
pub fn adx(bars: &[Bar], period: usize) -> AdxSeries {
    let n = bars.len();
    let mut plus_di = vec![None; n];
    let mut minus_di = vec![None; n];
    let mut adx = vec![None; n];
    if period == 0 || n < period + 1 {
        return AdxSeries {
            plus_di,
            minus_di,
            adx,
        };
    }
    let p = period as f64;

    // 首窗累计（索引 0..=period-1）：TR 含首根 high−low，DM 首根记 0
    let (mut s_plus, mut s_minus) = (0.0f64, 0.0f64);
    let mut s_tr = bars[0].high - bars[0].low;
    for i in 1..period {
        let dm = directional_moves(&bars[i], &bars[i - 1]);
        s_plus += dm.plus;
        s_minus += dm.minus;
        s_tr += true_range(&bars[i], bars[i - 1].close);
    }

    let mut dxs: Vec<f64> = Vec::with_capacity(n);
    for i in (period - 1)..n {
        if i > period - 1 {
            let dm = directional_moves(&bars[i], &bars[i - 1]);
            s_tr += -s_tr / p + true_range(&bars[i], bars[i - 1].close);
            s_plus += -s_plus / p + dm.plus;
            s_minus += -s_minus / p + dm.minus;
        }
        let (pdi, mdi) = if s_tr.abs() < f64::EPSILON {
            (0.0, 0.0)
        } else {
            (100.0 * s_plus / s_tr, 100.0 * s_minus / s_tr)
        };
        plus_di[i] = Some(pdi);
        minus_di[i] = Some(mdi);

        let denom = pdi + mdi;
        let dx = if denom.abs() < f64::EPSILON {
            0.0
        } else {
            100.0 * (pdi - mdi).abs() / denom
        };
        dxs.push(dx);
        if dxs.len() == period {
            // 首 ADX = 首 period 个 DX 的均值（播种）
            adx[i] = Some(dxs.iter().sum::<f64>() / p);
        } else if dxs.len() > period {
            if let Some(prev) = adx[i - 1] {
                adx[i] = Some((prev * (p - 1.0) + dx) / p);
            }
        }
    }
    AdxSeries {
        plus_di,
        minus_di,
        adx,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::NaiveDate;

    fn approx(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "left={a} right={b}");
    }

    /// 手工K线：(high, low, close)。
    fn bar(i: u32, h: f64, l: f64, c: f64) -> Bar {
        Bar::new(
            NaiveDate::from_ymd_opt(2020, 1, 1).unwrap() + chrono::Duration::days(i as i64),
            c,
            h,
            l,
            c,
            100.0,
            100.0 * c,
        )
        .unwrap()
    }

    #[test]
    fn true_ranges_hand_computed() {
        let bars = vec![bar(0, 10.0, 9.0, 9.5), bar(1, 11.0, 10.0, 10.5)];
        let tr = true_ranges(&bars);
        approx(tr[0], 1.0);
        approx(tr[1], 1.5); // max(1, |11−9.5|, |10−9.5|)
    }

    #[test]
    fn atr_hand_computed() {
        // TR = [1, 1.5, 1.5]，period=2：ATR1 = 1.25，ATR2 = (1.25+1.5)/2 = 1.375
        let bars = vec![
            bar(0, 10.0, 9.0, 9.5),
            bar(1, 11.0, 10.0, 10.5),
            bar(2, 12.0, 11.0, 11.5),
        ];
        let out = atr(&bars, 2);
        assert_eq!(out[0], None);
        approx(out[1].unwrap(), 1.25);
        approx(out[2].unwrap(), 1.375);
    }

    #[test]
    fn obv_hand_computed() {
        let b0 = Bar::new(
            NaiveDate::from_ymd_opt(2020, 1, 1).unwrap(),
            10.0,
            10.2,
            9.8,
            10.0,
            100.0,
            1000.0,
        )
        .unwrap();
        let b1 = Bar::new(
            NaiveDate::from_ymd_opt(2020, 1, 2).unwrap(),
            10.5,
            11.2,
            10.4,
            11.0,
            200.0,
            2200.0,
        )
        .unwrap();
        let b2 = Bar::new(
            NaiveDate::from_ymd_opt(2020, 1, 3).unwrap(),
            11.0,
            11.1,
            10.4,
            10.5,
            300.0,
            3150.0,
        )
        .unwrap();
        let out = obv(&[b0, b1, b2]);
        assert_eq!(out, vec![0.0, 200.0, -100.0]);
    }

    #[test]
    fn adx_strong_uptrend_wilder_exact() {
        // 五根逐级抬高的K线（period=3，手工递推）：
        // TR=[1,1.5,1.5,1.5,1.5]，+DM=[0,1,1,1,1]，−DM 全 0
        // s_tr: 4 → 25/6 → 77/18；s_plus: 2 → 7/3 → 23/9
        // +DI: 50 → 56 → 4600/77；−DI 恒 0 → DX 恒 100 → ADX@4 = 100
        let bars = vec![
            bar(0, 10.0, 9.0, 9.5),
            bar(1, 11.0, 10.0, 10.5),
            bar(2, 12.0, 11.0, 11.5),
            bar(3, 13.0, 12.0, 12.5),
            bar(4, 14.0, 13.0, 13.5),
        ];
        let out = adx(&bars, 3);
        assert_eq!(out.plus_di[0], None);
        assert_eq!(out.plus_di[1], None);
        approx(out.plus_di[2].unwrap(), 50.0);
        approx(out.plus_di[3].unwrap(), 56.0);
        approx(out.plus_di[4].unwrap(), 4600.0 / 77.0);
        assert!(out.minus_di[4].unwrap() == 0.0);
        assert!(out.adx[..4].iter().all(|v| v.is_none()));
        approx(out.adx[4].unwrap(), 100.0);
    }

    #[test]
    fn adx_flat_series_no_nan() {
        // 全一字板：TR=0 → DI=0、DX=0、ADX=0，不得出现 NaN
        let bars: Vec<Bar> = (0..10).map(|i| bar(i, 10.0, 10.0, 10.0)).collect();
        let out = adx(&bars, 3);
        for v in out.plus_di.iter().flatten() {
            assert!(v.is_finite());
        }
        assert_eq!(out.adx[9], Some(0.0));
    }

    #[test]
    fn adx_downtrend_minus_di_dominates() {
        let bars = vec![
            bar(0, 11.0, 10.0, 10.5),
            bar(1, 10.0, 9.0, 9.5),
            bar(2, 9.0, 8.0, 8.5),
            bar(3, 8.0, 7.0, 7.5),
            bar(4, 7.0, 6.0, 6.5),
        ];
        let out = adx(&bars, 3);
        assert!(out.minus_di[4].unwrap() > 55.0);
        assert!(out.plus_di[4].unwrap() == 0.0);
        assert!(out.adx[4].unwrap() > 60.0);
    }
}
