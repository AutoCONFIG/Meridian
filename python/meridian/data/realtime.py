"""实时快照多源适配层：新浪 / 腾讯 / 东财，多渠道互备 + 跨源对账。

设计（docs/DATA_SOURCES.md §4）：
- 各源的报文解析为纯函数（离线可测），HTTP 细节封在 Source 类内；
- MultiSourceSnapshot 按顺序 failover；可选跨源对账：第二可用源成功时校验
  最新价偏差，超差抛 DataError（失败明确报错，不静默吞错）。

统一快照 schema（SNAPSHOT_COLUMNS，列名与顺序固定）：
    symbol / name / last / open / high / low / pre_close / volume / amount
    / open_interest / ts
- 价格均为元（各源缩放差异在解析层抹平，规则见 DATA_SOURCES.md §2.2）；
- volume 统一为"手"（与日K BAR schema 的 akshare 口径一致；新浪A股为股，÷100）；
- 期货 pre_close 取昨结算价；字段未确认处宁缺毋错（NaN）；
- ts 为源端时间（北京时间字符串）；源不带时间时取本机拉取时刻。
"""

from __future__ import annotations

import json
import os
import re
import time
import warnings
from datetime import datetime
from typing import Sequence

import pandas as pd
import requests

from meridian.config import DataSourceConfig
from meridian.data.base import DataError, SourceHealth, with_retry

# 系统代理会拦截东财并造成 TLS 中断，数据请求一律直连（DATA_SOURCES.md §3.1）
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

SNAPSHOT_COLUMNS = [
    "symbol", "name", "last", "open", "high", "low", "pre_close",
    "volume", "amount", "open_interest", "ts",
]

# 东财请求 UA 必须为完整浏览器串（裸 UA 会被掐连接，DATA_SOURCES.md §2.1）
_UA_FULL = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 期货合约形态：品种 1-2 位字母 + 月份 1-4 位数字（RB0 / RB2610 / MA601 / IF0）
_FUTURES_RE = re.compile(r"^[A-Za-z]{1,2}\d{1,4}$")

# 报文内嵌日期字段（YYYY-MM-DD）
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 东财期货市场代码（CFFEX 实测在东财无快照数据，不支持）
_EM_MARKET_IDS = {"SHFE": "113", "DCE": "114", "CZCE": "115", "INE": "142"}


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def is_futures(symbol: str) -> bool:
    return bool(_FUTURES_RE.match(symbol.strip()))


def cn_symbol_prefix(symbol: str) -> str:
    """A股/场内基金 6 位代码 → 交易所前缀（按首位数字特征猜，不设白名单）。

    沪：5(基金)/6(股票)/9(B股)；深：0(股票)/1(基金)/2(B股)/3(创业)；4/8(北交所)→bj。
    猜错渠道由上游报"无数据"，不在本层拒绝——尽力支持一切代码。
    """
    s = symbol.strip()
    if len(s) != 6 or not s.isdigit():
        raise DataError(f"代码格式应为 6 位数字: {symbol}")
    first = s[0]
    if first in "569":
        return "sh"
    if first in "0123":
        return "sz"
    if first in "48":
        return "bj"
    return "sh"  # 兜底猜测，拿不到数据由数据源报错


def em_futures_secid(symbol: str, exchange: str) -> str:
    """期货代码 → 东财 secid。主力连续（len==3 且 0 结尾，如 RB0）→ rbm 形式。"""
    market = _EM_MARKET_IDS.get(exchange.upper())
    if market is None:
        raise DataError(f"暂不支持的期货交易所: {exchange}（中金所东财无数据，用新浪）")
    code = symbol.strip().upper()
    if len(code) == 3 and code.endswith("0"):
        code = code[:-1] + "M"  # 主力连续: RB0 → RBM
    return f"{market}.{code.lower()}"


def _http_get(url: str, *, referer: str, encoding: str | None = None, tries: int = 3) -> str:
    headers = {"User-Agent": _UA_FULL, "Referer": referer}
    last: Exception | None = None
    for attempt in range(tries):
        try:
            r = requests.get(
                url, headers=headers, timeout=8,
                proxies={"http": None, "https": None},
            )
            if encoding:
                r.encoding = encoding
            return r.text
        except Exception as exc:  # noqa: BLE001 —— 偶发断连必须重试
            last = exc
            if attempt < tries - 1:
                time.sleep(0.5 * (attempt + 1))
    raise DataError(f"HTTP 请求失败（重试 {tries} 次）: {url} :: {last}")


