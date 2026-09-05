//! Meridian 回测层（一期：单标的、日频、事件驱动）。
//!
//! 设计约束：回测与实盘同构 —— 信号在 bar 收盘确认、撮合在下一 bar 开盘（防未来函数泄露）。
//! 目标仓位由策略层给出（NaN = 维持现状），本层只负责撮合与统计；action→仓位
//! 的策略语义在 Python/config 侧（ScoreBasedStrategy），本层不理解 action。
//! 一期简化：股数浮点（不做整百取整）、现金无利息、不做融资融券。

use chrono::NaiveDate;
use meridian_core::{Bar, MeridianError, Result};

/// 回测成本与账户参数（config/backtest.yaml 经 Python 传入）。
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestConfig {
    /// 初始资金（元）
    pub initial_cash: f64,
    /// 单边佣金率（按成交额）
    pub commission_rate: f64,
    /// 最低佣金（元/笔）
    pub min_commission: f64,
    /// 单边滑点率（买入价 = 开盘×(1+s)，卖出价 = 开盘×(1−s)）
    pub slippage_rate: f64,
    /// 年化天数（夏普/年化收益用）
    pub trading_days_per_year: f64,
}

impl Default for BacktestConfig {
    fn default() -> Self {
        Self {
            initial_cash: 1_000_000.0,
            commission_rate: 0.0003,
            min_commission: 5.0,
            slippage_rate: 0.001,
            trading_days_per_year: 252.0,
        }
    }
}

/// 一笔完整（已平仓）交易。
#[derive(Debug, Clone, PartialEq)]
pub struct TradeRecord {
    pub date_in: NaiveDate,
    pub price_in: f64,
    pub date_out: NaiveDate,
    pub price_out: f64,
    pub shares: f64,
    pub pnl: f64,
    pub pnl_pct: f64,
}

/// 回测绩效。
#[derive(Debug, Clone, PartialEq)]
pub struct BacktestResult {
    pub total_return: f64,
    pub annual_return: f64,
    /// 最大回撤（正数，如 0.18 = 18%）
    pub max_drawdown: f64,
    /// 日收益年化夏普（无风险利率 0）
    pub sharpe: f64,
    pub win_rate: f64,
    /// 盈亏比 = 平均盈利 / |平均亏损|（无亏损交易时为 f64::INFINITY）
    pub profit_loss_ratio: f64,
    pub trade_count: usize,
    pub final_equity: f64,
    /// 逐日净值（日期, 总权益）
    pub equity_curve: Vec<(NaiveDate, f64)>,
    pub trades: Vec<TradeRecord>,
}

#[derive(Debug, Clone, Copy)]
struct Position {
    shares: f64,
    avg_cost: f64, // 摊薄成本价（不含佣金——佣金已在现金里扣）
    since: NaiveDate, // 当前仓位首次建仓日（加仓/减仓不改，清仓后重置）
}

