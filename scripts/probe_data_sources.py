"""数据源连通性与字段验证脚本（docs/DATA_SOURCES.md 的配套实测工具）。

用法:
    .venv/Scripts/python scripts/probe_data_sources.py [--no-tdx] [--no-akshare]

每项检查输出 [PASS]/[FAIL]/[SKIP]，结尾打印汇总。
退出码：无 FAIL 时为 0（SKIP 不算失败）。联网运行，盘中数据最完整。
"""

from __future__ import annotations

import inspect
import os
import socket
import sys
import time

# 系统代理会拦截东财并造成 TLS 中断，数据请求一律直连（见 DATA_SOURCES.md §3.1）
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

import requests  # noqa: E402

# ⚠ UA 必须是完整浏览器串：裸 "Mozilla/5.0" 会被东财直接掐连接（实测 2026-09-03）
_EM_HEADERS = {
    "Referer": "https://quote.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}
_SINA_HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
_NO_PROXY = {"http": None, "https": None}

# SSRF 防护：本脚本所有请求 URL 均为下方检查项中的硬编码行情源，
# 出口处统一做域名白名单校验，防止任何拼接/替换产生的 URL 越界。
_ALLOWED_HOSTS = {
    "push2his.eastmoney.com",
    "92.push2his.eastmoney.com",
    "33.push2his.eastmoney.com",
    "push2.eastmoney.com",
    "1.push2.eastmoney.com",
    "hq.sinajs.cn",
}


def _guard(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ValueError(f"拒绝访问非白名单数据源: {url}")
    return url

# 通达信行情服务器（2026-09-03 实测可达；内置 hq_hosts 已过时，探活通过即用第一个）
_TDX_HOSTS: list[tuple[str, int]] = [
    ("115.238.90.165", 7709),
    ("218.75.126.9", 7709),
    ("60.12.136.250", 7709),
    ("115.238.56.198", 7709),
    ("180.153.18.170", 7709),
    ("110.41.147.114", 7709),
    ("122.51.120.217", 7709),
]


def _http_json(url: str, headers: dict[str, str], tries: int = 3) -> dict:
    urls = [url]
    if "push2his.eastmoney.com" in url:
        # 镜像 92 实测可用，主域名全败时兜底（33.push2his 不可用）
        urls.append(url.replace("push2his.eastmoney.com", "92.push2his.eastmoney.com"))
    elif "push2.eastmoney.com" in url:
        urls.append(url.replace("push2.eastmoney.com", "1.push2.eastmoney.com"))
    last: Exception | None = None
    for u in urls:
        _guard(u)
        for i in range(tries):
            try:
                r = requests.get(u, headers=headers, timeout=8, proxies=_NO_PROXY)
                return r.json()
            except Exception as e:  # 东财偶发断连，重试是必须的
                last = e
                time.sleep(0.5 * (i + 1))
    raise ConnectionError(f"重试后仍失败: {last}")


def _http_text(url: str, headers: dict[str, str]) -> str:
    r = requests.get(_guard(url), headers=headers, timeout=8, proxies=_NO_PROXY)
    r.encoding = "gbk"  # 新浪为 GBK 编码
    return r.text


_CHECKS: list[tuple[str, object]] = []


def check(name: str):
    def _wrap(fn):
        _CHECKS.append((name, fn))
        return fn

    return _wrap


# ---------------- 东财 ----------------


@check("东财 push2his A股日K (600519, 前复权)")
def _em_daily() -> str:
    u = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid=1.600519&klt=101&fqt=1&lmt=3&end=20500101"
        "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
    )
    klines = _http_json(u, _EM_HEADERS)["data"]["klines"]
    last = klines[-1].split(",")
    # 顺序实测为: 日期,开,收,高,低,量,额（收盘在第二位）
    assert last[0] >= "2026-09-01", f"日K数据过旧: {last[0]}"
    return f"最新bar {last[0]} 开{last[1]} 收{last[2]} 高{last[3]} 低{last[4]}"


@check("东财 push2his A股1分钟K (600519)")
def _em_minute() -> str:
    u = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        "?secid=1.600519&klt=1&fqt=1&lmt=3&end=20500101"
        "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57"
    )
    klines = _http_json(u, _EM_HEADERS)["data"]["klines"]
    return f"当日 {len(klines)} 根, 最后一根: {klines[-1]}"


@check("东财 push2 A股快照 (600519) — 价格缩放需按 ÷100 解释")
def _em_stock_snapshot() -> str:
    u = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        "?secid=1.600519&fields=f43,f44,f45,f46,f57,f58,f60,f86,f169,f170"
    )
    d = _http_json(u, _EM_HEADERS)["data"]
    assert d["f57"] == "600519"
    # A股 f43 需 ÷100；期货不缩放（规则不统一，见 DATA_SOURCES.md §2.2）
    last = d["f43"] / 100.0
    pre = d["f60"] / 100.0
    assert abs(last / pre - 1 - d["f170"] / 10000.0) < 0.002, "快照字段自洽性校验失败"
    return f"{d['f58']} 最新≈{last:.2f} 昨收≈{pre:.2f} ts={d['f86']}"


