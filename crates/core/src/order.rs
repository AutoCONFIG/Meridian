//! 订单与持仓类型 —— Phase 0 仅定义 + 单测，Phase 2 回测/模拟盘使用。
//!
//! 形状届时可微调，但方向不变：回测与实盘同构（同一 Strategy trait 下的输出）。

use serde::{Deserialize, Serialize};

use crate::error::{MeridianError, Result};

/// 买卖方向。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    pub fn as_str(&self) -> &'static str {
        match self {
            Side::Buy => "buy",
            Side::Sell => "sell",
        }
    }
}

/// 订单类型。
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OrderType {
    Market,
    /// 限价单，附带限定价
    Limit { price: f64 },
}

/// 一笔订单（Phase 2 由 Strategy 产生、Broker 撮合）。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Order {
    pub id: String,
    pub symbol: String,
    pub side: Side,
    pub order_type: OrderType,
    /// 数量（股/份），必须 > 0
    pub quantity: f64,
}

impl Order {
    /// 基本合法性检查。
    pub fn validate(&self) -> Result<()> {
        if !self.quantity.is_finite() || self.quantity <= 0.0 {
            return Err(MeridianError::Data(format!(
                "订单 {} 数量非法: {}",
                self.id, self.quantity
            )));
        }
        if let OrderType::Limit { price } = &self.order_type {
            if !price.is_finite() || *price <= 0.0 {
                return Err(MeridianError::Data(format!(
                    "订单 {} 限价非法: {}",
                    self.id, price
                )));
            }
        }
        Ok(())
    }
}

/// 一笔成交。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Trade {
    pub order_id: String,
    pub symbol: String,
    pub side: Side,
    pub price: f64,
    pub quantity: f64,
    /// 手续费（含佣金/印花税等，Phase 2 回测计费）
    pub commission: f64,
}

/// 单标的多头持仓。Phase 0 仅支持多头（做空持仓 Phase 2 回测里按需扩展）。
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Position {
    pub symbol: String,
    /// 持股数量（0 = 空仓）
    pub quantity: f64,
    /// 摊薄成本
    pub avg_cost: f64,
}

/// 数值比较容差（浮点持仓归零判断）。
const EPS: f64 = 1e-9;

impl Position {
    pub fn new(symbol: impl Into<String>) -> Self {
        Self {
            symbol: symbol.into(),
            quantity: 0.0,
            avg_cost: 0.0,
        }
    }

    /// 按成交更新持仓（买入摊薄成本，卖出降低数量）。
    pub fn apply_trade(&mut self, trade: &Trade) -> Result<()> {
        if trade.quantity <= 0.0 || !trade.quantity.is_finite() {
            return Err(MeridianError::Data(format!(
                "成交数量非法: {}",
                trade.quantity
            )));
        }
        match trade.side {
            Side::Buy => {
                let total_cost = self.avg_cost * self.quantity + trade.price * trade.quantity;
                self.quantity += trade.quantity;
                self.avg_cost = total_cost / self.quantity;
            }
            Side::Sell => {
                if trade.quantity > self.quantity + EPS {
                    return Err(MeridianError::Data(format!(
                        "{} 卖出数量 {} 超过持仓 {}",
                        self.symbol, trade.quantity, self.quantity
                    )));
                }
                self.quantity -= trade.quantity;
                if self.quantity <= EPS {
                    self.quantity = 0.0;
                    self.avg_cost = 0.0;
                }
            }
        }
        Ok(())
    }

    /// 按现价计算的市值。
    pub fn market_value(&self, price: f64) -> f64 {
        self.quantity * price
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trade(side: Side, price: f64, qty: f64) -> Trade {
        Trade {
            order_id: "t1".to_string(),
            symbol: "600519".to_string(),
            side,
            price,
            quantity: qty,
            commission: 0.0,
        }
    }

    #[test]
    fn buy_then_avg_cost_weighted() {
        let mut pos = Position::new("600519");
        pos.apply_trade(&trade(Side::Buy, 10.0, 100.0)).unwrap();
        pos.apply_trade(&trade(Side::Buy, 20.0, 100.0)).unwrap();
        assert_eq!(pos.quantity, 200.0);
        assert_eq!(pos.avg_cost, 15.0);
    }

    #[test]
    fn sell_keeps_avg_cost_until_flat() {
        let mut pos = Position::new("600519");
        pos.apply_trade(&trade(Side::Buy, 10.0, 100.0)).unwrap();
        pos.apply_trade(&trade(Side::Sell, 12.0, 40.0)).unwrap();
        assert_eq!(pos.quantity, 60.0);
        assert_eq!(pos.avg_cost, 10.0);
        pos.apply_trade(&trade(Side::Sell, 11.0, 60.0)).unwrap();
        assert_eq!(pos.quantity, 0.0);
        assert_eq!(pos.avg_cost, 0.0);
    }

    #[test]
    fn oversell_rejected() {
        let mut pos = Position::new("600519");
        pos.apply_trade(&trade(Side::Buy, 10.0, 100.0)).unwrap();
        let err = pos.apply_trade(&trade(Side::Sell, 12.0, 101.0)).unwrap_err();
        assert!(matches!(err, MeridianError::Data(_)));
    }

    #[test]
    fn order_validate() {
        let ok = Order {
            id: "o1".into(),
            symbol: "600519".into(),
            side: Side::Buy,
            order_type: OrderType::Limit { price: 10.0 },
            quantity: 100.0,
        };
        assert!(ok.validate().is_ok());

        let bad_qty = Order {
            quantity: 0.0,
            ..ok.clone()
        };
        assert!(bad_qty.validate().is_err());

        let bad_limit = Order {
            order_type: OrderType::Limit { price: -1.0 },
            ..ok
        };
        assert!(bad_limit.validate().is_err());
    }

    #[test]
    fn market_value() {
        let pos = Position {
            symbol: "600519".into(),
            quantity: 200.0,
            avg_cost: 15.0,
        };
        assert_eq!(pos.market_value(20.0), 4000.0);
    }
}
