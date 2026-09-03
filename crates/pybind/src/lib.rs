//! PyO3 绑定层：全部 Python 交互代码隔离于此 crate（PLAN.md 第 5/7 节）。
//!
//! - `py_model`：PyAnalysisModel——Python 模型 → Rust AnalysisModel 桥接
//! - `py_engine`：PyEngine（三层评分门面）+ PyDb（DuckDB 读写门面）
//! - `conversions`：Rust ↔ Python 类型转换
//!
//! 注：PLAN.md 原列 lib.rs / py_model.rs / conversions.rs 三个文件；
//! 实现时把引擎/存储门面独立为 py_engine.rs（执行适配记录见 docs/PLAN.md 第 13 节）。

mod conversions;
mod py_engine;
mod py_model;

use pyo3::prelude::*;

use py_engine::{PyDb, PyEngine};

/// 扩展模块版本（与 workspace 版本对齐）。
#[pyfunction]
fn meridian_core_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[pymodule]
fn meridian_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyEngine>()?;
    m.add_class::<PyDb>()?;
    m.add_function(wrap_pyfunction!(meridian_core_version, m)?)?;
    Ok(())
}
