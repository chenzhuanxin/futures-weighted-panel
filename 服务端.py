# -*- coding: utf-8 -*-
"""
加权（仓权）合约监控面板 —— 服务端（纯标准库，无需第三方依赖）
=====================================================================

数据源可切换：曲合网（金投网，免账号）/ 天勤（TqSdk，需账号）

启动：
    python 服务端.py                    # 默认端口 8689，自动打开浏览器
    python 服务端.py --port 9000
    python 服务端.py --no-browser

接口一览：
    GET  /                     看板页面
    GET  /api/data             全量数据（实时行情 + 历史 H/L/M + 偏离度 percent + 阈值 + 预警）
    GET  /api/tick             精简实时行情（前端高频轮询，约 2 秒一次）
    GET  /api/kline            查看行情  ?key=&kind=timeshare|m5|m15|m30|d&limit=
    GET  /api/status           服务状态
    GET  /api/config           读配置
    GET  /api/source           数据源列表与状态
    POST /api/config           保存配置
    POST /api/source           切换数据源  {"source":"quhe"|"tianqin"}
    POST /api/threshold/set    设定 / 清除某品种阈值
    POST /api/threshold/auto   一键设阈（= 所选周期 + 所选均线的动态值）
    POST /api/refresh          立即刷新
    POST /api/mail/test        发送测试邮件
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _is_frozen() -> bool:
    """是否由 PyInstaller 打包后运行。"""
    return bool(getattr(sys, "frozen", False))


def _res_dir() -> str:
    """只读资源目录：打包后 = PyInstaller 解包目录（sys._MEIPASS），源码运行 = 脚本目录。"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _data_dir() -> str:
    """可写数据目录：打包后 = exe 所在目录；源码运行 = 脚本目录。

    配置与缓存都放这里，所以 **exe 拷到哪、配置就跟到哪**；
    也避免写进 onefile 的临时解包目录（每次运行都是新目录、退出即删）。
    """
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _res_dir()                 # 只读资源（看板.html）
DATA_DIR = _data_dir()                # 配置 / 缓存（可写、持久）
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, DATA_DIR)          # 允许在 exe 旁放同名模块覆盖内置模块

import importlib

PT = importlib.import_module("品种表")
DATA = importlib.import_module("数据源")
MAIL = importlib.import_module("邮件预警")

CACHE_DIR = os.path.join(DATA_DIR, "缓存")
os.makedirs(CACHE_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CACHE_DIR, "配置.json")
THRESH_FILE = os.path.join(CACHE_DIR, "阈值设置.json")
HTML_FILE = os.path.join(BASE_DIR, "看板.html")
if not os.path.exists(HTML_FILE):                 # 兜底：允许把看板.html 放在 exe 旁边
    _alt = os.path.join(DATA_DIR, "看板.html")
    if os.path.exists(_alt):
        HTML_FILE = _alt

THR_KINDS = ["m5", "m15", "m30", "d"]
MA_CHOICES = [5, 8, 10, 20, 30, 50]

DEFAULT_CONFIG = {
    "panel": {
        "source": "quhe",          # 数据源：quhe | tianqin
        "theme": "light",          # 显示模式：light（白底，默认）| dark（深蓝）
        "tickSec": 2,              # 实时 tick 轮询间隔（秒）
        "refreshSec": 30,          # 完整刷新间隔（秒）
        "thrKind": "d",            # 一键设阈所用周期：m5 | m15 | m30 | d
        "thrMA": 5,                # 一键设阈所用均线周期：5|8|10|20|30|50
        "chartMA": [5, 8, 10, 20, 30, 50],   # K线图默认展示的均线
        "mailOn": True,
        "soundOn": True,
        "minPercent": 70,          # 一键设阈门槛（偏离度 %，> 门槛才可一键设阈，对齐参照网页）
        # ---- 邮件防轰炸（守护） ---- #
        "mailTradingOnly": True,   # 仅交易时段发信（闭市静默）
        "mailEdgeOnly": True,      # 仅在「新突破」那一刻发一次，持续超阈不重发
        "mailOnStart": False,      # 启动首轮是否补发「存量触发」（默认不补发）
        "mailCooldown": 600,       # 同品种同方向冷却（秒）
        "mailReleasePct": 0.3,     # 滞回复位幅度（%）：回落超过此幅度才认为脱离触发态
        "mailStaleSec": 300,       # 行情超过多少秒未更新 → 视为闭市（秒）
        "mailMaxPerHour": 6,       # 全局限额：每小时最多发几封
        "mailMaxPerDay": 30,       # 全局限额：每天最多发几封
        "mailQueueTTL": 1800,      # 被限流的触发最多排队多久（秒），过期丢弃
    },
    "mail": {
        "host": "smtp.126.com",
        "port": 465,
        "ssl": True,
        "user": "",                # 发件邮箱，如 yourname@126.com（建议在面板里填）
        "authcode": "",            # SMTP 授权码（邮箱设置里生成，不是登录密码）
        "from_name": "加权合约监控",
        "to": [],
        "cc": [],
    },
    "tianqin": {
        "user": "",                # 快期账号（在面板「设置 → 数据源」里填写）
        "password": "",
    },
}


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def hlm_from_bars(bars: list) -> dict | None:
    """历史统计（与原页面同口径）：H=max(high) / L=min(low) / M=mean(close)。"""
    if not bars:
        return None
    hs = [b["h"] for b in bars if b.get("h", 0) > 0]
    ls = [b["l"] for b in bars if b.get("l", 0) > 0]
    cs = [b["c"] for b in bars if b.get("c", 0) > 0]
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


