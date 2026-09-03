# 数据源调研与接口实测报告（股票 / 期货）

> 实测日期：2026-09-03（周四，盘中）。环境：Windows + 系统代理（所有请求必须绕过代理，见 §5）。
> 复现方式：`.venv/Scripts/python scripts/probe_data_sources.py`，全部通道一次体检。

## 1. 结论速览

Meridian 需要的股票/期货数据**不需要从零逆向**——关键接口均已被社区逆向或本来就是开放的，
实测全部走通。真正的工作量在工程化封装（重试、编码、字段缩放、落库）。

| 数据 | 首选通道 | 备选 | 状态 |
|---|---|---|---|
| A股历史日K | akshare `stock_zh_a_hist`（东财源，已在用） | 东财 kline 直连 | ✅ |
| A股实时快照 | 新浪 `hq.sinajs.cn`（明文，无缩放坑） | 东财 push2 | ✅ |
| A股日K/分钟K（含当日） | 东财 push2his 直连 | pytdx | ✅ |
| A股五档/分笔 | pytdx（通达信协议，TCP 7709） | 新浪 `_i` 后缀（未验证） | ✅ |
| 期货历史日K | akshare `futures_main_sina` | 交易所官网 | ✅（主力日K滞后一天） |
| 期货分钟K | akshare `futures_zh_minute_sina`（新浪源） | — | ✅（带持仓量） |
| 期货实时快照（商品） | 东财 push2（secid=113.rbm 等） | 新浪 `nf_` | ✅ |
| 期货实时快照（股指/全品种兜底） | 新浪 `nf_IF0` 等 | — | ✅（东财不覆盖中金所） |
| 期货 tick 推送 | CTP MdApi（官方 SDK，非逆向） | — | 未实施（需仿真账号） |
| Level-2 逐笔委托 | — | — | ❌ 付费墙，免费无解 |
| 外盘期货 | — | — | ❌ 需专门源，另行调研 |
| 通达信扩展行情（期货，TCP 7727） | — | — | ❌ 本网络全部未连通 |

**不做的事**：券商 APP 下单协议逆向（本软件只做辅助决策，不交易；且涉资金风控对抗）。

## 2. 接口规范（全部为 2026-09-03 实测）

### 2.1 东财 K 线 `push2his.eastmoney.com`（A股；期货分钟线不支持）

```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get
    ?secid=1.600519        # 1.=沪, 0.=深, 113.=上期所, 114.=大商所, 115.=郑商所
    &klt=101               # K线周期: 101=日, 102=周, 1/5/15/30/60=分钟
    &fqt=1                 # 复权: 0=不复权, 1=前复权, 2=后复权
    &lmt=120&end=20500101  # ⚠ lmt 的值被忽略但参数必须存在（缺省→data:null）；返回当日全部bar，客户端自行截取
    &fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57
→ data.klines: ["2026-09-01,开,收,高,低,量,额", ...]  # ⚠ 顺序是 开,收,高,低（收盘在第二位！）
```

- 实测含当日实时 bar（盘中 14:34 请求已返回到 14:34 的 1 分钟K）。
- 单连接偶发被服务端断开（RemoteDisconnected / schannel close_notify），**必须带重试**；
  镜像 `92.push2his.eastmoney.com` 实测可用可作兜底（`33.push2his` 返回 rc:102 不可用）。
- **UA 必须是完整浏览器串**：裸 `Mozilla/5.0` 会被服务端直接掐连接（实测），换成完整
  Chrome UA 后立即恢复。
- 期货主连（`113.rbm`）分钟K 返回 rc:102 data:null —— 期货分钟走新浪（§2.4）。
- 中金所（secid=8.ifm）在 push2 无数据，股指用新浪。

### 2.2 东财实时快照 `push2.eastmoney.com`

```
GET https://push2.eastmoney.com/api/qt/stock/get
    ?secid=1.600519 | 113.rbm
    &fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f169,f170
```

字段映射（实测）：f43 最新 / f44 最高 / f45 最低 / f46 今开 / f47 成交量(手) / f48 成交额 /
f57 代码 / f58 名称 / f60 昨收 / f86 Unix秒时间戳 / f169 涨跌额 / f170 涨跌幅(×100)。

**⚠ 价格缩放规则不统一（关键坑）**：
- A股：f43=129912 → 1299.12（÷100，即 10^f152）；
- 期货：f43=3145 → 3145 元/吨（**不缩放**，尽管返回 f152=2）。
- **适配层策略**：东财快照的价格字段仅做参考/交叉校验，精确价格以新浪明文接口为准；
  或将 `f43/f60/f169/f170` 整组配合推出缩放（`涨跌幅×昨收 ≈ 最新价`）再采用。

### 2.3 新浪实时快照 `hq.sinajs.cn`

- **必须带 `Referer: https://finance.sina.com.cn`**，否则拒绝；响应 GBK 编码，需转码。
- A股（`list=sh600519`，实测）：`名称,今开,昨收,最新,最高,最低,买一价,卖一价,量(股),额,买一量,买一价,卖一量,卖一价,...`（明文浮点，无缩放坑）。
- 内盘商品（`list=nf_RB0`，`0`=主力连续；具体合约如 `nf_RB2610`，实测）：
  `名称,时间(HHMMSS),开,高,低,?,最新,买价,卖价,?,昨结算,买量,卖量,持仓量,成交量,市场,品种,日期,...`
  （确认位：开/高/低/最新/昨结算与 akshare 日K对账一致；`?` 位未逐一确认，落地时校准）。
