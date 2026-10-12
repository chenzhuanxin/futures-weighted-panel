# -*- coding: utf-8 -*-
"""
数据源路由 —— 曲合网 ↔ 天勤 切换
==================================

统一接口（两个源都实现）：
    id / label                      标识与显示名
    ok / status()                   可用性
    start() / stop()                生命周期
    quotes(keys) -> {key: quote}    实时行情
    kline(key, kind, limit)          K线（kind ∈ timeshare|m5|m15|m30|d）
    dayk(key) -> bars                日K全量
    timeshare(key) -> dict           当日分时

统一 key 见 品种表.py（形如 SHFE.rb / CZCE.SA / CFFEX.IF）。
"""

from __future__ import annotations

import threading

import 品种表 as PT
import 曲合行情 as QH
import 天勤行情 as TQ

SOURCES = [
    {"id": "quhe",    "label": "曲合网（金投网）", "needAccount": False,
     "desc": "免账号免密钥；日K约3.5年、分钟K单次500根"},
    {"id": "tianqin", "label": "天勤（TqSdk）",   "needAccount": True,
     "desc": "需天勤账号；日K 10年以上、分钟K 4000根、推送式实时"},
]


class SourceManager:
    """持有各数据源单例，按配置切换。"""

    def __init__(self, cfg: dict | None = None):
        self._lock = threading.RLock()
        self.cur = "quhe"
        self.cfg: dict = {}
        self._src: dict = {}
        self.configure(cfg or {})

    def configure(self, cfg: dict):
        with self._lock:
            self.cfg = cfg or {}
            panel = self.cfg.get("panel") or {}
            want = panel.get("source") or "quhe"
            if want in ("quhe", "tianqin"):
                self.cur = want

    # ---------------- 源实例 ---------------- #
    def source(self, name: str | None = None):
        name = name or self.cur
        with self._lock:
            if name == "quhe":
                if "quhe" not in self._src:
                    self._src["quhe"] = QH.QuheSource()
                return self._src["quhe"]
            if name == "tianqin":
                tqc = (self.cfg.get("tianqin") or {})
                user = (tqc.get("user") or "").strip()
                pwd = tqc.get("password") or ""
                s = self._src.get("tianqin")
                if s is None or s.user != user or s.password != pwd:
                    if s:
                        try:
                            s.stop()
                        except Exception:                           # noqa: BLE001
                            pass
                    s = TQ.TianqinSource(user, pwd)
                    self._src["tianqin"] = s
                return s
        raise ValueError("未知数据源：%s" % name)

    def current(self):
        return self.source(self.cur)

    # ---------------- 切换 ---------------- #
    def switch(self, name: str, cfg: dict | None = None) -> dict:
        if name not in ("quhe", "tianqin"):
            return {"ok": False, "message": "未知数据源 %s" % name}
        with self._lock:
            if cfg:
                self.cfg = cfg
            self.cur = name
        src = self.source(name)
        if name == "tianqin":
            t0 = _now()
            ok = src.start()
            if not ok:
                return {"ok": False, "message": src.err or "天勤连接失败",
                        "source": name, "status": src.status()}
            return {"ok": True, "source": name, "status": src.status(),
                    "elapsed": round(_now() - t0, 2)}
        return {"ok": True, "source": name, "status": src.status()}

    def info(self) -> dict:
        out = []
        for s in SOURCES:
            d = dict(s)
            try:
                st = self.source(s["id"]).status()
            except Exception as exc:                                # noqa: BLE001
                st = {"ok": False, "err": str(exc)[:150]}
            d["status"] = st
            d["active"] = (s["id"] == self.cur)
            try:
                d["keyCount"] = len(PT.keys_of(s["id"]))
            except Exception:                                       # noqa: BLE001
                d["keyCount"] = None
            out.append(d)
        return {"current": self.cur, "list": out,
                "keys": PT.keys_of(self.cur), "keysAll": PT.KEYS}

    def close(self):
        with self._lock:
            for s in self._src.values():
                try:
                    s.stop()
                except Exception:                                   # noqa: BLE001
                    pass
            self._src.clear()


def _now():
    import time
    return time.time()


if __name__ == "__main__":
    mgr = SourceManager({"panel": {"source": "quhe"}})   # 天勤账号走 缓存/配置.json
    info = mgr.info()
    print("当前数据源：", info["current"])
    for s in info["list"]:
        print("  %-8s %-18s 可用=%-5s 品种=%-3d %s"
              % (s["id"], s["label"], s["status"].get("ok"), len(PT.keys_of(s["id"])), s["desc"]))
    print()
    for name in ("quhe", "tianqin"):
        r = mgr.switch(name)
        print("切换 %s → %s %s" % (name, r["ok"], r.get("message", "")))
        if r["ok"]:
            src = mgr.current()
            qs = src.quotes()
            print("   实时行情 %d 个，样例：" % len(qs),
                  {k: round(qs[k]["last"], 2) for k in list(qs)[:3]})
            k = "CZCE.SA" if "CZCE.SA" in qs else list(qs)[0]
            kd = src.kline(k, "d")
            km = src.kline(k, "m5")
            print("   %s 日K %d 根，5分K %d 根" % (k, kd["count"], km["count"]))
            ts = src.timeshare(k)
            print("   分时 %d 点" % ts["count"])
    mgr.close()
