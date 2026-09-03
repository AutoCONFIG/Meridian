//! 统计与动量类指标：收益 / RSI / 回撤 / 年化波动。

/// 简单收益序列（等长，首元素为 0）：ret[i] = close[i]/close[i-1] − 1。
pub fn simple_returns(closes: &[f64]) -> Vec<f64> {
    let mut out = vec![0.0; closes.len()];
    for i in 1..closes.len() {
        out[i] = closes[i] / closes[i - 1] - 1.0;
    }
    out
}

/// n 日涨跌幅（t vs t−n），前 n 个位置为 None。
pub fn return_over(closes: &[f64], n: usize) -> Vec<Option<f64>> {
    let len = closes.len();
    let mut out = vec![None; len];
    for i in n..len {
        out[i] = Some(closes[i] / closes[i - n] - 1.0);
    }
    out
}

/// RSI（Wilder 平滑）。首个值在索引 period。
pub fn rsi(closes: &[f64], period: usize) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut out = vec![None; n];
    if period == 0 || n < period + 1 {
        return out;
    }
    let p = period as f64;
    let (mut avg_gain, mut avg_loss) = (0.0f64, 0.0f64);
    for i in 1..=period {
        let ch = closes[i] - closes[i - 1];
        if ch > 0.0 {
            avg_gain += ch;
        } else {
            avg_loss -= ch;
        }
    }
    avg_gain /= p;
    avg_loss /= p;
    out[period] = Some(rsi_from(avg_gain, avg_loss));
    for i in period + 1..n {
        let ch = closes[i] - closes[i - 1];
        avg_gain = (avg_gain * (p - 1.0) + ch.max(0.0)) / p;
        avg_loss = (avg_loss * (p - 1.0) + (-ch).max(0.0)) / p;
        out[i] = Some(rsi_from(avg_gain, avg_loss));
    }
    out
}

fn rsi_from(avg_gain: f64, avg_loss: f64) -> f64 {
    if avg_loss.abs() < f64::EPSILON {
        // 无下跌：全涨 = 100，完全走平 = 50
        return if avg_gain.abs() < f64::EPSILON { 50.0 } else { 100.0 };
    }
    let rs = avg_gain / avg_loss;
    100.0 - 100.0 / (1.0 + rs)
}

/// 相对历史最高收盘的回撤序列（<= 0）。
pub fn drawdown_series(closes: &[f64]) -> Vec<f64> {
    let mut peak = f64::NEG_INFINITY;
    closes
        .iter()
        .map(|c| {
            if *c > peak {
                peak = *c;
            }
            if peak > 0.0 {
                c / peak - 1.0
            } else {
                0.0
            }
        })
        .collect()
}

/// 最大回撤（正数，如 0.25 表示 25%）。
pub fn max_drawdown(closes: &[f64]) -> f64 {
    drawdown_series(closes).iter().copied().fold(0.0, f64::min).abs()
}

/// 滚动年化波动率：窗口内简单收益的样本标准差（ddof=1）× √periods_per_year。
/// 首个值在索引 window−1。
pub fn annualized_volatility(
    closes: &[f64],
    window: usize,
    periods_per_year: f64,
) -> Vec<Option<f64>> {
    let n = closes.len();
    let mut out = vec![None; n];
    if window < 2 || n < window {
        return out;
    }
    let rets = simple_returns(closes);
    let w = window as f64;
    for i in window - 1..n {
        let win = &rets[i + 1 - window..=i];
        let mean = win.iter().sum::<f64>() / w;
        let var = win.iter().map(|r| (r - mean) * (r - mean)).sum::<f64>() / (w - 1.0);
        out[i] = Some(var.sqrt() * periods_per_year.sqrt());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f64, b: f64) {
        assert!((a - b).abs() < 1e-9, "left={a} right={b}");
    }

    #[test]
    fn simple_returns_exact() {
        let r = simple_returns(&[100.0, 110.0, 121.0]);
        assert_eq!(r[0], 0.0);
        approx(r[1], 0.1);
        approx(r[2], 0.1);
    }

    #[test]
    fn return_over_exact() {
        let r = return_over(&[100.0, 110.0, 121.0], 2);
        assert_eq!(r[0], None);
        assert_eq!(r[1], None);
        approx(r[2].unwrap(), 0.21);
    }

    #[test]
    fn rsi_wilder_hand_computed() {
        // 手工算例（period=3）：
        // 前3个变化 +1,+1,-3 → avgGain=2/3, avgLoss=1 → RSI = 100·(2/3)/(5/3) = 40
        // 第4步 +1 → avgGain=7/9, avgLoss=2/3 → RS=7/6 → RSI = 700/13
        let out = rsi(&[10.0, 11.0, 12.0, 9.0, 10.0], 3);
        assert!(out[..3].iter().all(|v| v.is_none()));
        approx(out[3].unwrap(), 40.0);
        approx(out[4].unwrap(), 700.0 / 13.0);
    }

    #[test]
    fn rsi_extremes() {
        let up = rsi(&[1.0, 2.0, 3.0, 4.0, 5.0], 2);
        assert_eq!(up[2], Some(100.0));
        let down = rsi(&[5.0, 4.0, 3.0, 2.0, 1.0], 2);
        assert_eq!(down[2], Some(0.0));
        let flat = rsi(&[3.0, 3.0, 3.0, 3.0], 2);
        assert_eq!(flat[2], Some(50.0));
    }

    #[test]
    fn drawdown_exact() {
        let dd = drawdown_series(&[1.0, 2.0, 3.0, 2.0, 1.0]);
        assert_eq!(dd[0], 0.0);
        assert_eq!(dd[1], 0.0);
        assert_eq!(dd[2], 0.0);
        approx(dd[3], -1.0 / 3.0);
        approx(dd[4], -2.0 / 3.0);
        approx(max_drawdown(&[1.0, 2.0, 3.0, 2.0, 1.0]), 2.0 / 3.0);
    }

    #[test]
    fn annualized_vol_hand_computed() {
        // closes [100,110,121] window=3：rets=[0, 0.1, 0.1]
        // 样本方差 = (1/225 + 1/900 + 1/900)/2 = 1/300 → 年化 = √(252/300)
        let v = annualized_volatility(&[100.0, 110.0, 121.0], 3, 252.0);
        assert_eq!(v[0], None);
        assert_eq!(v[1], None);
        approx(v[2].unwrap(), (252.0f64 / 300.0).sqrt());
        // 窗口 < 2 → 全 None
        assert!(annualized_volatility(&[1.0, 2.0], 1, 252.0)
            .iter()
            .all(|v| v.is_none()));
    }
}
