# -*- coding: utf-8 -*-
"""
邮件预警
========

用 126 邮箱（SMTP/SSL）把加权合约的阈值预警以「卡片式」HTML 邮件发出。

邮件正文刻意写成**内联样式的表格卡片**（不用 <style> 标签、不用 flex/grid），
因为 QQ 邮箱 / 126 邮箱 / 微信内置浏览器对 CSS 的支持参差不齐，
只有 table + inline-style 的路子在各家客户端都能正常显示。
"""

from __future__ import annotations

import smtplib
import ssl
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate

# 配色（与前端看板保持一致）
C_UP = "#e0403f"        # 上破（涨）—— 中国习惯：红
C_DOWN = "#12a150"      # 下破（跌）—— 绿
C_DARK = "#1f2a37"
C_GRAY = "#6b7280"
C_LINE = "#e5e7eb"


def _fmt_price(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if abs(f) >= 10000:
        return "{:,.1f}".format(f)
    if abs(f) >= 1000:
        return "{:,.2f}".format(f)
    return "{:.2f}".format(f)


def _alert_row(a: dict) -> str:
    is_up = a.get("state") == "up"
    color = C_UP if is_up else C_DOWN
    bg = "#fff5f5" if is_up else "#f2fbf6"
    arrow = "▲" if is_up else "▼"
    title = "上破上阈" if is_up else "下破下阈"
    side = "上阈" if is_up else "下阈"
    pct = a.get("percent")
    pct_txt = ("%.2f%%" % pct) if isinstance(pct, (int, float)) else "-"
    diff = ""
    try:
        d = float(a["price"]) - float(a["limit"])
        diff = "%+.2f" % d
    except Exception:                                 # noqa: BLE001
        diff = "-"

    return """
    <tr><td style="padding:8px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             style="background:{bg};border:1px solid {line};border-left:5px solid {color};
                    border-radius:12px;">
        <tr>
          <td width="52" valign="middle" align="center"
              style="padding:14px 0 14px 12px;">
            <div style="width:36px;height:36px;line-height:36px;border-radius:10px;
                        background:{color};color:#ffffff;font-size:16px;
                        text-align:center;font-weight:bold;">{arrow}</div>
          </td>
          <td valign="middle" style="padding:14px 14px 14px 6px;
                     font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
            <div style="font-size:16px;font-weight:700;color:{color};letter-spacing:.3px;">
              {name} <span style="font-size:12px;font-weight:600;color:{color};
                       background:{bg};border:1px solid {line};border-radius:20px;
                       padding:1px 8px;">{title}</span>
            </div>
            <div style="font-size:13px;color:{gray};line-height:1.7;margin-top:4px;">
              加权价 <b style="color:{dark};font-size:15px;">{price}</b>
              &nbsp;{cmp}&nbsp;{side}
              <b style="color:{dark};font-size:15px;">{limit}</b>
              <span style="color:{gray};">（{diff}）</span>
            </div>
            <div style="font-size:12px;color:{gray};margin-top:2px;">
              历史位置 {pct_txt} &nbsp;·&nbsp; 阈值基准 {thr_label}
            </div>
          </td>
        </tr>
      </table>
    </td></tr>
    """.format(bg=bg, line=C_LINE, color=color, arrow=arrow, name=a.get("name", ""),
               title=title, gray=C_GRAY, dark=C_DARK,
               price=_fmt_price(a.get("price")),
               limit=_fmt_price(a.get("limit")),
               cmp="高于" if is_up else "低于",
               side=side, diff=diff, pct_txt=pct_txt,
               thr_label=a.get("thrLabel", "-"))


def build_alert_html(alerts: list[dict], *, basis: str = "曲合加权",
                     test: bool = False) -> str:
    """把预警列表渲染成一张卡片式 HTML 邮件（内联样式，兼容各家邮箱）。"""
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    month_cn = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
                "十一", "十二"][now.month - 1]

    rows = "".join(_alert_row(a) for a in alerts)
    n_up = sum(1 for a in alerts if a.get("state") == "up")
    n_down = len(alerts) - n_up

    banner = ""
    if test:
        banner = """
        <tr><td style="padding:0 0 12px;">
          <div style="background:#fff8e1;border:1px dashed #f0b429;border-radius:10px;
                      padding:10px 14px;font-size:13px;color:#8a6d3b;
                      font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
            ⚙️ 这是一封<b>测试邮件</b>，用于验证 SMTP 配置是否可用；下列为当前触发的真实预警。
          </div>
        </td></tr>
        """

    return """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>加权合约阈值预警</title></head>
<body style="margin:0;padding:0;background:#eef2f7;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#eef2f7;padding:24px 12px;">
  <tr><td align="center">
    <table width="640" cellpadding="0" cellspacing="0" border="0"
           style="max-width:640px;width:100%;background:#ffffff;border-radius:18px;
                  overflow:hidden;box-shadow:0 12px 34px rgba(15,23,42,.10);">

      <!-- 头部 -->
      <tr><td style="background:#1f2a37;background-image:linear-gradient(135deg,#243b53 0%,#1f2a37 55%,#111827 100%);
                     padding:26px 28px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td valign="middle" width="52">
              <div style="width:44px;height:44px;line-height:44px;border-radius:12px;
                          background:rgba(255,255,255,.14);color:#ffffff;
                          font-size:22px;text-align:center;">🔔</div>
            </td>
            <td valign="middle" style="padding-left:14px;
                       font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
              <div style="font-size:20px;font-weight:700;color:#ffffff;letter-spacing:.5px;">
                加权合约阈值预警
              </div>
              <div style="font-size:13px;color:#9db4cc;margin-top:3px;">
                {basis}加权 · 实时监控 · {stamp}
              </div>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- 概览 -->
      <tr><td style="padding:18px 24px 6px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr>
            <td width="33%" align="center" style="padding:6px;">
              <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;padding:12px 6px;
                          font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
                <div style="font-size:22px;font-weight:800;color:#1f2a37;">{total}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:2px;">触发品种</div>
              </div>
            </td>
            <td width="33%" align="center" style="padding:6px;">
              <div style="background:#fff5f5;border:1px solid #ffd9d9;border-radius:12px;padding:12px 6px;
                          font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
                <div style="font-size:22px;font-weight:800;color:{up};">{nup}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:2px;">上破上阈</div>
              </div>
            </td>
            <td width="33%" align="center" style="padding:6px;">
              <div style="background:#f2fbf6;border:1px solid #cdeedb;border-radius:12px;padding:12px 6px;
                          font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
                <div style="font-size:22px;font-weight:800;color:{down};">{ndown}</div>
                <div style="font-size:12px;color:#6b7280;margin-top:2px;">下破下阈</div>
              </div>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- 明细 -->
      <tr><td style="padding:6px 24px 4px;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0">
          {banner}
          {rows}
        </table>
      </td></tr>

      <!-- 页脚 -->
      <tr><td style="padding:14px 24px 26px;">
        <div style="border-top:1px solid #e5e7eb;padding-top:14px;
                    font-size:12px;color:#9aa5b1;line-height:1.9;
                    font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
          本邮件由「加权合约监控面板」自动发出，请勿直接回复。<br>
          统计口径：H = 加权日K全历史最高 · L = 全历史最低 · M = 全历史均价 ·
          比例 = (加权价 − M) / (H − M)（高于 M 时）或 (加权价 − M) / (L − M)（低于 M 时）。<br>
          阈值来源：加权合约日K的 5 日均值（动态）。数据源：新浪财经期货行情。
        </div>
      </td></tr>

    </table>
    <div style="font-size:11px;color:#a0aab5;margin-top:14px;
                font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
      {y} 年 {m} 月 · 加权合约监控面板
    </div>
  </td></tr>
</table>
</body></html>""".format(basis=basis, stamp=stamp, total=len(alerts), nup=n_up,
                       ndown=n_down, up=C_UP, down=C_DOWN,
                       banner=banner, rows=rows,
                       y=now.year, m=month_cn)