# ---------------- 新浪 ----------------


def parse_sina_quotes(raw: str) -> dict[str, dict]:
    """新浪 hq.sinajs.cn 报文 → {symbol: 行数据}。按前缀路由 A股 / 期货。"""
    rows: dict[str, dict] = {}
    for m in re.finditer(r'hq_str_(\w+)="([^"]*)"', raw):
        key, body = m.group(1), m.group(2)
        if not body:
            continue
        if key.startswith("nf_"):
            rows[key[3:]] = _parse_sina_futures(body, key[3:])
        elif key.startswith(("sh", "sz")):
            rows[key[2:]] = _parse_sina_a_share(body, key[2:])
    return rows


def _parse_sina_a_share(body: str, symbol: str) -> dict:
    f = body.split(",")
    # 0名称 1今开 2昨收 3最新 4最高 5最低 6买一价 7卖一价 8量(股) 9额(元)
    return {
        "symbol": symbol, "name": f[0],
        "open": float(f[1]), "pre_close": float(f[2]), "last": float(f[3]),
        "high": float(f[4]), "low": float(f[5]),
        "volume": float(f[8]) / 100.0,  # 股 → 手
        "amount": float(f[9]), "open_interest": float("nan"),
        "ts": _now_str(),  # 新浪 A股报文不含时间，取拉取时刻
    }


def _parse_sina_futures(body: str, symbol: str) -> dict:
    f = body.split(",")
    if _is_cffex(f):
        # 股指结构: 0开 1高 2低 3最新 4量 5额 …末尾有 日期/时间 与名称；
        # 字段位随品种浮动 → 日期动态定位，名称取末尾首个非数值字段
        date_i = next(i for i, v in enumerate(f) if _DATE_RE.match(v))
        # 名称在报文末尾（日期之后），需全文倒查并排除日期/时间字段
        name = next(
            (v for v in reversed(f)
             if v and not _is_num(v) and not _DATE_RE.match(v) and ":" not in v),
            symbol,
        )
        return {
            "symbol": symbol, "name": name,
            "open": float(f[0]), "high": float(f[1]), "low": float(f[2]),
            "last": float(f[3]), "volume": float(f[4]), "amount": float(f[5]),
            "pre_close": float("nan"), "open_interest": float("nan"),
            "ts": f"{f[date_i]} {f[date_i + 1]}",
        }
    # 商品结构: 0名称 1时间 2开 3高 4低 5? 6最新 7买 8卖 9? 10昨结算
    #           11买量 12卖量 13持仓 14成交量 15市场 16品种 17日期
    hhmmss = f[1].zfill(6)
    return {
        "symbol": symbol, "name": f[0],
        "open": float(f[2]), "high": float(f[3]), "low": float(f[4]),
        "last": float(f[6]), "pre_close": float(f[10]),  # 期货昨收=昨结算
        "volume": float(f[14]), "amount": float("nan"),
        "open_interest": float(f[13]),
        "ts": f"{f[17]} {hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}",
    }


def _is_cffex(fields: list[str]) -> bool:
    """股指报文首字段即开盘价（数值）且含日期字段；商品首字段为名称。"""
    if len(fields) < 20 or not _is_num(fields[0]):
        return False
    return any(_DATE_RE.match(v) for v in fields)


def _is_num(v: str) -> bool:
    try:
        float(v)
    except ValueError:
        return False
    return True


class SinaRealtimeSource:
    """新浪实时快照：A股（sh/sz）+ 内盘期货（nf_，含股指）。"""

    name = "sina"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        keys = []
        for s in symbols:
            if is_futures(s):
                keys.append(f"nf_{s.strip().upper()}")
            else:
                keys.append(f"{cn_symbol_prefix(s)}{s.strip()}")
        raw = _http_get(
            "https://hq.sinajs.cn/list=" + ",".join(keys),
            referer="https://finance.sina.com.cn", encoding="gbk",
        )
        rows = parse_sina_quotes(raw)
        missing = [s for s in symbols if _canon(s) not in rows]
        if missing:
            raise DataError(f"新浪快照缺少品种 {missing}（接口或代码规则变更）")
        df = pd.DataFrame([rows[_canon(s)] for s in symbols])
        return df[SNAPSHOT_COLUMNS]


