//! 模型实现内部共用的小工具。

use meridian_core::{Direction, Factor};

/// 机会通道分数 → 方向：>60 看多，<40 看空，其余中性。
pub(crate) fn direction_from_score(score: f64) -> Direction {
    if score > 60.0 {
        Direction::Up
    } else if score < 40.0 {
        Direction::Down
    } else {
        Direction::Neutral
    }
}

/// 置信度随有效因子数量增长：0 个因子 0.4，每 +1 个 +0.06，封顶 0.9。
pub(crate) fn confidence_from_factor_count(n: usize) -> f64 {
    (0.4 + 0.06 * n as f64).min(0.9)
}

/// Factor 构造的简写。
pub(crate) fn model_factor(
    name: &str,
    value: f64,
    contribution: f64,
    description: &str,
) -> Factor {
    Factor::new(name, value, contribution, description)
}