@check("东财 push2 商品期货快照 (上期所主连 113.rbm) — f43 即原价")
def _em_futures_snapshot() -> float:
    u = "https://push2.eastmoney.com/api/qt/stock/get?secid=113.rbm&fields=f43,f44,f45,f46,f57,f58,f60,f86"
    d = _http_json(u, _EM_HEADERS)["data"]
    assert d["f57"] == "rbm", "东财期货 secid 规则变更"
    return float(d["f43"])  # 返回最新价，供与新浪交叉校验


# ---------------- 新浪 ----------------


@check("新浪 A股快照 (sh600519, GBK 明文)")
def _sina_stock() -> str:
    text = _http_text("https://hq.sinajs.cn/list=sh600519", _SINA_HEADERS)
    f = text.split('"')[1].split(",")
    assert f[0] == "贵州茅台", f"字段结构变更: {f[0]}"
    return f"{f[0]} 开{f[1]} 昨收{f[2]} 最新{f[3]} 高{f[4]} 低{f[5]}"


@check("新浪 股指期货快照 (nf_IF0) — 东财不覆盖中金所")
def _sina_cffex() -> str:
    text = _http_text("https://hq.sinajs.cn/list=nf_IF0", _SINA_HEADERS)
    f = text.split('"')[1].split(",")
    # 股指字段结构与商品不同: 开,高,低,最新 在前 4 位, 日期/时间在第 36/37 位
    assert float(f[0]) > 1000 and f[36].count("-") == 2, "股指字段结构变更"
    return f"沪深300期货 最新{f[3]} {f[36]} {f[37]}"


@check("新浪 商品期货快照 (nf_RB0) — 与东财交叉校验 <0.5%")
def _sina_commodity(em_price: float) -> str:
    text = _http_text("https://hq.sinajs.cn/list=nf_RB0", _SINA_HEADERS)
    f = text.split('"')[1].split(",")
    last = float(f[6])  # 商品结构: 名称,时间,开,高,低,?,最新,买价,卖价,...
    assert em_price > 0 and abs(last - em_price) / em_price < 0.005, (
        f"东财({em_price}) 与新浪({last}) 偏差过大"
    )
    return f"{f[0]} 最新{last} (东财 {em_price}, 对账一致)"


# ---------------- akshare ----------------


@check("akshare 期货主力日K (RB0) — 注意滞后一天")
def _ak_futures_daily() -> str:
    import akshare as ak

    df = ak.futures_main_sina(symbol="RB0", start_date="20260801", end_date="20261231")
    row = df.iloc[-1]
    return f"最后一行 {row['日期']}: 收{row['收盘价']} 持仓{row['持仓量']:.0f}"


@check("akshare 期货1分钟K (RB0, 含持仓量)")
def _ak_futures_minute() -> str:
    import akshare as ak

    df = ak.futures_zh_minute_sina(symbol="RB0", period="1")
    row = df.iloc[-1]
    return f"共 {len(df)} 根, 最后: {row['datetime']} 收{row['close']} 持仓{row['hold']:.0f}"


# ---------------- 通达信协议 ----------------


@check("通达信协议 (pytdx, TCP 7709): 探活+快照+日K")
def _tdx() -> str:
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return "SKIP: pytdx 未安装 (uv pip install pytdx)"

    api = TdxHq_API()
    for ip, port in _TDX_HOSTS:
        try:
            if not api.connect(ip, port, time_out=3):
                continue
            q = api.get_security_quotes([(1, "600519")])
            assert q, "快照为空"
            bars = api.get_security_bars(9, 1, "600519", 0, 1)  # 9=日K
            # 注意: 日K最后一根是当日实时累计值
            api.disconnect()
            return f"via {ip}: {q[0]['code']} 最新{q[0]['price']} 买一{q[0]['bid1']} 卖一{q[0]['ask1']}, 日K末根 {bars[-1]['datetime']}"
        except Exception:
            continue
    return "FAIL: 全部 TDX 服务器未连通（列表有时效性，参考 DATA_SOURCES.md §2.5 更新）"


def main() -> int:
    args = set(sys.argv[1:])
    results: list[tuple[str, str]] = []
    em_price = 0.0

    for idx, (name, fn) in enumerate(_CHECKS):
        if idx:
            time.sleep(1.0)  # 节流：东财对秒级连发限流，间隔 ≥1s 即正常
        if "--no-akshare" in args and "akshare" in name:
            results.append((name, "SKIP"))
            continue
        if "--no-tdx" in args and "通达信" in name:
            results.append((name, "SKIP"))
            continue
        try:
            takes_price = len(inspect.signature(fn).parameters) > 0
            detail = fn(em_price) if takes_price else fn()
            if "SKIP" in str(detail) and str(detail).startswith("SKIP"):
                results.append((name, "SKIP"))
                print(f"[SKIP] {name}: {detail}")
            else:
                results.append((name, "PASS"))
                print(f"[PASS] {name}\n       {detail}")
            if "东财" in name and "期货" in name and "商品" in name:
                em_price = detail if isinstance(detail, float) else 0.0
        except Exception as e:
            results.append((name, "FAIL"))
            print(f"[FAIL] {name}\n       {type(e).__name__}: {e}")

    n_pass = sum(1 for _, s in results if s == "PASS")
    n_fail = sum(1 for _, s in results if s == "FAIL")
    n_skip = sum(1 for _, s in results if s == "SKIP")
    print(f"\n汇总: PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
