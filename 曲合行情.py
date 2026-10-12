# -*- coding: utf-8 -*-
"""
曲合网（金投网行情中心）· 期货加权合约行情数据层
==================================================

数据来源：https://m.quheqihuo.com/quote/hq-1048.html 所用的金投网行情接口
    · 实时行情  https://api.jijinhao.com/quoteCenter/realTime.htm?codes=JO_xxx
    · 分时      https://api.jijinhao.com/sQuoteCenter/todayMin.htm?code=JO_xxx
    · 分钟K     https://api.jijinhao.com/sQuoteCenter/history.htm?code=JO_xxx&style=N
                 style: 1=5分钟  6=15分钟  7=30分钟（实测自页面 JS 的 URLHASH 映射）
                 单次上限 500 根
    · 日K       https://api.jijinhao.com/sQuoteCenter/kDataList.htm?code=JO_xxx
                 返回约 969 根（回溯约 3.5 年，pageSize 不影响）

特点：无需账号、无需登录、免密钥。
局限：日K 仅约 3.5 年；分钟K 单次最多 500 根。

品种代码（JO_xxx）↔ 统一 key 的映射见 品种表.py。

作者：WorkBuddy  ·  为 chenzhuanxin 定制
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import 品种表 as PT

# 与 服务端.py 同口径：打包后缓存写在 exe 所在目录，源码运行写在脚本目录
BASE_DIR = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "缓存")
os.makedirs(CACHE_DIR, exist_ok=True)

DAY_K_CACHE = os.path.join(CACHE_DIR, "曲合日K缓存.json")

API = "https://api.jijinhao.com"
REFERER = "https://m.quheqihuo.com/quote/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MIN_STYLE = {"m5": 1, "m15": 6, "m30": 7}               # 曲合网页面实测映射
MIN_MAX = 500                                            # history.htm 单次上限
KIND_CN = {"timeshare": "分时", "m5": "5分", "m15": "15分", "m30": "30分", "d": "日K"}
KINDS = ["timeshare", "m5", "m15", "m30", "d"]
THR_KINDS = ["m5", "m15", "m30", "d"]                    # 可作阈值基准的周期

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url: str, timeout: int = 12, retries: int = 2, referer: str = REFERER) -> str:
    """极简 GET（显式绕过系统代理，带重试）。"""
    last = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", UA)
            req.add_header("Referer", referer)
            req.add_header("Accept", "*/*")
            with _OPENER.open(req, timeout=timeout) as resp:
                raw = resp.read()
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
        except Exception as exc:                                    # noqa: BLE001
            last = exc
            time.sleep(0.35)
    raise last if last else RuntimeError("http_get failed")


def _parse_js(text: str):
    """解析 `var XXX = {...};` / `var XXX = [...];` 形式。"""
    i = text.find("=")
    if i < 0:
        return None
    body = text[i + 1:].strip().rstrip(";").strip()
    if not body or body in ("null", "undefined"):
        return None
    try:
        return json.loads(body)
    except Exception:                                               # noqa: BLE001
        return None


def _num(v, default=0.0):
    try:
        f = float(v)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 实时行情
# --------------------------------------------------------------------------- #
def _norm_quote(raw: dict) -> dict:
    """金投网 qNN 字段 → 语义字段（字段说明实测得出）。"""
    last = _num(raw.get("q63"))
    prev = _num(raw.get("q64")) or _num(raw.get("q2"))
    change = _num(raw.get("q70"), last - prev)
    pct = _num(raw.get("q80"))
    if not pct and prev:
        pct = (last - prev) / prev * 100.0
    return {
        "showName": raw.get("showName", ""),
        "showCode": raw.get("showCode", ""),
        "digits": _int(raw.get("digits"), 2),
        "unit": raw.get("unit", ""),
        "last": last,
        "open": _num(raw.get("q1")),
        "high": _num(raw.get("q3")),
        "low": _num(raw.get("q4")),
        "presettle": _num(raw.get("q2")),
        "prevclose": prev,
        "change": change,
        "pct": pct,
        "volume": _int(raw.get("q62")),
        "amount": _num(raw.get("q61")),
        "oi": _int(raw.get("q60")),
        "oiChange": _int(raw.get("q192")),
        "status": _int(raw.get("status")),
        "time": _int(raw.get("time")),
        "trading": _int(raw.get("status")) == 100,
    }


def fetch_quotes(quhe_codes=None) -> dict:
    """批量拉取实时行情（按曲合代码），返回 {曲合代码: quote}。"""
    codes = list(quhe_codes or PT.QUHE_CODES)
    out = {}
    batch = 40
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = API + "/quoteCenter/realTime.htm?codes=" + ",".join(chunk)
        try:
            data = _parse_js(http_get(url, timeout=10))
        except Exception:                                           # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            if k in ("flag", "errorCode") or not isinstance(v, dict):
                continue
            out[k] = _norm_quote(v)
    return out


# --------------------------------------------------------------------------- #
# K 线 / 分时
# --------------------------------------------------------------------------- #
def _norm_bars(rows) -> list:
    bars = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        c = _num(r.get("close"))
        o = _num(r.get("open"))
        if c <= 0 and o <= 0:
            continue                                    # 跳过占位/空行
        bars.append({
            "t": _int(r.get("time") or r.get("date")),
            "day": r.get("day", "") or "",
            "o": o, "h": _num(r.get("high")), "l": _num(r.get("low")), "c": c,
            "v": _num(r.get("volume")),
            "oi": 0,
            "pct": _num(r.get("percent")),
            "chg": _num(r.get("change")),
        })
    bars.sort(key=lambda b: b["t"])                     # m30 偶发倒序，统一升序
    return bars


def fetch_dayk(quhe_code: str) -> list:
    """日K（约 969 根 / 3.5 年，pageSize 不影响返回量）。"""
    url = API + "/sQuoteCenter/kDataList.htm?code=%s&pageSize=1000" % urllib.parse.quote(quhe_code)
    data = _parse_js(http_get(url, timeout=20))
    rows = (data or {}).get("data") if isinstance(data, dict) else data
    if rows and isinstance(rows[0], list):
        rows = rows[0]
    return _norm_bars(rows)


def fetch_min(quhe_code: str, kind: str) -> list:
    """分钟K（单次上限 500 根）。"""
    style = MIN_STYLE.get(kind)
    if style is None:
        return []
    url = (API + "/sQuoteCenter/history.htm?code=%s&style=%d&pageSize=%d"
           % (urllib.parse.quote(quhe_code), style, MIN_MAX))
    data = _parse_js(http_get(url, timeout=20))
    rows = (data or {}).get("data") if isinstance(data, dict) else data
    if rows and isinstance(rows[0], list):
        rows = rows[0]
    return _norm_bars(rows)


def fetch_timeshare(quhe_code: str) -> dict:
    """当日分时：{'xAxis': [...], 'bars': [{time, price, avg, v}]}。"""
    url = API + "/sQuoteCenter/todayMin.htm?code=" + urllib.parse.quote(quhe_code)
    data = _parse_js(http_get(url, timeout=20))
    if not isinstance(data, dict):
        return {"xAxis": [], "bars": []}
    rows = data.get("data") or []
    bars = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        p = _num(r.get("price"), -1)
        if p < 0:
            continue                                    # 未到时间点，占位 -1
        bars.append({"time": r.get("time", ""), "t": _int(r.get("date")),
                     "price": p, "avg": _num(r.get("avg_price")), "v": _num(r.get("volume"))})
    return {"xAxis": data.get("xAxis") or [], "bars": bars}


# --------------------------------------------------------------------------- #
# 日K磁盘缓存
# --------------------------------------------------------------------------- #
_cache_lock = threading.Lock()
_dayk_mem: dict = {}
_dayk_dirty = False


def _load_dayk_cache():
    global _dayk_mem
    try:
        with open(DAY_K_CACHE, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        if isinstance(obj, dict):
            _dayk_mem = obj
    except Exception:                                               # noqa: BLE001
        _dayk_mem = {}


def save_dayk_cache():
    global _dayk_dirty
    with _cache_lock:
        if not _dayk_dirty:
            return
        try:
            tmp = DAY_K_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(_dayk_mem, fh, ensure_ascii=False)
            os.replace(tmp, DAY_K_CACHE)
            _dayk_dirty = False
        except Exception:                                           # noqa: BLE001
            pass


def _get_dayk_cached(quhe_code: str, use_cache: bool = True) -> list:
    """带缓存的日K（当日已取过则用缓存；跨日自动失效）。"""
    global _dayk_dirty
    today = time.strftime("%Y-%m-%d")
    with _cache_lock:
        hit = _dayk_mem.get(quhe_code)
        if use_cache and hit and hit.get("date") == today:
            return hit.get("bars") or []
    try:
        bars = fetch_dayk(quhe_code)
    except Exception:                                               # noqa: BLE001
        bars = []
    with _cache_lock:
        _dayk_mem[quhe_code] = {"date": today, "bars": bars}
        _dayk_dirty = True
    return bars


_load_dayk_cache()


# --------------------------------------------------------------------------- #
# 历史统计（复刻原页面口径：H=max(high) L=min(low) M=mean(close)）
# --------------------------------------------------------------------------- #
def stats_from_bars(bars: list) -> dict | None:
    if not bars:
        return None
    hs = [b["h"] for b in bars if b["h"] > 0]
    ls = [b["l"] for b in bars if b["l"] > 0]
    cs = [b["c"] for b in bars if b["c"] > 0]
    if not hs or not ls or not cs:
        return None
    return {
        "H": round(max(hs), 6),
        "L": round(min(ls), 6),
        "M": round(sum(cs) / len(cs), 6),
        "bars": len(bars),
        "from": bars[0].get("day") or "",
        "to": bars[-1].get("day") or "",
    }


# --------------------------------------------------------------------------- #
# 数据源对象（与 天勤行情.TianqinSource 同接口）
# --------------------------------------------------------------------------- #
class QuheSource:
    id = "quhe"
    label = "曲合网 · 金投网行情中心（加权合约）"

    def __init__(self):
        self.err = ""

    @property
    def ok(self) -> bool:
        return True

    def status(self) -> dict:
        return {"ok": True, "err": self.err, "user": "", "connectedAt": "", "subs": 0}

    def start(self) -> bool:
        return True

    def stop(self):
        pass

    # ---------------- 行情 ---------------- #
    def quotes(self, keys=None) -> dict:
        keys = [k for k in (keys or PT.keys_of("quhe")) if PT.KEY_TO_QUHE.get(k)]
        codes = [PT.KEY_TO_QUHE[k] for k in keys]
        raw = fetch_quotes(codes)
        out = {}
        for k in keys:
            q = raw.get(PT.KEY_TO_QUHE[k])
            if not q or q.get("last", 0) <= 0:
                continue
            out[k] = {
                "code": k,
                "name": PT.NAME.get(k, ""),
                "showName": q.get("showName") or (PT.NAME.get(k, "") + "加权"),
                "digits": q.get("digits", 2),
                "unit": q.get("unit", ""),
                "last": q["last"],
                "open": q.get("open", 0),
                "high": q.get("high", 0),
                "low": q.get("low", 0),
                "presettle": q.get("presettle", 0),
                "prevclose": q.get("prevclose", 0),
                "change": q.get("change", 0),
                "pct": q.get("pct", 0),
                "volume": q.get("volume", 0),
                "oi": q.get("oi", 0),
                "oiChange": q.get("oiChange", 0),
                "trading": q.get("trading", False),
                "ts": q.get("time", 0),
            }
        return out

    # ---------------- K 线 ---------------- #
    def kline(self, key: str, kind: str, limit: int | None = None) -> dict:
        if kind == "timeshare":
            return self.timeshare(key)
        code = PT.KEY_TO_QUHE.get(key)
        if not code:
            return {"kind": kind, "code": key, "bars": [], "count": 0}
        if kind == "d":
            bars = _get_dayk_cached(code)
            limit_hit = False
        else:
            if kind not in MIN_STYLE or MIN_STYLE.get(kind) is None:
                return {"kind": kind, "code": key, "bars": [], "count": 0}
            bars = fetch_min(code, kind)
            limit_hit = len(bars) >= MIN_MAX
        if limit:
            bars = bars[-int(limit):]
        return {"kind": kind, "code": key, "name": PT.NAME.get(key, ""),
                "bars": bars, "count": len(bars),
                "limitHit": limit_hit, "maxBars": MIN_MAX if kind != "d" else 0}

    def dayk(self, key: str) -> list:
        code = PT.KEY_TO_QUHE.get(key)
        if not code:
            return []
        return _get_dayk_cached(code)

    def bars_many(self, keys, kind: str = "d", limit: int | None = None) -> dict:
        """批量取同一周期的 K 线 → {key: bars}（并发；日K 走磁盘缓存）。"""
        out = {}
        ks = [k for k in keys if PT.KEY_TO_QUHE.get(k)]

        def one(k):
            try:
                return k, self.kline(k, kind, limit).get("bars") or []
            except Exception:                                       # noqa: BLE001
                return k, []
        with ThreadPoolExecutor(max_workers=10) as ex:
            for k, b in ex.map(one, ks):
                out[k] = b
        return out

    def timeshare(self, key: str) -> dict:
        code = PT.KEY_TO_QUHE.get(key)
        if not code:
            return {"kind": "timeshare", "code": key, "bars": [], "count": 0}
        ts = fetch_timeshare(code)
        return {"kind": "timeshare", "code": key, "name": PT.NAME.get(key, ""),
                "xAxis": ts["xAxis"], "bars": ts["bars"], "count": len(ts["bars"])}


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    src = QuheSource()
    t0 = time.time()
    qs = src.quotes()
    print("实时行情: %d 个品种，%.2fs" % (len(qs), time.time() - t0))
    for k in list(qs)[:5]:
        q = qs[k]
        print("  %-10s %-8s 最新 %-10s 涨跌 %+.2f%% 持仓 %s" % (k, q["name"], q["last"], q["pct"], q["oi"]))
    for kind in ("d", "m30", "m15", "m5"):
        t0 = time.time()
        r = src.kline("CZCE.SA", kind)
        b = r["bars"]
        print("  %-4s 条数 %-5d 用时 %.2fs 首 %s 末 %s"
              % (kind, len(b), time.time() - t0, (b[0]["day"] if b else "-"), (b[-1]["day"] if b else "-")))
    ts = src.timeshare("CZCE.SA")
    print("  分时点数:", ts["count"])
    save_dayk_cache()