def build_test_html(cfg: dict) -> str:
    """纯配置测试邮件（不依赖行情）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    to = "、".join(cfg.get("to") or [])
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;background:#eef2f7;padding:24px 12px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center">
  <table width="560" cellpadding="0" cellspacing="0" border="0"
         style="max-width:560px;width:100%;background:#fff;border-radius:18px;overflow:hidden;
                box-shadow:0 12px 34px rgba(15,23,42,.10);">
    <tr><td style="background:linear-gradient(135deg,#243b53 0%,#1f2a37 60%,#111827 100%);
                   padding:24px 26px;color:#fff;
                   font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;">
      <div style="font-size:19px;font-weight:700;">✅ SMTP 配置测试成功</div>
      <div style="font-size:13px;color:#9db4cc;margin-top:4px;">加权合约监控面板 · {now}</div>
    </td></tr>
    <tr><td style="padding:22px 26px;
                   font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
                   font-size:14px;color:#374151;line-height:2;">
      发件账号：<b>{user}</b><br>
      收件人：<b>{to}</b><br>
      服务器：<b>{host}:{port}</b>（{ssl}）<br>
      <div style="margin-top:14px;background:#f2fbf6;border:1px solid #cdeedb;
                  border-left:4px solid #12a150;border-radius:10px;padding:12px 14px;
                  font-size:13px;color:#1b5e20;">
        邮件通道已打通，触发阈值时预警邮件会自动发送到上面的收件人。
      </div>
    </td></tr>
  </table>
</td></tr></table>
</body></html>""".format(now=now, user=cfg.get("user", ""), to=to,
                       host=cfg.get("host", ""), port=cfg.get("port", ""),
                       ssl="SSL" if cfg.get("ssl", True) else "明文/STARTTLS")


def send_mail(cfg: dict, subject: str, html: str) -> dict:
    """发送一封 HTML 邮件。cfg 为 smtp 配置段。返回 {ok, message}。"""
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 465)
    user = (cfg.get("user") or "").strip()
    auth = (cfg.get("authcode") or "").strip()
    to_list = [x.strip() for x in (cfg.get("to") or []) if x and x.strip()]
    cc_list = [x.strip() for x in (cfg.get("cc") or []) if x and x.strip()]
    from_name = cfg.get("from_name") or "加权合约监控"

    if not host or not user or not auth:
        return {"ok": False, "message": "SMTP 服务器 / 发件账号 / 授权码不能为空"}
    if not to_list:
        return {"ok": False, "message": "收件人不能为空"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(from_name, "utf-8")), user))
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(html, "html", "utf-8"))

    rcpt = to_list + cc_list
    try:
        if cfg.get("ssl", True):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE      # 部分网络环境证书链不全
            with smtplib.SMTP_SSL(host, port, timeout=25, context=ctx) as s:
                s.login(user, auth)
                s.sendmail(user, rcpt, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=25) as s:
                s.ehlo()
                try:
                    s.starttls(context=ssl.create_default_context())
                    s.ehlo()
                except Exception:                     # noqa: BLE001
                    pass
                s.login(user, auth)
                s.sendmail(user, rcpt, msg.as_string())
        return {"ok": True, "message": "已发送至 " + "、".join(rcpt)}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}