/// 事件驱动回测：T 日收盘信号 → T+1 开盘调仓 → 逐日收盘 mark-to-market。
///
/// `target_weights[i]` 为第 i 根K线收盘时的目标仓位比例（[0,1]）；
/// NaN 表示维持现状（Hold 语义），策略层只对想变动的日子给数。
pub fn run_backtest(
    bars: &[Bar],
    target_weights: &[f64],
    cfg: &BacktestConfig,
) -> Result<BacktestResult> {
    if bars.len() != target_weights.len() {
        return Err(MeridianError::Data(format!(
            "信号与K线数量不一致: {} vs {}",
            target_weights.len(),
            bars.len()
        )));
    }
    let n = bars.len();
    if n < 2 {
        return Err(MeridianError::Data(format!("K线不足（{n} 根），至少 2 根")));
    }

    let mut cash = cfg.initial_cash;
    let mut pos: Option<Position> = None;
    let mut equity_curve = Vec::with_capacity(n);
    let mut trades: Vec<TradeRecord> = Vec::new();
    // 待执行调仓：(信号索引, 目标权重)。信号 T 收盘确认 → T+1 开盘执行。
    let mut pending: Option<(usize, f64)> = None;

    for i in 0..n {
        let bar = &bars[i];

        // ---- 开盘：执行昨日收盘确认的调仓 ----
        if let Some((_, target)) = pending.take() {
            let price_buy = bar.open * (1.0 + cfg.slippage_rate);
            let price_sell = bar.open * (1.0 - cfg.slippage_rate);
            let equity_prev = cash + pos.map_or(0.0, |p| p.shares * bar.open);
            let target_value = target * equity_prev;
            let current_value = pos.map_or(0.0, |p| p.shares * bar.open);

            if target_value > current_value + 1e-9 {
                // 买入差额
                let budget = (target_value - current_value).min(cash);
                if budget > 1e-9 {
                    let fill = budget / price_buy;
                    let commission = (budget * cfg.commission_rate).max(cfg.min_commission);
                    let p = pos.get_or_insert_with(|| Position {
                        shares: 0.0,
                        avg_cost: 0.0,
                        since: bar.date,
                    });
                    p.avg_cost = (p.avg_cost * p.shares + price_buy * fill) / (p.shares + fill);
                    p.shares += fill;
                    cash -= budget + commission;
                }
            } else if current_value > target_value + 1e-9 {
                // 卖出差额（target=0 → 清仓）
                if let Some(p) = pos.as_mut() {
                    let sell_shares = if target <= 1e-9 {
                        p.shares
                    } else {
                        (current_value - target_value) / price_sell
                    }
                    .min(p.shares);
                    let proceeds = sell_shares * price_sell;
                    let commission = (proceeds * cfg.commission_rate).max(cfg.min_commission);
                    let pnl = sell_shares * (price_sell - p.avg_cost) - commission;
                    trades.push(TradeRecord {
                        date_in: p.since,
                        price_in: p.avg_cost,
                        date_out: bar.date,
                        price_out: price_sell,
                        shares: sell_shares,
                        pnl,
                        pnl_pct: pnl / (sell_shares * p.avg_cost).max(1e-9),
                    });
                    p.shares -= sell_shares;
                    cash += proceeds - commission;
                    if p.shares <= 1e-9 {
                        pos = None;
                    }
                }
            }
        }

        // ---- 收盘：记账 + 记录今日信号（明日执行）----
        let equity = cash + pos.map_or(0.0, |p| p.shares * bar.close);
        equity_curve.push((bar.date, equity));
        let w = target_weights[i];
        if i + 1 < n && w.is_finite() {
            pending = Some((i, w.clamp(0.0, 1.0)));
        }
    }

    // 期末仍有持仓 → 以末根收盘价虚拟平仓（进胜率统计，回测期完整）
    if let Some(p) = pos {
        let last = &bars[n - 1];
        let price = last.close * (1.0 - cfg.slippage_rate);
        let proceeds = p.shares * price;
        let commission = (proceeds * cfg.commission_rate).max(cfg.min_commission);
        let pnl = p.shares * (price - p.avg_cost) - commission;
        trades.push(TradeRecord {
            date_in: p.since,
            price_in: p.avg_cost,
            date_out: last.date,
            price_out: price,
            shares: p.shares,
            pnl,
            pnl_pct: pnl / (p.shares * p.avg_cost).max(1e-9),
        });
        cash += proceeds - commission;
        pos = None;
        equity_curve[n - 1] = (last.date, cash);
    }

    let final_equity = equity_curve.last().map(|(_, e)| *e).unwrap_or(cfg.initial_cash);
    let total_return = final_equity / cfg.initial_cash - 1.0;
    let days = (n - 1) as f64;
    let annual_return =
        (final_equity / cfg.initial_cash).powf(cfg.trading_days_per_year / days) - 1.0;

    let mut max_dd = 0.0f64;
    let mut peak = cfg.initial_cash;
    for (_, e) in &equity_curve {
        peak = peak.max(*e);
        if peak > 0.0 {
            max_dd = max_dd.max(1.0 - e / peak);
        }
    }

    // 日收益夏普
    let rets: Vec<f64> = equity_curve
        .windows(2)
        .map(|w| w[1].1 / w[0].1 - 1.0)
        .collect();
    let sharpe = if rets.len() > 1 {
        let mean = rets.iter().sum::<f64>() / rets.len() as f64;
        let var = rets.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (rets.len() - 1) as f64;
        let std = var.sqrt();
        if std > 1e-12 {
            mean / std * cfg.trading_days_per_year.sqrt()
        } else {
            0.0
        }
    } else {
        0.0
    };

    let wins: Vec<f64> = trades.iter().map(|t| t.pnl).filter(|p| *p > 0.0).collect();
    let losses: Vec<f64> = trades.iter().map(|t| t.pnl).filter(|p| *p < 0.0).collect();
    let win_rate = if trades.is_empty() {
        0.0
    } else {
        wins.len() as f64 / trades.len() as f64
    };
    let avg_win = if wins.is_empty() { 0.0 } else { wins.iter().sum::<f64>() / wins.len() as f64 };
    let avg_loss = if losses.is_empty() { 0.0 } else { losses.iter().sum::<f64>() / losses.len() as f64 };
    let profit_loss_ratio = if avg_loss < 0.0 { avg_win / -avg_loss } else { f64::INFINITY };

    Ok(BacktestResult {
        total_return,
        annual_return,
        max_drawdown: max_dd,
        sharpe,
        win_rate,
        profit_loss_ratio,
        trade_count: trades.len(),
        final_equity,
        equity_curve,
        trades,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bar(day: u32, open: f64, close: f64) -> Bar {
        Bar {
            date: NaiveDate::from_ymd_opt(2026, 1, day).unwrap(),
            open,
            high: open.max(close) * 1.01,
            low: open.min(close) * 0.99,
            close,
            volume: 1_000_000.0,
            amount: f64::NAN,
        }
    }

    #[test]
    fn buy_and_hold_ride_uptrend() {
        // 第0根收盘信号满仓 → 第1根开盘买入持有到期末
        let bars: Vec<Bar> =
            (0..10).map(|i| bar(i as u32 + 1, 100.0 + i as f64, 101.0 + i as f64)).collect();
        let mut w = vec![f64::NAN; 10];
        w[0] = 1.0;
        let r = run_backtest(&bars, &w, &BacktestConfig::default()).unwrap();

        assert!(r.total_return > 0.0);
        assert_eq!(r.trade_count, 1, "期末虚拟平仓计入一笔");
        assert!(r.win_rate == 1.0);
        assert!(r.max_drawdown < 0.05, "单调上涨回撤应极小");
        assert_eq!(r.equity_curve.len(), 10);
        assert_eq!(r.trades[0].date_in, bars[1].date, "T+1 开盘入场日应回填");
    }

    #[test]
    fn flat_signal_never_trades() {
        let bars: Vec<Bar> = (0..5).map(|i| bar(i as u32 + 1, 100.0, 100.0)).collect();
        let w = vec![f64::NAN; 5];
        let r = run_backtest(&bars, &w, &BacktestConfig::default()).unwrap();

        assert_eq!(r.trade_count, 0);
        assert_eq!(r.total_return, 0.0, "空仓不动 → 净值不变");
        assert_eq!(r.sharpe, 0.0);
    }

    #[test]
    fn avoid_liquidates_at_next_open() {
        // 满仓后第3日收盘 Avoid → 第4日开盘清仓（open 逐日抬升，保证 T+1 撮合有价差）
        let bars: Vec<Bar> =
            (0..6).map(|i| bar(i as u32 + 1, 100.0 + i as f64, 100.5 + i as f64)).collect();
        let mut w = vec![f64::NAN; 6];
        w[0] = 1.0;
        w[3] = 0.0;
        let r = run_backtest(&bars, &w, &BacktestConfig::default()).unwrap();

        assert_eq!(r.trade_count, 1);
        let t = &r.trades[0];
        assert_eq!(t.date_in, bars[1].date, "T+1 开盘入场");
        assert_eq!(t.date_out, bars[4].date, "信号日次日开盘清仓");
        assert!(t.pnl > 0.0, "101 开盘买入、104 开盘卖出应盈利");
    }

    #[test]
    fn costs_reduce_return() {
        // 同一上涨序列：含滑点佣金的总收益 < 理论无成本收益
        let bars: Vec<Bar> =
            (0..5).map(|i| bar(i as u32 + 1, 100.0, 100.0 + i as f64)).collect();
        let mut w = vec![f64::NAN; 5];
        w[0] = 1.0;
        let r = run_backtest(&bars, &w, &BacktestConfig::default()).unwrap();
        let gross = bars[4].close / bars[1].open - 1.0;
        assert!(r.total_return < gross, "成本应侵蚀收益");
    }

    #[test]
    fn loss_trade_records_drawdown_and_negative_pnl() {
        // 买入后下跌清仓：负收益 + 回撤
        let bars = vec![
            bar(1, 100.0, 100.0),
            bar(2, 100.0, 95.0),
            bar(3, 95.0, 90.0),
            bar(4, 90.0, 88.0),
            bar(5, 88.0, 88.0),
        ];
        let mut w = vec![f64::NAN; 5];
        w[0] = 1.0;
        w[2] = 0.0;
        let r = run_backtest(&bars, &w, &BacktestConfig::default()).unwrap();

        assert!(r.total_return < 0.0);
        assert!(r.max_drawdown > 0.05);
        assert_eq!(r.win_rate, 0.0);
        assert_eq!(r.profit_loss_ratio, 0.0, "只有亏损交易：无盈利 → 比值为 0");
    }

    #[test]
    fn length_mismatch_is_error() {
        let bars = vec![bar(1, 100.0, 100.0), bar(2, 100.0, 101.0)];
        assert!(run_backtest(&bars, &[1.0], &BacktestConfig::default()).is_err());
    }
}
