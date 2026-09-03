//! Meridian 存储层：DuckDB（热数据/元数据）+ Parquet（冷数据，Hive 分区）。
//!
//! - `schema`：全部建表 SQL（幂等）
//! - `duckdb`：MeridianDb 连接封装，K 线与三层评分的读写（UPSERT 幂等）
//! - `parquet`：Hive 分区导出（year=/month=），COPY TO PARTITION_BY

mod duckdb;
mod parquet;
mod schema;

pub use duckdb::MeridianDb;
pub use schema::SCHEMA_SQL;
