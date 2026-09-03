//! Parquet 冷数据导出：Hive 分区（year=YYYY/month=MM），供长期归档与跨语言回读。

use std::path::Path;

use duckdb::params;
use meridian_core::{Asset, MeridianError, Result};

use crate::duckdb::MeridianDb;

impl MeridianDb {
    /// 将某标的的全部 K 线导出为 Hive 分区 Parquet：
    /// `out_dir/year=2020/month=01/data_0.parquet`。
    /// 返回导出行数；无数据时不落盘、返回 0。
    ///
    /// COPY 语句不支持绑定参数，标识符/路径经单引号转义后内联。
    pub fn export_bars_partitioned(&self, asset: &Asset, out_dir: &Path) -> Result<usize> {
        let count: i64 = self
            .conn()
            .query_row(
                "SELECT count(*) FROM bars
                 WHERE market = ?1 AND symbol = ?2 AND frequency = ?3",
                params![asset.market.as_str(), asset.symbol, asset.frequency.as_str()],
                |r| r.get(0),
            )
            .map_err(|e| MeridianError::Storage(format!("统计 bars 失败: {e}")))?;

        if count == 0 {
            return Ok(0);
        }

        // DuckDB SQL 字符串中反斜杠是转义符 → 路径统一正斜杠（Windows 亦接受）
        let dir = out_dir.display().to_string().replace('\\', "/").replace('\'', "''");
        let market = asset.market.as_str().replace('\'', "''");
        let symbol = asset.symbol.replace('\'', "''");
        let freq = asset.frequency.as_str().replace('\'', "''");

        self.conn()
            .execute_batch(&format!(
                "COPY (
                    SELECT date, open, high, low, close, volume, amount,
                           year(date) AS year,
                           lpad(CAST(month(date) AS VARCHAR), 2, '0') AS month
                    FROM bars
                    WHERE market = '{market}' AND symbol = '{symbol}' AND frequency = '{freq}'
                ) TO '{dir}'
                (FORMAT PARQUET, PARTITION_BY (year, month), OVERWRITE_OR_IGNORE);"
            ))
            .map_err(|e| MeridianError::Storage(format!("导出 Parquet 失败: {e}")))?;

        Ok(count as usize)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::duckdb::MeridianDb as Db;
    use meridian_core::{AssetType, Bar, Frequency, Market};
    use std::path::PathBuf;
    use chrono::NaiveDate;

    fn asset() -> Asset {
        Asset::new("600519", "贵州茅台", Market::Cn, AssetType::Stock, Frequency::Daily)
    }

    fn bars(n: usize) -> Vec<Bar> {
        let mut out = Vec::with_capacity(n);
        let mut prev_close = 99.0;
        for i in 0..n {
            let date =
                NaiveDate::from_ymd_opt(2020, 1, 1).unwrap() + chrono::Duration::days(i as i64);
            let close = 100.0 + i as f64;
            out.push(
                Bar::new(
                    date,
                    prev_close,
                    close + 0.5,
                    prev_close - 0.5,
                    close,
                    1000.0,
                    0.0,
                )
                .unwrap(),
            );
            prev_close = close;
        }
        out
    }

    #[test]
    fn export_hive_partitioned_and_read_back() {
        let db = Db::open_in_memory().unwrap();
        let a = asset();
        // 40 天数据：1 月 31 天 + 2 月 9 天 → 两个月份分区
        db.insert_bars(&a, &bars(40)).unwrap();

        let out_dir = std::env::temp_dir().join(format!(
            "meridian_parquet_test_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));

        let written = db.export_bars_partitioned(&a, &out_dir).unwrap();
        assert_eq!(written, 40);

        // Hive 分区目录结构存在（month 补零两位，与 Hive 生态惯例一致）
        let jan = out_dir.join("year=2020").join("month=01");
        assert!(jan.is_dir(), "缺少分区目录 {}", jan.display());
        let feb = out_dir.join("year=2020").join("month=02");
        assert!(feb.is_dir(), "缺少分区目录 {}", feb.display());

        // 用 glob 回读验证行数一致（路径统一正斜杠供 duckdb glob）
        let glob = format!("{}/year=*/month=*/*.parquet", out_dir.display().to_string().replace('\\', "/"));
        let read_back: i64 = db
            .conn()
            .query_row(
                &format!("SELECT count(*) FROM read_parquet('{glob}')"),
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(read_back, 40);

        let _ = std::fs::remove_dir_all(&out_dir);
    }

    #[test]
    fn export_empty_is_noop() {
        let db = Db::open_in_memory().unwrap();
        let out_dir = PathBuf::from(format!(
            "{}\\meridian_parquet_empty_test",
            std::env::temp_dir().display()
        ));
        assert_eq!(db.export_bars_partitioned(&asset(), &out_dir).unwrap(), 0);
    }
}
