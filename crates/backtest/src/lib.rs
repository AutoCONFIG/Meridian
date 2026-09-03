//! Meridian 回测层（Phase 2 实现：事件驱动回测、Walk Forward、绩效指标）。
//! 计划模块：broker / portfolio / engine / metrics / walk_forward。当前为占位。
//!
//! 设计约束：回测与实盘同构 —— 信号在 bar 收盘确认、撮合在下一 bar 开盘（防未来函数泄露）。