- 股指（`list=nf_IF0`，实测）：**字段结构与商品不同**（开,高,低,最新,量,额,...，日期时间在第 36/37 位）。
  → 新浪期货原始字段序脆弱，落地建议：快照解析每季对账一次，或统一经 akshare 封装。

### 2.4 新浪期货 K 线（经 akshare，实测）

```python
ak.futures_main_sina(symbol="RB0", start_date="20260820", end_date="20260903")
# 主力连续日K：OHLC/成交量/持仓量/动态结算价；⚠ 滞后一天（当日bar收盘后才出）
ak.futures_zh_minute_sina(symbol="RB0", period="1")   # period: 1/5/15/30/60
# 实时分钟K（盘中 14:36 请求返回到 14:36），带持仓量列
```

### 2.5 通达信私有协议（pytdx，TCP 7709）——A股 tick/五档的免费通道

- 协议为二进制私有格式，社区已完成逆向（pytdx 库，纯 Python，可直接用；协议细节见其源码，如需可再移植 Rust）。
- **内置 `hq_hosts` 列表过时**（2026-09-03 实测 14 台中 7 台可达）。实测可用 IP（有时效性，接入时须先探活）：
  `115.238.90.165` `218.75.126.9` `60.12.136.250` `115.238.56.198` `180.153.18.170` `110.41.147.114` `122.51.120.217`（端口均 7709）
- 实测 API：
  - `get_security_quotes([(1,'600519')])`：实时快照+五档（price/bid1/ask1/vol…，market: 0=深 1=沪）；
  - `get_security_bars(category, market, code, start, count)`：category 9=日K（实测✓），1分钟=7/8（社区共识，接入时验证）；
  - `get_transaction_data`：分笔成交（3 秒级快照，非逐笔）。
- **注意：日K最后一根是当日实时累计值**（盘中 14:35 请求已返回 `2026-09-03 15:00` 行），适合做日线增量刷新。
- 扩展行情 `TdxExHq_API`（7727 端口，含期货）：本网络实测全部未连通，期货不走此路。

### 2.6 CTP（期货 tick 的官方路，本期未实施）

期货实时 tick 的正规通道是上期技术 CTP 的 **MdApi**（免费官方 SDK，非逆向；Python 绑定用
`openctp-tts` 或 vnpy 封装）。仿真环境：SimNow（上期技术官方，需注册）或 openctp 7×24 仿真。
0.5 秒级 tick 推送，覆盖含中金所。前置地址以官方公告为准（会变动）。

## 3. 工程 注意事项（实测踩坑记录）

1. **系统代理**：本机系统代理会拦截东财并导致 TLS 中断（`schannel: server closed abruptly` /
   `RemoteDisconnected`）。所有数据请求必须绕过：进程内设 `NO_PROXY=*`，requests 显式 `proxies={'http':None,'https':None}`。
2. **重试是必须的**：东财两个域名都有偶发断连；适配层沿用 `base.py` 的 `with_retry`。
   同时请求 UA 必须为完整浏览器串（裸 `Mozilla/5.0` 会被掐连接，见 §2.1）。
   **连续秒级连发会触发限流**（同主机快照接口连发即被掐，间隔 ≥1s 即正常），适配层需节流；
   `push2` 也有编号镜像（`1.push2` / `92.push2` / `99.push2` 实测可用），可与镜像轮换配合。
   **开盘/收盘时点是断连高发窗口**（15:00 收盘竞价后数秒内日K接口连续掐连接，约 20s 后恢复），
   EOD 批量拉取任务应避开整点或加重退避。
3. **编码**：新浪响应是 GBK，`r.encoding='gbk'` 后再解析。
4. **价格缩放**：见 §2.2，东财快照价格不直接信任。
5. **数据对账**：多源并存，落地时至少做一次交叉校验（东财 vs 新浪价格差 < 0.5%），防止单源脏数据。

## 4. Meridian 落地建议（后续 Phase 实施）

- `python/meridian/data/` 扩展（全部走 `DataSource` 抽象 + `with_retry`）：
  - `realtime.py`：`SinaRealtimeSource`（A股+期货快照，明文）+ `EastmoneyRealtimeSource`（辅助/交叉校验）；
  - `minute.py`：`EastmoneyMinuteSource`（A股分钟K）+ `SinaFuturesMinuteSource`（期货分钟K，含持仓量）；
  - `tdx.py`：`TdxSource`（五档/分笔；服务器探活 + 自动 failover + 可用 IP 配置化）；
  - `ctp_md.py`（Phase 2）：CTP MdApi tick → 分钟bar聚合 → 落库。
- 统一快照 schema：`symbol,name,last,open,high,low,pre_close,bid1,ask1,volume,amount,open_interest,ts`
  （A股 open_interest 置空）；期货 bar schema 在 `BAR_COLUMNS` 基础上加 `open_interest`（A股置空）。
- `config/data_sources.yaml` 增加 `sina_realtime` / `eastmoney` / `tdx` 条目，继承 retry 配置。
- Python venv 缺 `pip`（uv 管理），装包用 `uv pip install <pkg> -p .venv/Scripts/python.exe`。