def _canon(symbol: str) -> str:
    return symbol.strip().upper() if is_futures(symbol) else symbol.strip()


# ---------------- 腾讯 ----------------


def _tx_ts(t: str) -> str:
    """腾讯时间字段 → "YYYY-MM-DD HH:MM:SS"。A股为数字串，港/美股含斜杠。"""
    t = t.strip()
    if "/" in t:
        return t.replace("/", "-")
    return f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}"


def parse_tencent_quotes(raw: str) -> dict[str, dict]:
    """腾讯 qt.gtimg.cn 报文 → {symbol: 行}。A股与港股共用此布局。

    A股: 1名称 3最新 4昨收 5今开 30时间 33高 34低 35=价/量/额 36量(手)
    港股: 35=最新价、36量(股)、37额（无斜杠分隔结构）
    """
    rows: dict[str, dict] = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', raw):
        body = m.group(2)
        if not body or "~" not in body:
            continue
        f = body.split("~")
        if len(f) < 36:
            continue
        pv = f[35].split("/")
        if len(pv) == 3:
            amount = float(pv[2])  # A股: 价/量/额
        else:
            amount = float(f[37]) if len(f) > 37 and f[37] else float("nan")  # 港股
        rows[f[2]] = {
            "symbol": f[2], "name": f[1],
            "last": float(f[3]), "pre_close": float(f[4]), "open": float(f[5]),
            "high": float(f[33]), "low": float(f[34]),
            "volume": float(f[36]),  # A股=手，港股=股（口径见 DATA_SOURCES.md）
            "amount": amount, "open_interest": float("nan"),
            "ts": _tx_ts(f[30]),
        }
    return rows


class TencentRealtimeSource:
    """腾讯实时快照：仅 A股（qt.gtimg.cn，明文 GBK，无五档）。"""

    name = "tencent"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        keys = [f"{cn_symbol_prefix(s)}{s.strip()}" for s in symbols]
        if any(is_futures(s) for s in symbols):
            raise DataError("腾讯快照不支持期货（请用新浪/东财）")
        raw = _http_get("https://qt.gtimg.cn/q=" + ",".join(keys), referer="https://gu.qq.com/", encoding="gbk")
        rows = parse_tencent_quotes(raw)
        missing = [s for s in symbols if s.strip() not in rows]
        if missing:
            raise DataError(f"腾讯快照缺少品种 {missing}（接口或代码规则变更）")
        df = pd.DataFrame([rows[s.strip()] for s in symbols])
        return df[SNAPSHOT_COLUMNS]


# ---------------- 东财 ----------------


def _em_fetch(secids: Sequence[str], fields: str, *, throttle_seconds: float) -> list[dict]:
    out: list[dict] = []
    for i, secid in enumerate(secids):
        if i:
            time.sleep(throttle_seconds)  # 东财对秒级连发限流
        u = (
            "https://push2.eastmoney.com/api/qt/stock/get"
            f"?secid={secid}&fields={fields}"
        )
        payload = json.loads(_http_get(u, referer="https://quote.eastmoney.com/"))
        data = payload.get("data")
        if not data:
            raise DataError(f"东财快照无数据: {secid} (rc={payload.get('rc')})")
        out.append(data)
    return out


