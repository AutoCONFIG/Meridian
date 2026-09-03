//! 趋势类指标：sma / ema / macd / bollinger / rolling。

/// 简单移动平均。前 `period-1` 个位置为 None。
pub fn sma(values: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = values.len();
    let mut out = vec![None; n];
    if period == 0 || n < period {
        return out;
    }
    let p = period as f64;
    let mut sum: f64 = values[..period].iter().sum();
    out[period - 1] = Some(sum / p);
    for i in period..n {
        sum += values[i] - values[i - period];
        out[i] = Some(sum / p);
    }
    out
}

/// 指数移动平均：索引 period-1 处以首 period 均值（SMA）播种，
/// 随后 EMA_t = alpha·x_t + (1−alpha)·EMA_{t−1}，alpha = 2/(period+1)。
pub fn ema(values: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = values.len();
    let mut out = vec![None; n];
    if period == 0 || n < period {
        return out;
    }
    let alpha = 2.0 / (period as f64 + 1.0);
    let mut prev = values[..period].iter().sum::<f64>() / period as f64;
    out[period - 1] = Some(prev);
    for i in period..n {
        prev = alpha * values[i] + (1.0 - alpha) * prev;
        out[i] = Some(prev);
    }
    out
}

/// MACD 结果集：dif = EMA_fast − EMA_slow；dea = dif 的 EMA(signal)；hist = dif − dea。
/// （hist 采用国际惯例 DIF−DEA；国内行情软件常显示 2×(DIF−DEA)，仅影响柱高显示。）
pub struct MacdSeries {
    pub dif: Vec<Option<f64>>,
    pub dea: Vec<Option<f64>>,
    pub hist: Vec<Option<f64>>,
}

pub fn macd(closes: &[f64], fast: usize, slow: usize, signal: usize) -> MacdSeries {
    let n = closes.len();
    let mut dif = vec![None; n];
    let mut dea = vec![None; n];
    let mut hist = vec![None; n];
    let ema_fast = ema(closes, fast);
    let ema_slow = ema(closes, slow);

    let mut dif_start = None;
    for i in 0..n {
        if let (Some(f), Some(s)) = (ema_fast[i], ema_slow[i]) {
            dif[i] = Some(f - s);
            if dif_start.is_none() {
                dif_start = Some(i);
            }
        }
    }

    if let Some(start) = dif_start {
        let sub: Vec<f64> = dif[start..].iter().map(|v| v.unwrap_or(0.0)).collect();
        for (j, v) in ema(&sub, signal).into_iter().enumerate() {
            if let Some(x) = v {
                dea[start + j] = Some(x);
            }
        }
        for i in 0..n {
            if let (Some(d), Some(e)) = (dif[i], dea[i]) {
                hist[i] = Some(d - e);
            }
        }
    }
    MacdSeries { dif, dea, hist }
}

/// 布林带：中轨 = SMA(period)，上下轨 = 中轨 ± k·总体标准差（ddof=0）。
pub struct BollingerBands {
    pub mid: Vec<Option<f64>>,
    pub upper: Vec<Option<f64>>,
    pub lower: Vec<Option<f64>>,
}

pub fn bollinger(closes: &[f64], period: usize, k: f64) -> BollingerBands {
    let n = closes.len();
    let mut mid = vec![None; n];
    let mut upper = vec![None; n];
    let mut lower = vec![None; n];
    if period == 0 || n < period {
        return BollingerBands { mid, upper, lower };
    }
    let p = period as f64;
    for i in period - 1..n {
        let w = &closes[i + 1 - period..=i];
        let m = w.iter().sum::<f64>() / p;
        let var = w.iter().map(|x| (x - m) * (x - m)).sum::<f64>() / p;
        let sd = var.sqrt();
        mid[i] = Some(m);
        upper[i] = Some(m + k * sd);
        lower[i] = Some(m - k * sd);
    }
    BollingerBands { mid, upper, lower }
}

/// 滚动最大值（含当日）。前 `window-1` 个位置为 None。
pub fn rolling_max(values: &[f64], window: usize) -> Vec<Option<f64>> {
    let n = values.len();
    let mut out = vec![None; n];
    if window == 0 || n < window {
        return out;
    }
    for i in window - 1..n {
        out[i] = Some(
            values[i + 1 - window..=i]
                .iter()
                .copied()
                .fold(f64::NEG_INFINITY, f64::max),
        );
    }
    out
}

/// 滚动最小值（含当日）。前 `window-1` 个位置为 None。
pub fn rolling_min(values: &[f64], window: usize) -> Vec<Option<f64>> {
    let n = values.len();
    let mut out = vec![None; n];
    if window == 0 || n < window {
        return out;
    }
    for i in window - 1..n {
        out[i] = Some(
            values[i + 1 - window..=i]
                .iter()
                .copied()
                .fold(f64::INFINITY, f64::min),
        );
    }
    out
}