def dynamic_ma(bars: list, ma: int, live: float | None) -> float | None:
    """动态均线 = （最近 ma-1 根已收盘 + 当前实时价）/ ma。

    最后一根 K 线视为「当前未收盘 bar」，故用实时价替换。
    """
    cs = [b["c"] for b in bars if b.get("c", 0) > 0]
    if not cs:
        return None
    live = live if (live and live > 0) else cs[-1]
    if ma <= 1:
        return round(live, 6)
    closed = cs[:-1] if len(cs) >= 2 else cs
    tail = closed[-(ma - 1):] if closed else []
    vals = list(tail) + [live]
    return round(sum(vals) / len(vals), 6)


# --------------------------------------------------------------------------- #
# 交易时段判定（用于「非交易时段不发邮件」）
# --------------------------------------------------------------------------- #
# 各交易所的日内交易时段（北京时间，单位=分钟，自当日 00:00 起算；
# 结束值 > 1440 表示跨到次日，例如 1260~1500 即 21:00~次日 01:00）。
#
# ⚠️ 这张表只是**兜底**：真正判断「此刻该品种是否在交易」以行情数据自身的
#    新鲜度为准（见 market_state），因为夜盘结束时间**因品种而异**
#    （23:00 / 01:00 / 02:30 三种），静态表无法精确覆盖；而行情时间戳
#    天然就是最新交易时刻，闭市即冻结，零维护、自适应所有节假日。
EXCH_SESSIONS = {
    "上期所":   [(540, 615), (630, 690), (810, 900), (1260, 1500)],
    "上期能源": [(540, 615), (630, 690), (810, 900), (1260, 1500)],
    "大商所":   [(540, 615), (630, 690), (810, 900), (1260, 1380)],
    "郑商所":   [(540, 615), (630, 690), (810, 900), (1260, 1380)],
    "广期所":   [(540, 615), (630, 690), (810, 900)],
    "中金所":   [(570, 690), (780, 900)],
}


def in_session(exch: str, dt) -> bool:
    """按交易所粗表判断是否在交易时段内（含夜盘跨日；周末不交易）。"""
    spans = EXCH_SESSIONS.get(exch)
    if not spans:
        return True                             # 未知交易所 → 保守放行
    m = dt.hour * 60 + dt.minute
    wd = dt.weekday()                           # 0=周一 … 6=周日
    prev_wd = (wd - 1) % 7
    for a, b in spans:
        if b <= 1440:                           # 当日闭合时段
            if wd < 5 and a <= m < b:
                return True
        else:                                   # 夜盘：当日 a 点开盘，跨到次日 (b-1440)
            if wd < 5 and m >= a:               # 当日晚间那一段
                return True
            if prev_wd < 5 and m < (b - 1440):  # 前一日夜盘延续到今日凌晨
                return True
    return False