class EastmoneyRealtimeSource:
    """东财实时快照：仅 A股（push2，价格按 ÷100 缩放）。"""

    name = "eastmoney"

    _FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86"

    def __init__(self, cfg: DataSourceConfig | None = None, throttle_seconds: float = 1.0):
        self._cfg = cfg or DataSourceConfig.load()
        self._throttle = throttle_seconds

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        if any(is_futures(s) for s in symbols):
            raise DataError("东财 A股快照源不支持期货（请用 EastmoneyFuturesRealtimeSource）")
        secids = [f"{1 if cn_symbol_prefix(s) == 'sh' else 0}.{s.strip()}" for s in symbols]
        data = with_retry(
            lambda: _em_fetch(secids, self._FIELDS, throttle_seconds=self._throttle),
            self._cfg.retry_for(self.name), what=f"东财A股快照 {list(symbols)}",
        )
        rows = []
        for d in data:
            rows.append({
                "symbol": d["f57"], "name": d["f58"],
                "last": d["f43"] / 100.0, "pre_close": d["f60"] / 100.0,
                "open": d["f46"] / 100.0, "high": d["f44"] / 100.0, "low": d["f45"] / 100.0,
                "volume": float(d["f47"]),  # 手
                "amount": float(d["f48"]), "open_interest": float("nan"),
                "ts": datetime.fromtimestamp(d["f86"]).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return pd.DataFrame(rows)[SNAPSHOT_COLUMNS]


class EastmoneyFuturesRealtimeSource:
    """东财商品期货实时快照（f43 即原价，不缩放；中金所不支持）。"""

    name = "eastmoney_futures"

    _FIELDS = "f43,f44,f45,f46,f47,f57,f58,f60,f86"

    def __init__(self, exchange: str = "SHFE", cfg: DataSourceConfig | None = None,
                 throttle_seconds: float = 1.0):
        self._exchange = exchange
        self._cfg = cfg or DataSourceConfig.load()
        self._throttle = throttle_seconds

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        secids = [em_futures_secid(s, self._exchange) for s in symbols]
        data = with_retry(
            lambda: _em_fetch(secids, self._FIELDS, throttle_seconds=self._throttle),
            self._cfg.retry_for(self.name), what=f"东财期货快照 {list(symbols)}",
        )
        rows = []
        for d, sym in zip(data, symbols):
            rows.append({
                "symbol": sym.strip().upper(), "name": d["f58"],
                "last": float(d["f43"]), "pre_close": float(d["f60"]),
                "open": float(d["f46"]), "high": float(d["f44"]), "low": float(d["f45"]),
                "volume": float(d["f47"]),  # 手
                "amount": float("nan"), "open_interest": float("nan"),
                "ts": datetime.fromtimestamp(d["f86"]).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return pd.DataFrame(rows)[SNAPSHOT_COLUMNS]


# ---------------- 新浪港/美股快照 ----------------


def parse_sina_hk_quotes(raw: str) -> dict[str, dict]:
    """新浪港股 rt_hkXXXXX 报文 → {symbol: 行}。

    0英文名 1中文名 2开 3昨收 4高 5低 6最新 11成交额(港元) 12成交量(股)
    17日期 18时间。港股每手股数不一 → 统一存股。
    字段位与腾讯港股快照交叉验证（其 f36=量、f37=额，实测 2026-09-03）。
    """
    rows: dict[str, dict] = {}
    for m in re.finditer(r'hq_str_rt_(hk\w+)="([^"]*)"', raw):
        f = m.group(2).split(",")
        if len(f) < 19:
            continue
        sym = m.group(1)[2:]  # hk00700 → 00700
        rows[sym] = {
            "symbol": sym, "name": f[1],
            "open": float(f[2]), "pre_close": float(f[3]),
            "high": float(f[4]), "low": float(f[5]), "last": float(f[6]),
            "volume": float(f[12]), "amount": float(f[11]),
            "open_interest": float("nan"),
            "ts": f"{f[17].replace('/', '-')} {f[18]}",
        }
    return rows


def parse_sina_us_quotes(raw: str) -> dict[str, dict]:
    """新浪美股 gb_xxx 报文 → {symbol: 行}。

    0名称 1最新 3北京时间 5开 6高 7低 10成交量(股) 26昨收。
    （昨收=26 与涨跌幅字段自洽验证，实测 2026-09-03；27 为成交笔数）
    成交额无可靠字段 → NaN（宁缺毋错）。
    """
    rows: dict[str, dict] = {}
    for m in re.finditer(r'hq_str_gb_(\w+)="([^"]*)"', raw):
        sym = m.group(1).upper()  # aapl → AAPL
        f = m.group(2).split(",")
        if len(f) < 28:
            continue
        rows[sym] = {
            "symbol": sym, "name": f[0],
            "last": float(f[1]), "open": float(f[5]),
            "high": float(f[6]), "low": float(f[7]),
            "pre_close": float(f[26]), "volume": float(f[10]),
            "amount": float("nan"), "open_interest": float("nan"),
            "ts": f[3],
        }
    return rows


class SinaGlobalRealtimeSource:
    """新浪港/美股实时快照（rt_hk / gb_）。region: "hk" | "us"。"""

    name = "sina_global"

    def __init__(self, region: str = "hk", cfg: DataSourceConfig | None = None):
        self._region = region
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        if self._region == "hk":
            keys = [f"rt_hk{s.strip()}" for s in symbols]
            rows = parse_sina_hk_quotes(_http_get(
                "https://hq.sinajs.cn/list=" + ",".join(keys),
                referer="https://finance.sina.com.cn", encoding="gbk",
            ))
            canon = lambda s: s.strip()  # noqa: E731
        elif self._region == "us":
            keys = [f"gb_{s.strip().lower()}" for s in symbols]
            rows = parse_sina_us_quotes(_http_get(
                "https://hq.sinajs.cn/list=" + ",".join(keys),
                referer="https://finance.sina.com.cn", encoding="gbk",
            ))
            canon = lambda s: s.strip().upper()  # noqa: E731
        else:
            raise DataError(f"未知 region: {self._region}（应为 hk / us）")
        missing = [s for s in symbols if canon(s) not in rows]
        if missing:
            raise DataError(f"新浪港/美快照缺少品种 {missing}（接口或代码规则变更）")
        df = pd.DataFrame([rows[canon(s)] for s in symbols])
        return df[SNAPSHOT_COLUMNS]


class TencentHkRealtimeSource:
    """腾讯港股实时快照（qt.gtimg.cn，与 A股同一报文布局）。"""

    name = "tencent_hk"

    def __init__(self, cfg: DataSourceConfig | None = None):
        self._cfg = cfg or DataSourceConfig.load()

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        keys = [f"hk{s.strip()}" for s in symbols]
        raw = _http_get(
            "https://qt.gtimg.cn/q=" + ",".join(keys),
            referer="https://gu.qq.com/", encoding="gbk",
        )
        rows = parse_tencent_quotes(raw)
        missing = [s for s in symbols if s.strip() not in rows]
        if missing:
            raise DataError(f"腾讯港股快照缺少品种 {missing}（接口或代码规则变更）")
        df = pd.DataFrame([rows[s.strip()] for s in symbols])
        return df[SNAPSHOT_COLUMNS]


# ---------------- 多源组合 ----------------


class MultiSourceSnapshot:
    """多源快照组合：按序 failover；可选跨源对账。

    - failover：首个成功源的结果即返回；全败抛 DataError；
    - 对账（cross_check=True）：取下一个可用源，按 symbol 比对最新价，
      相对偏差超 tolerance_pct 抛 DataError；对账源失败仅告警不致命（容错优先）。
    - 批次内品种类型需与链匹配（A股链 / 期货链）。
    """

    def __init__(self, sources: Sequence, *, cross_check: bool = False,
                 tolerance_pct: float = 0.005, health: SourceHealth | None = None):
        if not sources:
            raise DataError("MultiSourceSnapshot 需要至少一个数据源")
        self._sources = list(sources)
        self._cross = cross_check
        self._tol = tolerance_pct
        self._health = health or SourceHealth()

    def fetch_snapshot(self, symbols: Sequence[str]) -> pd.DataFrame:
        primary: pd.DataFrame | None = None
        primary_errs: list[str] = []
        # 冷却中的源自动排到链尾兜底（SourceHealth），避免反复撞超时
        for src in self._health.order(self._sources):
            try:
                primary = src.fetch_snapshot(symbols)
                self._health.record_success(src.name)
                break
            except DataError as exc:
                self._health.record_failure(src.name)
                primary_errs.append(f"{src.name}: {exc}")
        if primary is None:
            raise DataError("全部快照源失败: " + " | ".join(primary_errs))

        if self._cross and len(self._sources) > 1:
            self._cross_check(symbols, primary)
        return primary

    def _cross_check(self, symbols: Sequence[str], primary: pd.DataFrame) -> None:
        for src in self._sources[1:]:
            if self._health.in_cooldown(src.name):
                continue
            try:
                second = src.fetch_snapshot(symbols)
            except DataError as exc:
                self._health.record_failure(src.name)
                warnings.warn(f"跨源对账源 {src.name} 失败（跳过对账）: {exc}")
                continue
            self._health.record_success(src.name)
            p = primary.set_index("symbol")["last"]
            s = second.set_index("symbol")["last"]
            for sym in p.index.intersection(s.index):
                if s[sym] <= 0:
                    continue
                dev = abs(p[sym] - s[sym]) / s[sym]
                if dev > self._tol:
                    raise DataError(
                        f"跨源对账超差 {sym}: {src.name}={p[sym]} vs "
                        f"second={s[sym]} (dev={dev:.4%} > {self._tol:.2%})，疑似脏数据"
                    )
            return  # 首个可用对账源通过即结束
        return


def _resolve_chain(chain: Sequence[str], registry: dict, what: str) -> list:
    """链名 → 源实例；未知源名立即报错（配置错误不静默）。"""
    unknown = [n for n in chain if n not in registry]
    if unknown:
        raise DataError(f"{what}链包含未注册数据源: {unknown}（config/data_sources.yaml）")
    return [registry[n] for n in chain]


def build_cn_stock_realtime(cfg: DataSourceConfig | None = None) -> MultiSourceSnapshot:
    """按 config/data_sources.yaml 的 realtime.cn_stock 组装 A股快照链。"""
    cfg = cfg or DataSourceConfig.load()
    rt = cfg.section("realtime").get("cn_stock", {})
    chain = rt.get("chain", ["sina", "tencent", "eastmoney"])
    registry = {
        "sina": SinaRealtimeSource(cfg),
        "tencent": TencentRealtimeSource(cfg),
        "eastmoney": EastmoneyRealtimeSource(cfg),
    }
    return MultiSourceSnapshot(
        _resolve_chain(chain, registry, "A股快照"),
        cross_check=bool(rt.get("cross_check", True)),
        tolerance_pct=float(rt.get("tolerance_pct", 0.005)),
    )


def build_futures_realtime(exchange: str = "SHFE",
                           cfg: DataSourceConfig | None = None) -> MultiSourceSnapshot:
    """按 config/data_sources.yaml 的 realtime.futures 组装期货快照链。"""
    cfg = cfg or DataSourceConfig.load()
    rt = cfg.section("realtime").get("futures", {})
    chain = rt.get("chain", ["sina", "eastmoney_futures"])
    registry = {
        "sina": SinaRealtimeSource(cfg),
        "eastmoney_futures": EastmoneyFuturesRealtimeSource(exchange, cfg),
    }
    return MultiSourceSnapshot(
        _resolve_chain(chain, registry, "期货快照"),
        cross_check=bool(rt.get("cross_check", False)),
        tolerance_pct=float(rt.get("tolerance_pct", 0.005)),
    )


def build_global_realtime(region: str, cfg: DataSourceConfig | None = None) -> MultiSourceSnapshot:
    """按 realtime.{hk_stock,us_stock} 组装港/美股快照链。region: "hk"|"us"。"""
    cfg = cfg or DataSourceConfig.load()
    section = "hk_stock" if region == "hk" else "us_stock"
    rt = cfg.section("realtime").get(section, {})
    default_chain = ["sina_hk", "tencent_hk"] if region == "hk" else ["sina_us"]
    chain = rt.get("chain", default_chain)
    registry = {
        "sina_hk": SinaGlobalRealtimeSource("hk", cfg),
        "sina_us": SinaGlobalRealtimeSource("us", cfg),
        "tencent_hk": TencentHkRealtimeSource(cfg),
    }
    return MultiSourceSnapshot(
        _resolve_chain(chain, registry, f"{section}快照"),
        cross_check=bool(rt.get("cross_check", region == "hk")),
        tolerance_pct=float(rt.get("tolerance_pct", 0.005)),
    )
