use std::fmt;
use std::str::FromStr;

use serde::{Deserialize, Serialize};

use crate::error::MeridianError;

/// 交易市场。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Market {
    /// 中国大陆（A股 / 指数 / 基金）
    Cn,
    Us,
    Hk,
}

impl Market {
    pub fn as_str(&self) -> &'static str {
        match self {
            Market::Cn => "cn",
            Market::Us => "us",
            Market::Hk => "hk",
        }
    }
}

impl FromStr for Market {
    type Err = MeridianError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "cn" | "cn_stock" | "a_share" | "ashare" => Ok(Market::Cn),
            "us" | "us_stock" => Ok(Market::Us),
            "hk" | "hk_stock" => Ok(Market::Hk),
            other => Err(MeridianError::Data(format!("未知市场: {other}"))),
        }
    }
}

/// 资产类型 —— 决定评分配置 `config/scoring/{as_str}.yaml` 的选择。
/// 新增资产类型 = 新增一个 scoring yaml + markets 条目，不改核心代码（验收标准 7）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum AssetType {
    Stock,
    Index,
    Etf,
    Fund,
    Gold,
    Commodity,
    /// 期货（商品/金融期货合约与主力连续）
    Futures,
}

impl AssetType {
    pub fn as_str(&self) -> &'static str {
        match self {
            AssetType::Stock => "stock",
            AssetType::Index => "index",
            AssetType::Etf => "etf",
            AssetType::Fund => "fund",
            AssetType::Gold => "gold",
            AssetType::Commodity => "commodity",
            AssetType::Futures => "futures",
        }
    }

    /// 该资产类型对应的评分配置文件名。
    pub fn scoring_config(&self) -> String {
        format!("{}.yaml", self.as_str())
    }
}

impl FromStr for AssetType {
    type Err = MeridianError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "stock" => Ok(AssetType::Stock),
            "index" => Ok(AssetType::Index),
            "etf" => Ok(AssetType::Etf),
            "fund" => Ok(AssetType::Fund),
            "gold" => Ok(AssetType::Gold),
            "commodity" => Ok(AssetType::Commodity),
            "futures" | "future" => Ok(AssetType::Futures),
            other => Err(MeridianError::Data(format!("未知资产类型: {other}"))),
        }
    }
}

/// 数据频率。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Frequency {
    Daily,
    Minute,
}

impl Frequency {
    pub fn as_str(&self) -> &'static str {
        match self {
            Frequency::Daily => "daily",
            Frequency::Minute => "minute",
        }
    }
}

impl FromStr for Frequency {
    type Err = MeridianError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "daily" | "1d" | "day" => Ok(Frequency::Daily),
            "minute" | "1m" | "min" => Ok(Frequency::Minute),
            other => Err(MeridianError::Data(format!("未知频率: {other}"))),
        }
    }
}

/// 被分析资产的静态描述。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Asset {
    pub symbol: String,
    pub name: String,
    pub market: Market,
    pub asset_type: AssetType,
    pub frequency: Frequency,
}

impl Asset {
    pub fn new(
        symbol: impl Into<String>,
        name: impl Into<String>,
        market: Market,
        asset_type: AssetType,
        frequency: Frequency,
    ) -> Self {
        Self {
            symbol: symbol.into(),
            name: name.into(),
            market,
            asset_type,
            frequency,
        }
    }
}

impl fmt::Display for Asset {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}({})", self.name, self.symbol)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn market_parse_and_as_str() {
        assert_eq!("cn".parse::<Market>().unwrap(), Market::Cn);
        assert_eq!("A_SHARE".parse::<Market>().unwrap(), Market::Cn);
        assert_eq!("us_stock".parse::<Market>().unwrap(), Market::Us);
        assert_eq!(Market::Hk.as_str(), "hk");
        assert!("moon".parse::<Market>().is_err());
    }

    #[test]
    fn asset_type_maps_to_scoring_config() {
        assert_eq!(AssetType::Stock.scoring_config(), "stock.yaml");
        assert_eq!(AssetType::Gold.scoring_config(), "gold.yaml");
        assert_eq!(AssetType::Index.as_str(), "index");
        assert_eq!("ETF".parse::<AssetType>().unwrap(), AssetType::Etf);
        assert!("bond".parse::<AssetType>().is_err());
    }

    #[test]
    fn frequency_parse() {
        assert_eq!("daily".parse::<Frequency>().unwrap(), Frequency::Daily);
        assert_eq!("1m".parse::<Frequency>().unwrap(), Frequency::Minute);
        assert!("weekly".parse::<Frequency>().is_err());
    }

    #[test]
    fn asset_serde_roundtrip() {
        let asset =
            Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily);
        let json = serde_json::to_string(&asset).unwrap();
        assert!(json.contains("\"market\":\"cn\""));
        assert!(json.contains("\"asset_type\":\"stock\""));
        assert_eq!(serde_json::from_str::<Asset>(&json).unwrap(), asset);
    }

    #[test]
    fn asset_display() {
        let asset =
            Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily);
        assert_eq!(asset.to_string(), "贵州茅台(600519)");
    }
}