/// 滚动最大值（**不含当日**，窗口为 [i-window, i)）——用于"收盘突破前高"判定。
/// 首个有效值在索引 window。
pub fn rolling_max_prev(values: &[f64], window: usize) -> Vec<Option<f64>> {
    let n = values.len();
    let mut out = vec![None; n];
    if window == 0 {
        return out;
    }
    for i in window..n {
        out[i] = Some(
            values[i - window..i]
                .iter()
                .copied()
                .fold(f64::NEG_INFINITY, f64::max),
        );
    }
    out
}

/// 滚动最小值（**不含当日**，窗口为 [i-window, i)）——用于"收盘跌破前低"判定。
/// 首个有效值在索引 window。
pub fn rolling_min_prev(values: &[f64], window: usize) -> Vec<Option<f64>> {
    let n = values.len();
    let mut out = vec![None; n];
    if window == 0 {
        return out;
    }
    for i in window..n {
        out[i] = Some(
            values[i - window..i]
                .iter()
                .copied()
                .fold(f64::INFINITY, f64::min),
        );
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil;

    fn approx(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "left={a} right={b}");
    }

    #[test]
    fn sma_exact() {
        let out = sma(&[1.0, 2.0, 3.0, 4.0, 5.0], 3);
        assert_eq!(out[0], None);
        assert_eq!(out[1], None);
        approx(out[2].unwrap(), 2.0);
        approx(out[3].unwrap(), 3.0);
        approx(out[4].unwrap(), 4.0);
        // 窗口不足 → 全 None
        assert!(sma(&[1.0, 2.0], 3).iter().all(|v| v.is_none()));
        assert!(sma(&[], 3).is_empty());
        assert!(sma(&[1.0, 2.0, 3.0], 0).iter().all(|v| v.is_none()));
    }

    #[test]
    fn ema_exact_sma_seed() {
        // alpha = 2/(3+1) = 0.5；播种 = mean(2,4,6) = 4；E3 = 0.5·8 + 0.5·4 = 6
        let out = ema(&[2.0, 4.0, 6.0, 8.0], 3);
        assert_eq!(out[0], None);
        assert_eq!(out[1], None);
        approx(out[2].unwrap(), 4.0);
        approx(out[3].unwrap(), 6.0);
    }

    #[test]
    fn macd_structure_on_uptrend() {
        // 二次曲线（加速上涨）：线性序列下 dif 恒定、dea 会追平，严格大于不成立
        let closes: Vec<f64> = testutil::quadratic_bars(40, 100.0, 0.5)
            .iter()
            .map(|b| b.close)
            .collect();
        let m = macd(&closes, 12, 26, 9);
        // dif 首个有效值在索引 25（ema26 播种处）
        assert!(m.dif[..25].iter().all(|v| v.is_none()));
        assert!(m.dif[25].is_some());
        // 加速上涨：dif > 0 且高于其均值线 dea，hist = dif − dea > 0
        let dif_last = m.dif[39].unwrap();
        let dea_last = m.dea[39].unwrap();
        assert!(dif_last > 0.0);
        assert!(dif_last > dea_last);
        assert!(m.hist[39].unwrap() > 0.0);
    }

    #[test]
    fn bollinger_exact() {
        // [1..5] period=5：mid = 3，总体标准差 = √2
        let b = bollinger(&[1.0, 2.0, 3.0, 4.0, 5.0], 5, 2.0);
        approx(b.mid[4].unwrap(), 3.0);
        approx(b.upper[4].unwrap(), 3.0 + 2.0 * 2.0f64.sqrt());
        approx(b.lower[4].unwrap(), 3.0 - 2.0 * 2.0f64.sqrt());
        assert!(b.mid[..4].iter().all(|v| v.is_none()));
    }

    #[test]
    fn rolling_extremes() {
        let out = rolling_max(&[1.0, 3.0, 2.0, 5.0], 2);
        assert_eq!(out[0], None);
        assert_eq!(out[1], Some(3.0));
        assert_eq!(out[2], Some(3.0));
        assert_eq!(out[3], Some(5.0));
        let out = rolling_min(&[1.0, 3.0, 2.0, 5.0], 2);
        assert_eq!(out[3], Some(2.0));
    }

    #[test]
    fn rolling_prev_excludes_current() {
        let out = rolling_max_prev(&[1.0, 3.0, 2.0, 5.0], 2);
        assert_eq!(out[0], None);
        assert_eq!(out[1], None);
        assert_eq!(out[2], Some(3.0)); // max(1,3)，不含当日 2
        assert_eq!(out[3], Some(3.0)); // max(3,2)，不含当日 5
        let out = rolling_min_prev(&[1.0, 3.0, 2.0, 5.0], 2);
        assert_eq!(out[2], Some(1.0));
        assert_eq!(out[3], Some(2.0));
    }
}
