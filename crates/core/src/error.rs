use thiserror::Error;

/// Meridian 统一错误类型，所有 crate 对外返回 `Result<T, MeridianError>`。
/// 约定：失败必须明确报错，不静默吞错（设计原则，见 PLAN.md 第 12 节）。
#[derive(Debug, Error)]
pub enum MeridianError {
    #[error("非法K线: {0}")]
    InvalidBar(String),

    #[error("配置错误: {0}")]
    InvalidConfig(String),

    #[error("模型错误: {0}")]
    Model(String),

    #[error("存储错误: {0}")]
    Storage(String),

    #[error("数据错误: {0}")]
    Data(String),

    #[error("桥接错误: {0}")]
    Bridge(String),

    #[error("IO错误: {0}")]
    Io(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, MeridianError>;
