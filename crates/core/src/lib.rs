//! Meridian 核心类型层（PLAN.md 第 5/6 节）。
//!
//! 依赖方向约定：本 crate 不依赖任何其他 meridian crate；
//! indicators / quant_engine / storage / pybind 依赖本 crate。

pub mod agent;
pub mod asset;
pub mod bar;
pub mod context;
pub mod error;
pub mod model;
pub mod order;
pub mod signal;

pub use agent::{AgentReport, ResearchAgent, ResearchContext};
pub use asset::{Asset, AssetType, Frequency, Market};
pub use bar::Bar;
pub use context::{AnalysisContext, IndicatorSnapshot};
pub use error::{MeridianError, Result};
pub use model::{AnalysisModel, Channel, ModelCategory};
pub use order::{Order, OrderType, Position, Side, Trade};
pub use signal::{
    Action, ActionOutput, CompositeScore, Direction, Factor, ModelOutput, OpportunityScore,
    Regime, RegimeState, RiskScore,
};