def quote_ts(q: dict):
    """从行情快照中取出「最新数据时刻」（秒级 Unix 时间戳）；取不到返回 None。

    · 曲合：`ts` 为**毫秒**时间戳（如 1791820280000）
    · 天勤：`ts` 为 'YYYY-MM-DD HH:MM:SS.ffffff' 字符串
    """
    t = q.get("ts")
    if isinstance(t, (int, float)) and t > 0:
        return t / 1000.0 if t > 1e11 else float(t)
    if isinstance(t, str) and len(t) >= 16:
        try:
            return time.mktime(time.strptime(t[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:                                       # noqa: BLE001
            return None
    return None


def market_state(q: dict, exch: str, stale_sec: int, now: float = None):
    """判断该品种此刻是否处于「可发信的交易状态」。

    返回 (可发信: bool, 原因文本: str)。判据优先级：
      1) 行情自带交易标志（曲合 status==100）→ 交易中
      2) 行情数据新鲜度：距最新数据 ≤ stale_sec 秒 → 交易中（最鲁棒，自适应夜盘差异/节假日）
      3) 以上都取不到 → 退回交易所粗时段表兜底
    """
    now = now or time.time()
    if q.get("trading") is True:
        return True, "交易所状态：交易中"
    ts = quote_ts(q)
    if ts:
        age = now - ts
        if age < -120:                          # 时间戳异常（明显未来）→ 不信任
            pass
        elif age <= stale_sec:
            return True, "行情 %.0f 秒前更新" % max(0.0, age)
        else:
            return False, "行情已 %.1f 分钟未更新，视为闭市" % (age / 60.0)
    ok = in_session(exch, time.localtime(now))
    return ok, ("交易所时段表：交易时段" if ok else "交易所时段表：非交易时段")


# --------------------------------------------------------------------------- #
# 应用主体
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, warm: bool = True):
        self.lock = threading.RLock()
        self.config = self._load_config()
        self.thresholds = self._load_thresholds()
        self.sources = DATA.SourceManager(self.config)
        self.stats: dict = {}
        self.data: dict = {"ready": False, "items": [], "above": [], "below": []}
        self.alerts: list = []
        self.mail_log: list = []
        self.last_mail: dict = {}
        self.error = ""
        self.warming = True
        self.started = now_str()
        self._bars_cache: dict = {}
        self._stats_by_source: dict = {}
        # ---- 邮件防轰炸状态 ---- #
        self.trig: dict = {}          # {key: {"state":"up"|"down","since":ts,"price":p}} 触发态
        self.mail_queue: list = []    # 被限流 / 冷却中的待发预警
        self.mail_sent: list = []     # 实际发信时间戳（用于每小时/每天配额）
        self.mail_note: list = []     # 静默 / 抑制原因（面板可见）
        self.suppressed = {"closed": 0, "quota": 0, "cooldown": 0, "queueDrop": 0}
        self.first_scan = True        # 首轮只建立基线，不回补存量触发
        self._tick_lock = threading.Lock()
        self._tick_cache: tuple = (0.0, {})
        self._kl_lock = threading.Lock()
        self._kl_cache: dict = {}
        if warm:
            self._build_stats()
            self.refresh()
            self.warming = False
        else:
            threading.Thread(target=self._warmup, daemon=True,
                             name="warmup").start()

    def _warmup(self):
        try:
            self._build_stats()
        except Exception:                                           # noqa: BLE001
            traceback.print_exc()
        finally:
            self.warming = False
            self.refresh()

    # ---------------- 当前数据源 ---------------- #
    def src(self):
        return self.sources.current()

    @property
    def source_name(self) -> str:
        return self.sources.cur

    # ---------------- 配置 / 阈值 ---------------- #
    def _load_config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                cfg = deep_merge(cfg, json.load(fh))
        except Exception:                                           # noqa: BLE001
            pass
        return cfg

    def save_config(self):
        try:
            with open(CONFIG_FILE + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.config, fh, ensure_ascii=False, indent=1)
            os.replace(CONFIG_FILE + ".tmp", CONFIG_FILE)
        except Exception:                                           # noqa: BLE001
            pass

    def panel(self, k, d=None):
        return self.config.get("panel", {}).get(k, d)

    def _load_thresholds(self) -> dict:
        try:
            with open(THRESH_FILE, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            return obj if isinstance(obj, dict) else {}
        except Exception:                                           # noqa: BLE001
            return {}

    def save_thresholds(self):
        try:
            with open(THRESH_FILE + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.thresholds, fh, ensure_ascii=False, indent=1)
            os.replace(THRESH_FILE + ".tmp", THRESH_FILE)
        except Exception:                                           # noqa: BLE001
            pass

    def thr(self, key: str) -> dict:
        return self.thresholds.setdefault(key, {"up": None, "down": None,
                                                "locked": False, "auto": None,
                                                "kind": None, "ma": None})

    # ---------------- 历史统计 ---------------- #
    def _build_stats(self):
        """构建日K历史统计（H/L/M）。结果按数据源缓存；日K尾部另存入 bars 缓存供阈值均线复用。"""
        name = self.source_name
        cached = self._stats_by_source.get(name)
        if cached:
            self.stats = cached
            return
        src = self.src()
        keys = PT.keys_of(name)
        t0 = time.time()
        try:
            bm = src.bars_many(keys, "d")
        except Exception:                                           # noqa: BLE001
            traceback.print_exc()
            bm = {}
        if not bm:
            def one(k):
                try:
                    return k, src.dayk(k)
                except Exception:                                   # noqa: BLE001
                    return k, []
            with ThreadPoolExecutor(max_workers=10) as ex:
                bm = dict(ex.map(one, keys))
        self.stats = {k: hlm_from_bars(bm.get(k) or []) for k in keys}
        now = time.time()
        with self._kl_lock:
            for k, b in bm.items():
                if b:
                    self._bars_cache[(name, k, "d")] = (now, b[-120:])
        self._stats_by_source[name] = dict(self.stats)
        print(" [统计] %s 日K %d/%d 个品种，用时 %.1fs"
              % (name, len([1 for v in bm.values() if v]), len(keys), time.time() - t0))

    def rebuild_stats_async(self):
        self.warming = True

        def work():
            try:
                self._build_stats()
            except Exception:                                       # noqa: BLE001
                traceback.print_exc()
            finally:
                self.warming = False
                self.refresh()
        threading.Thread(target=work, daemon=True).start()

    def mark_switching(self, name: str):
        """切换数据源时立即清空面板，避免展示上一个源的数据。"""
        with self.lock:
            self.stats = {}
            self.alerts = []
            self.data = {
                "ready": False, "warming": True, "source": name,
                "sourceLabel": next((s["label"] for s in DATA.SOURCES if s["id"] == name), name),
                "items": [], "above": [], "below": [],
                "stats": {"above": 0, "below": 0, "up70": 0, "down70": 0},
                "updated": "--", "elapsed": 0, "total": 0, "skipped": [],
                "thresholds": dict(self.thresholds), "minPercent": float(self.panel("minPercent", 70)),
                "tickSec": int(self.panel("tickSec", 2)), "refreshSec": int(self.panel("refreshSec", 30)),
                "soundOn": bool(self.panel("soundOn", True)),
                "thrKind": self.panel("thrKind", "d"), "thrMA": int(self.panel("thrMA", 5)),
                "thrKinds": THR_KINDS, "maChoices": MA_CHOICES,
                "chartMA": list(self.panel("chartMA", [5, 8, 10, 20, 30, 50])),
                "kinds": ["timeshare", "m5", "m15", "m30", "d"],
                "alerts": [], "mailLog": self.mail_log[-8:], "error": "",
            }

    # ---------------- K 线缓存（服务端级） ---------------- #
    def kline(self, key: str, kind: str, limit: int | None = None) -> dict:
        ttl = {"timeshare": 6, "m5": 25, "m15": 25, "m30": 25, "d": 300}.get(kind, 30)
        ck = (self.source_name, key, kind)
        now = time.time()
        with self._kl_lock:
            hit = self._kl_cache.get(ck)
            if hit and now - hit[0] < ttl:
                data = hit[1]
                if limit:
                    d2 = dict(data)
                    d2["bars"] = (data.get("bars") or [])[-int(limit):]
                    d2["count"] = len(d2["bars"])
                    return d2
                return data
        data = self.src().kline(key, kind, limit)
        if not limit:
            with self._kl_lock:
                self._kl_cache[ck] = (now, data)
        return data

    # ---------------- 实时行情 ---------------- #
    def tick(self, force: bool = False) -> dict:
        with self._tick_lock:
            ts, data = self._tick_cache
            if not force and data and time.time() - ts < 1.5:
                return data
        try:
            data = self.src().quotes()
        except Exception as exc:                                    # noqa: BLE001
            self.error = "行情获取失败：%s" % exc
            return {}
        with self._tick_lock:
            self._tick_cache = (time.time(), data)
        return data

    # ---------------- 刷新（全量） ---------------- #
    def refresh(self) -> dict:
        t0 = time.time()
        quotes = self.tick(force=True)
        if quotes:
            self.error = ""
        elif not self.error:
            self.error = "该数据源暂无行情返回"

        kind = self.panel("thrKind", "d")
        ma = int(self.panel("thrMA", 5))
        items, skipped = [], []

        def ma_for(k, live):
            """取该品种所选周期的 K 线（带缓存）并算动态均值。"""
            try:
                ck = (self.source_name, k, kind)
                now = time.time()
                # 日K尾部只用于均线：最后一根会被实时价替换，故可长缓存
                ttl = 1800 if kind == "d" else 60
                with self._kl_lock:
                    hit = self._bars_cache.get(ck)
                    bars = hit[1] if (hit and now - hit[0] < ttl) else None
                if bars is None:
                    bars = self.src().kline(k, kind).get("bars") or []
                    with self._kl_lock:
                        self._bars_cache[ck] = (now, bars)
                return dynamic_ma(bars, ma, live)
            except Exception:                                       # noqa: BLE001
                return None

        # ---- 批量预取所选周期的 K 线（避免逐品种串行请求；天勤侧尤其明显）----
        # 均线计算只需最近 ma 根，故只取 ma+10 根即可，不必拉全量。
        ttl = 1800 if kind == "d" else 60
        try:
            t_pre = time.time()
            need = []
            for k in PT.keys_of(self.source_name):
                with self._kl_lock:
                    hit = self._bars_cache.get((self.source_name, k, kind))
                if not (hit and t_pre - hit[0] < ttl):
                    need.append(k)
            if need:
                got = self.src().bars_many(need, kind, limit=max(60, ma + 10)) or {}
                ts_now = time.time()
                with self._kl_lock:
                    for k, bars in got.items():
                        if bars:
                            self._bars_cache[(self.source_name, k, kind)] = (ts_now, bars)
        except Exception:                                           # noqa: BLE001
            traceback.print_exc()

        for key in PT.keys_of(self.source_name):
            v = PT.BY_KEY[key]
            q = quotes.get(key) or {}
            if not q or q.get("last", 0) <= 0:
                skipped.append(v["name"])
                continue
            st = self.stats.get(key)
            last = q["last"]
            it = {
                "key": key, "name": v["name"], "exch": v["exch"],
                "showName": q.get("showName") or (v["name"] + "加权"),
                "digits": q.get("digits", 2), "unit": q.get("unit", ""),
                "price": last, "open": q.get("open", 0),
                "high": q.get("high", 0), "low": q.get("low", 0),
                "presettle": q.get("presettle", 0), "prevclose": q.get("prevclose", 0),
                "change": round(q.get("change", 0), 4), "pct": round(q.get("pct", 0), 4),
                "volume": q.get("volume", 0), "oi": q.get("oi", 0),
                "oiChange": q.get("oiChange", 0), "trading": q.get("trading", False),
                "ts": q.get("ts", 0),
            }
            if st:
                # 百分比口径**对齐参照网页**（www.html2web.com/view/6a4eee0090bf2981702394a3）：
                # 以「历史均价 M」为 0 基准，高低两侧各自归一化到 0~100：
                #   · 高侧（现价 ≥ M）：pct = (现价 − M) ÷ (H − M) × 100  → 触及历史最高 = 100
                #   · 低侧（现价 < M）：pct = (现价 − M) ÷ (L − M) × 100  → 触及历史最低 = 100
                # 两侧数值都表示「离开均值走完该侧全程的比例」，故越大越极端；
                # 若现价越过 H / L（突破历史极值）会 > 100，照原网页做法不截断，仅条长截断。
                M = st["M"]
                if last >= M:
                    den = st["H"] - M
                    pct_rel = (last - M) / den * 100.0 if den > 0 else 0.0
                    side_hi = True
                else:
                    den = st["L"] - M                      # 恒为负，负÷负得正
                    pct_rel = (last - M) / den * 100.0 if den < 0 else 0.0
                    side_hi = False
                rng = st["H"] - st["L"]
                pos = (last - st["L"]) / rng * 100.0 if rng > 0 else 50.0   # 全区间位置（备用/参考）
                it.update({"H": st["H"], "L": st["L"], "M": M,
                           "bars": st["bars"], "from": st["from"], "to": st["to"],
                           "position": round(pos, 2), "percent": round(pct_rel, 2),
                           "above": side_hi})
            else:
                it.update({"H": 0, "L": 0, "M": 0, "bars": 0, "from": "", "to": "",
                           "position": 50.0, "percent": 50.0, "above": False})
            thr = self.thresholds.get(key) or {}
            it["up"] = thr.get("up")
            it["down"] = thr.get("down")
            it["locked"] = bool(thr.get("locked"))
            it["auto"] = thr.get("auto")
            it["maValue"] = ma_for(key, last)
            it["maKind"] = kind
            it["maN"] = ma
            # 该品种此刻是否处于「可发信的交易状态」（闭市时不发预警邮件）
            mkt_ok, mkt_why = market_state(q, v["exch"], self._stale_sec())
            it["mktOpen"] = bool(mkt_ok)
            it["mktNote"] = mkt_why
            items.append(it)

        # 排序口径对齐参照网页：两栏**都按百分比降序**（越极端越靠前）。
        above = sorted([x for x in items if x["above"] and x["bars"]],
                       key=lambda x: x["percent"], reverse=True)
        below = sorted([x for x in items if (not x["above"]) and x["bars"]],
                       key=lambda x: x["percent"], reverse=True)
        for i, x in enumerate(above, 1):
            x["rank"] = i
            x["side"] = "down"                  # 高于均值 → 设下阈
        for i, x in enumerate(below, 1):
            x["rank"] = i
            x["side"] = "up"                    # 低于均值 → 设上阈

        mp = float(self.panel("minPercent", 70))
        srcc = self.src()
        payload = {
            "ready": True,
            "source": self.source_name,
            "sourceLabel": srcc.label,
            "basisName": "加权（仓权）合约",
            "date": time.strftime("%Y-%m-%d"),
            "updated": now_str(),
            "elapsed": round(time.time() - t0, 2),
            "total": len(items),
            "skipped": skipped,
            "items": items,
            "above": above,
            "below": below,
            "stats": {
                "above": len(above), "below": len(below),
                # 门槛判定与参照网页一致：pct > 门槛 才算「极端」（高侧贴近高位 / 低侧贴近低位）
                "up70": len([x for x in above if x["percent"] > mp]),
                "down70": len([x for x in below if x["percent"] > mp]),
            },
            "thresholds": dict(self.thresholds),
            "minPercent": mp,
            "tickSec": int(self.panel("tickSec", 2)),
            "refreshSec": int(self.panel("refreshSec", 30)),
            "soundOn": bool(self.panel("soundOn", True)),
            "thrKind": kind, "thrMA": ma,
            "thrKinds": THR_KINDS, "maChoices": MA_CHOICES,
            "chartMA": list(self.panel("chartMA", [5, 8, 10, 20, 30, 50])),
            "kinds": ["timeshare", "m5", "m15", "m30", "d"],
            "alerts": self.alerts,
            "mailLog": self.mail_log[-8:],
            "mailNote": self.mail_note[-8:],
            "mailGuard": self._mail_guard(),
            "error": self.error,
        }
        with self.lock:
            self.data = payload
        self._check_alerts(items)
        return payload

    # ---------------- 预警 ---------------- #
    def _mail_guard(self) -> dict:
        """邮件防轰炸守护状态（面板展示）。"""
        now = time.time()
        sent = list(self.mail_sent)
        try:
            per_h = int(self.panel("mailMaxPerHour", 6))
            per_d = int(self.panel("mailMaxPerDay", 30))
        except (TypeError, ValueError):
            per_h, per_d = 6, 30
        h = len([t for t in sent if now - t < 3600])
        return {
            "on": bool(self.panel("mailOn", True)),
            "tradingOnly": bool(self.panel("mailTradingOnly", True)),
            "edgeOnly": bool(self.panel("mailEdgeOnly", True)),
            "onStart": bool(self.panel("mailOnStart", False)),
            "cooldown": int(self.panel("mailCooldown", 600) or 0),
            "releasePct": float(self.panel("mailReleasePct", 0.3) or 0),
            "staleSec": self._stale_sec(),
            "maxPerHour": per_h, "maxPerDay": per_d,
            "sentHour": h, "sentDay": len(sent),
            "queued": len(self.mail_queue),
            "trigCount": sum(1 for v in self.trig.values() if v.get("state")),
            "suppressed": dict(self.suppressed),
            "nextAllowed": ("可发信" if (per_h - h > 0 and per_d - len(sent) > 0)
                            else "已达上限，暂停发信"),
        }

    def _stale_sec(self) -> int:
        try:
            return max(30, int(self.panel("mailStaleSec", 300)))
        except (TypeError, ValueError):
            return 300

    def note(self, msg: str):
        """记录一条「为什么没发邮件」的原因，保留最近 12 条供面板查看。"""
        self.mail_note.append("%s %s" % (time.strftime("%H:%M:%S"), msg))
        if len(self.mail_note) > 12:
            self.mail_note = self.mail_note[-12:]

    def _check_alerts(self, items: list):
        """阈值预警判定（**边沿触发**）。

        与旧实现的根本区别：旧版每轮刷新只要「价格在阈值之外」就尝试发信，
        于是持续超阈时会每 `mailCooldown` 秒重复轰炸一次（含整夜 / 整个周末）。
        现在只有「从未触发 → 触发」这一**瞬间**才算一次事件；持续处于触发态
        不重发，必须回落到滞回带以内复位后，再次突破才会重新计一次。
        """
        now = time.time()
        kl = {"m5": "5分K", "m15": "15分K", "m30": "30分K", "d": "日K"}
        edge_only = bool(self.panel("mailEdgeOnly", True))
        trading_only = bool(self.panel("mailTradingOnly", True))
        try:
            rel = max(0.0, float(self.panel("mailReleasePct", 0.3)))
        except (TypeError, ValueError):
            rel = 0.3
        fired, closed_hits = [], 0
        active = []                   # 当前位于阈值外的全部品种（面板展示用）

        for it in items:
            key = it["key"]
            price = it.get("price", 0) or 0
            up, down = it.get("up"), it.get("down")
            if not up and not down:
                self.trig.pop(key, None)
                continue
            st = self.trig.get(key)
            prev = st.get("state") if st else None

            # ---- 1) 当前是否位于阈值外（先把面板展示用数据备好，含持续触发态）----
            cur = None
            if up and price >= float(up):
                cur = "up"
            elif down and price <= float(down):
                cur = "down"

            base = None
            if cur:
                thr = self.thresholds.get(key) or {}
                kind = thr.get("kind") or self.panel("thrKind", "d")
                ma = thr.get("ma") or self.panel("thrMA", 5)
                limit = float(up if cur == "up" else down)
                base = {
                    "key": key, "name": it["name"], "exch": it["exch"],
                    "price": price, "percent": it.get("percent"), "M": it.get("M"),
                    "H": it.get("H"), "L": it.get("L"), "pct": it.get("pct"),
                    "unit": it.get("unit", ""), "digits": it.get("digits", 2),
                    "thrLabel": "%s MA%s" % (kl.get(kind, kind), ma),
                    "at": now_str(), "source": self.source_name,
                    "state": cur, "side": "上阈" if cur == "up" else "下阈",
                    "limit": limit, "diff": round(price - limit, 4),
                    "mktOpen": bool(it.get("mktOpen", True)),
                    "mktNote": it.get("mktNote", ""),
                }
                active.append(base)

            # ---- 2) 复位判定（不涉及发信，闭市期间同样执行）----
            if prev == "up":
                if up and price > float(up) * (1.0 - rel / 100.0):
                    continue                        # 仍在触发态（含滞回带）→ 保持，不重发
                self.trig.pop(key, None)
                prev = None
            elif prev == "down":
                if down and price < float(down) * (1.0 + rel / 100.0):
                    continue
                self.trig.pop(key, None)
                prev = None

            if not cur:
                continue

            # ---- 3) 交易时段门禁：闭市期间**不置位状态**，等同「开盘时才首破」----
            #      这样收盘后才设阈 / 隔夜跳空超阈的品种，会在开盘首轮补发一封，
            #      而不是在深夜与周末被反复轰炸。
            if trading_only and not it.get("mktOpen", True):
                closed_hits += 1
                continue

            if edge_only and prev == cur:
                continue                            # 理论上已被第 2 步拦掉，双保险

            # ---- 4) 新突破 → 置位 + 进入待发队列 ----
            self.trig[key] = {"state": cur, "since": now, "price": price}
            if self.first_scan and not self.panel("mailOnStart", False):
                continue                            # 启动首轮只建基线，不回补存量
            fired.append(dict(base, _q=now))

        if closed_hits:
            self.suppressed["closed"] += closed_hits
            self.note("闭市静默：%d 个品种处于触发价（非交易时段不发信）" % closed_hits)

        with self.lock:
            self.alerts = sorted(active, key=lambda a: a.get("percent") or 0,
                                 reverse=True)

        if self.first_scan:
            self.first_scan = False
            n_trig = sum(1 for v in self.trig.values() if v.get("state"))
            self.note("已建立触发基线：当前有 %d 个品种位于阈值外（按设置%s）"
                      % (n_trig, "已补发" if self.panel("mailOnStart", False) else "不补发"))
        self._send_alert_mail(fired)

    # ---- 发信配额 ----
    def _quota_left(self, now: float) -> int:
        self.mail_sent = [t for t in self.mail_sent if now - t < 86400]
        try:
            per_h = max(0, int(self.panel("mailMaxPerHour", 6)))
            per_d = max(0, int(self.panel("mailMaxPerDay", 30)))
        except (TypeError, ValueError):
            per_h, per_d = 6, 30
        h = len([t for t in self.mail_sent if now - t < 3600])
        d = len(self.mail_sent)
        return max(0, min(per_h - h, per_d - d))

    def _send_alert_mail(self, fired: list):
        if not self.panel("mailOn", True):
            return
        now = time.time()
        try:
            cd = int(self.panel("mailCooldown", 600))
            ttl = int(self.panel("mailQueueTTL", 1800))
        except (TypeError, ValueError):
            cd, ttl = 600, 1800

        # 待发池：队列中的旧 + 本轮新，按 key:state 去重（保留最新一条）
        pool: dict = {}
        for a in list(self.mail_queue) + list(fired):
            if now - a.get("_q", now) > ttl:
                self.suppressed["queueDrop"] += 1
                continue
            k = "%s:%s" % (a["key"], a["state"])
            if k not in pool or a.get("_q", 0) >= pool[k].get("_q", 0):
                pool[k] = a
        if not pool:
            self.mail_queue = []
            return

        # 同品种同方向冷却
        ready = {k: a for k, a in pool.items()
                 if now - self.last_mail.get(k, 0) >= cd}
        cooled = len(pool) - len(ready)
        if cooled:
            self.suppressed["cooldown"] += cooled
        if not ready:
            self.mail_queue = list(pool.values())
            return

        # 全局频率配额（每小时 / 每天）
        left = self._quota_left(now)
        if left <= 0:
            self.mail_queue = list(pool.values())
            self.suppressed["quota"] += 1
            self.note("已达发信上限（≤%s 封/时、≤%s 封/天），%d 条预警排队待发"
                      % (self.panel("mailMaxPerHour", 6),
                         self.panel("mailMaxPerDay", 30), len(pool)))
            return

        pick = sorted(ready.values(), key=lambda a: a.get("_q", 0))[:left]
        rest = [a for k, a in pool.items()
                if not any(a is b for b in pick)]
        self.mail_queue = rest

        for a in pick:
            self.last_mail["%s:%s" % (a["key"], a["state"])] = now

        label = {"quhe": "曲合加权", "tianqin": "天勤加权"}.get(self.source_name, "加权")
        try:
            cfg = self.config["mail"]
            html = MAIL.build_alert_html(pick, basis=label)
            subject = "【加权合约预警】%s 触发 %s" % (
                "、".join(a["name"] for a in pick[:3]),
                " / ".join(sorted({a["side"] for a in pick})))
            res = MAIL.send_mail(cfg, subject, html)
        except Exception as exc:                                    # noqa: BLE001
            res = {"ok": False, "message": str(exc)}
        if res.get("ok"):
            self.mail_sent.append(now)
        self.mail_log.append("%s %s → %s" % (
            now_str(), "邮件已发出" if res.get("ok") else "邮件失败",
            "、".join(a["name"] for a in pick)))
        if not res.get("ok"):
            self.mail_log[-1] += "（%s）" % res.get("message", "")



APP: App = None                                            # 由 main() 赋值


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "WeightedDashboard/2.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:                                           # noqa: BLE001
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:                                           # noqa: BLE001
            return {}

    # ---------------- GET ---------------- #
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                return self._static()
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path == "/api/status":
                return self._json({"ok": True, "started": APP.started,
                                   "warming": APP.warming,
                                   "source": APP.source_name,
                                   "total": APP.data.get("total", 0),
                                   "updated": APP.data.get("updated", ""),
                                   "error": APP.error})
            if path == "/api/config":
                return self._json({"ok": True, "config": APP.config})
            if path == "/api/source":
                return self._json({"ok": True, "sources": APP.sources.info()})
            if path == "/api/data":
                return self._json({"ok": True, "data": APP.data})
            if path == "/api/tick":
                if APP.warming and not APP.data.get("items"):
                    return self._json({"ok": True, "quotes": {}, "warming": True})
                q = APP.tick()
                slim = {k: {"last": v.get("last"), "pct": v.get("pct"),
                            "change": v.get("change"), "digits": v.get("digits"),
                            "high": v.get("high"), "low": v.get("low"),
                            "volume": v.get("volume"), "oi": v.get("oi"),
                            "trading": v.get("trading")}
                        for k, v in q.items()}
                return self._json({"ok": True, "quotes": slim,
                                   "updated": now_str(), "source": APP.source_name})
            if path == "/api/kline":
                key = (qs.get("key") or qs.get("code") or [""])[0]
                kind = (qs.get("kind") or ["d"])[0]
                limit = (qs.get("limit") or [None])[0]
                if not key:
                    return self._json({"ok": False, "message": "缺少 key"}, 400)
                if key not in PT.BY_KEY:
                    return self._json({"ok": False, "message": "未知品种 %s" % key}, 404)
                if kind not in ("timeshare", "m5", "m15", "m30", "d"):
                    return self._json({"ok": False, "message": "未知周期 %s" % kind}, 400)
                data = APP.kline(key, kind, int(limit) if limit else None)
                data["name"] = PT.NAME.get(key, "")
                data["exch"] = PT.EXCH.get(key, "")
                data["source"] = APP.source_name
                thr = APP.thresholds.get(key) or {}
                data["thresholds"] = {"up": thr.get("up"), "down": thr.get("down")}
                data["digits"] = next(
                    (x["digits"] for x in APP.data.get("items", []) if x["key"] == key), 2)
                return self._json({"ok": True, "data": data})
            return self._json({"ok": False, "message": "not found"}, 404)
        except Exception as exc:                                    # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "message": str(exc)}, 500)

    # ---------------- POST ---------------- #
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        body = self._body()
        try:
            if path == "/api/config":
                old_src = APP.config.get("panel", {}).get("source")
                APP.config = deep_merge(APP.config, body.get("config") or {})
                APP.save_config()
                APP.sources.configure(APP.config)
                new_src = APP.config.get("panel", {}).get("source")
                need_rebuild = (new_src != old_src)
                if need_rebuild:
                    r = APP.sources.switch(new_src, APP.config)
                    if not r.get("ok"):
                        return self._json({"ok": False, "message": r.get("message"),
                                           "config": APP.config})
                    APP._kl_cache.clear()
                    APP.mark_switching(new_src)
                    APP.rebuild_stats_async()
                else:
                    APP.refresh()
                return self._json({"ok": True, "config": APP.config,
                                   "source": APP.source_name})

            if path == "/api/source":
                name = body.get("source")
                if name not in ("quhe", "tianqin"):
                    return self._json({"ok": False, "message": "参数错误"}, 400)
                r = APP.sources.switch(name, APP.config)
                if not r.get("ok"):
                    return self._json({"ok": False, "message": r.get("message", "切换失败"),
                                       "sources": APP.sources.info()})
                APP.config.setdefault("panel", {})["source"] = name
                APP.save_config()
                APP._kl_cache.clear()
                APP.mark_switching(name)
                APP.rebuild_stats_async()
                return self._json({"ok": True, "source": name,
                                   "info": APP.sources.info()})

            if path == "/api/refresh":
                APP.refresh()
                return self._json({"ok": True, "updated": APP.data.get("updated")})

            if path == "/api/threshold/set":
                key = body.get("key") or body.get("code")
                if not key:
                    return self._json({"ok": False, "message": "缺少 key"}, 400)
                thr = APP.thr(key)
                changed = False
                for k in ("up", "down"):
                    if k in body:
                        val = body[k]
                        thr[k] = None if val in (None, "", "null") else float(val)
                        changed = True
                if "locked" in body:
                    thr["locked"] = bool(body["locked"])
                if "auto" in body:
                    thr["auto"] = body["auto"] or None
                if "kind" in body:
                    thr["kind"] = body["kind"] or None
                if "ma" in body:
                    thr["ma"] = body["ma"] or None
                if changed:
                    APP.save_thresholds()
                APP.refresh()
                return self._json({"ok": True, "threshold": thr})

            if path == "/api/threshold/auto":
                key = body.get("key") or body.get("code")
                side = body.get("side")
                if not key or side not in ("up", "down"):
                    return self._json({"ok": False, "message": "参数错误"}, 400)
                kind = body.get("kind") or APP.panel("thrKind", "d")
                ma = int(body.get("ma") or APP.panel("thrMA", 5))
                if kind not in THR_KINDS:
                    return self._json({"ok": False, "message": "周期不合法"}, 400)
                if ma not in MA_CHOICES:
                    return self._json({"ok": False, "message": "均线周期不合法"}, 400)
                item = next((x for x in APP.data.get("items", []) if x["key"] == key), None)
                live = (item or {}).get("price")
                try:
                    bars = APP.src().kline(key, kind).get("bars") or []
                except Exception as exc:                            # noqa: BLE001
                    return self._json({"ok": False, "message": "取K线失败：%s" % exc}, 400)
                val = dynamic_ma(bars, ma, live)
                if val is None:
                    return self._json({"ok": False, "message": "该周期无K线数据，无法计算"}, 400)
                thr = APP.thr(key)
                thr[side] = float(val)
                thr["auto"] = side
                thr["kind"] = kind
                thr["ma"] = ma
                APP.save_thresholds()
                APP.refresh()
                return self._json({"ok": True, "threshold": thr, "value": float(val),
                                   "side": side, "kind": kind, "ma": ma})

            if path == "/api/mail/test":
                cfg = body.get("config") or APP.config["mail"]
                cfg = deep_merge(APP.config["mail"], cfg)
                html = MAIL.build_test_html(cfg)
                res = MAIL.send_mail(cfg, "【加权合约监控】测试邮件", html)
                return self._json({"ok": bool(res.get("ok")), "result": res})

            return self._json({"ok": False, "message": "not found"}, 404)
        except Exception as exc:                                    # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "message": str(exc)}, 500)

    def _static(self):
        try:
            with open(HTML_FILE, "rb") as fh:
                body = fh.read()
            self._send(200, body, "text/html; charset=utf-8")
        except Exception as exc:                                    # noqa: BLE001
            self._send(500, ("页面读取失败：%s" % exc).encode("utf-8"),
                       "text/plain; charset=utf-8")


# --------------------------------------------------------------------------- #
# 后台刷新循环
# --------------------------------------------------------------------------- #
def refresher_loop():
    while True:
        try:
            time.sleep(max(5, int(APP.panel("refreshSec", 30))))
            APP.refresh()
        except Exception:                                           # noqa: BLE001
            time.sleep(5)


def port_in_use(port: int) -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def pick_port(start: int, tries: int = 30) -> int:
    for p in range(start, start + tries):
        if not port_in_use(p):
            return p
    return start


def main() -> int:
    global APP
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8689)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    print("=" * 64)
    print(" 加权（仓权）合约监控面板 · 双数据源版")
    print("=" * 64)

    t0 = time.time()
    APP = App(warm=False)
    src = APP.src()
    print(" 数据源：%s" % src.label)
    print(" 正在后台预热（拉取全部品种历史日K），面板可先打开 …")
    if src.id == "tianqin":
        st = src.status()
        print(" 天勤账号：%s，连接于 %s" % (st.get("user"), st.get("connectedAt")))

    port = pick_port(args.port)
    url = "http://127.0.0.1:%d/" % port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    threading.Thread(target=refresher_loop, daemon=True).start()
    print(" 看板地址：%s" % url)
    print(" 按 Ctrl+C 退出")
    print("=" * 64)

    if not args.no_browser:
        def open_later():
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:                                       # noqa: BLE001
                pass
        threading.Thread(target=open_later, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        try:
            APP.sources.close()
        except Exception:                                           # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
