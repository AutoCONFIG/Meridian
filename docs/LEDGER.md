# 决策台账（做账）规范 —— 为什么记、记什么、如何凭账证明

> 定位：软件只辅助判断、不代客交易。台账解决的是"证明"问题 ——
> **系统当时说了什么、依据什么数据、我实际做了什么、两者是否一致**，事后可完整复盘。

## 1. 为什么做账

- **证明系统说了什么**：每次 `analyze` 自动留痕一条系统建议（append-only，永不改写）。
  事后凭 `config_fingerprint` + 数据窗口重跑，机会/风险/建议应逐位一致 —— 建议**可复现**；
- **证明我做了什么**：实际决策/成交由用户手动补记（软件不做交易），
  `ledger_id` 回链系统建议行，形成"建议 → 操作"的证据链；
- **证明系统有没有用**：建议 × 实际操作的一致性对照 + 后续行情复盘，
  把"感觉系统挺准"变成有账可查的结论。

## 2. 两张账表（均在 data/meridian.duckdb，append-only）

### decision_ledger —— 系统建议留痕（自动写入）

每次 `analyze` 成功即追加一行，含：记录时刻 `ts`、标的、**数据窗口**（data_start/data_end/bar_count）、
**数据来源**（live/store/cache，回退原因）、三层评分（机会/风险/建议）、触发规则、
`model_version` 与 `config_fingerprint`（评分配置 sha256 前 16 位，改配置即换指纹，旧账不失真）。

与 `trend_scores` 的分工：`trend_scores` 按 (market, symbol, date) UPSERT 只存**最新**评分；
台账每次分析一行，历史建议**永不覆盖**（审计语义）。因子明细证据在当日 Markdown 报告中（reports/）。

### trade_journal —— 人工操作补记（手动写入）

| 字段 | 说明 |
| --- | --- |
| side | 实际操作：buy / sell / hold / watch |
| quantity / price | 成交数量 / 价格（观望可不填） |
| note | 决策理由 —— **如实记录是否跟随系统**，"没跟随"同样是有效账目 |
| ledger_id | 回链台账号（可选）—— 当时依据哪条系统建议 |

## 3. 每日做账流程

```bash
# ① 盘中/收盘后分析 → 报告落 reports/，系统建议自动入账
meridian analyze --symbol 600519

# ② 实际操作后立即补记（趁记忆新鲜，理由写清楚）
meridian journal --symbol 600519 --side buy --quantity 100 --price 1299.16 \
    --note "跟随 Add 建议，半仓" --ledger-id 3

# ③ 随时导出做账文档（系统建议 + 人工流水 + 一致性对照）
meridian ledger                                # → reports/ledger_2026-09-04.md
meridian ledger --format csv                   # 系统建议表 → Excel
meridian ledger --format trades                # 人工流水表
meridian ledger --symbol 600519 --limit 50     # 只看某标的最近 50 条
```

一致性对照规则：`Add×buy`、`Hold×hold`、`Reduce/Avoid×sell`、`Watch×watch` 记"一致"，
其余组合记"背离"。**背离不是错误** —— 系统建议仅供参考；台账的价值恰恰在于如实记录偏离，
防止事后只保留对自己有利的记忆。

## 4. 如何凭账证明

1. **复现建议**：取台账某行的 `config_fingerprint`（对应 config/scoring/{asset_type}.yaml 的历史版本）
   与数据窗口，重跑 `analyze --start --end`，三层评分应与账面逐位一致；
2. **交叉验证**：台账行与当日报告（`reports/{symbol}_{date}.md`，内含同样指纹与因子明细）互证；
3. **完整性**：两表 append-only、id 顺序分配 —— 行数只增不减。审计时发现缺号或断号即异常；
4. **数据效力**：`data_source=cache/store` 的建议基于本地库，`live` 为实时拉取；
   回退原因（fallback_reason）一并留痕，离线账与实时账效力可区分。

## 5. 纪律（做账有效性的前提）

- 不删行、不改行；补记错误时**追加更正流水**并在 note 注明（"更正 #N"）；
- 理由在操作当下写，不事后补写理由 —— 事后理由不是证据；
- 每笔实际操作都该有账；"这笔没记"比"亏钱"更伤复盘价值。
