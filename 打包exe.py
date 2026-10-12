# -*- coding: utf-8 -*-
"""把「加权（仓权）合约监控面板」打包成单文件 EXE。

用法（Windows，在项目目录下执行）：

    C:\\Python314\\python.exe 打包exe.py

产物：

    发布/加权合约监控面板.exe

说明：
  · `看板.html` 通过 --add-data 打进 EXE，运行时从临时解包目录读取；
  · 配置与缓存（缓存/配置.json、阈值设置.json、曲合日K缓存.json）**不打包**，
    而是落在 **EXE 所在目录**（见 服务端.py / 曲合行情.py 的 _data_dir()），
    因此 EXE 拷到哪，配置就跟到哪，也不会把天勤账号 / 邮箱授权码带进 EXE；
  · 5 个中文模块名是**动态导入**（服务端.py 里用 importlib.import_module），
    PyInstaller 静态分析扫不到，必须用 --hidden-import 显式声明。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

NAME = "加权合约监控面板"
DIST = os.path.join(ROOT, "发布")
WORK = os.path.join(ROOT, "build")

# 动态导入的中文模块：必须显式声明，否则 EXE 启动即 ModuleNotFoundError
HIDDEN = ["品种表", "数据源", "邮件预警", "天勤行情", "曲合行情"]

# 排除明确无关的重型包（实测 tqsdk 顶层 import 不加载它们，可安全剔除、显著减小体积）
# ⚠ 千万不要排除 pydoc / scipy：实测 `import tqsdk` 会连带导入二者，
#   排除后 `from tqsdk import TqApi` 会抛异常，表现为「未安装 tqsdk」的假象。
EXCLUDE = [
    "matplotlib", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "IPython", "notebook", "jupyter", "jupyterlab",
    "pytest", "docutils", "sphinx",
]


def main() -> int:
    if not os.path.exists(os.path.join(ROOT, "看板.html")):
        print("× 找不到 看板.html，请在项目目录下运行本脚本")
        return 1

    for p in (DIST, WORK):
        os.makedirs(p, exist_ok=True)

    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--name", NAME,
           "--onefile",                       # 单文件，双击即用
           "--console",                       # 保留控制台看日志 / Ctrl+C 退出
           "--distpath", DIST,
           "--workpath", WORK,
           "--specpath", WORK,
           # ⚠ --add-data 的源路径要用**绝对路径**：加了 --specpath 后，
           #   相对路径会以 spec 所在目录为基准，导致找不到 看板.html
           "--add-data", os.path.join(ROOT, "看板.html") + os.pathsep + ".",
           "--collect-all", "tqsdk",          # tqsdk 有数据文件与子模块，整包收
           ]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for e in EXCLUDE:
        cmd += ["--exclude-module", e]
    cmd.append(os.path.join(ROOT, "服务端.py"))

    print("=" * 70)
    print(" 打包命令：")
    print(" " + " ".join(('"%s"' % c if " " in c else c) for c in cmd))
    print("=" * 70)

    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("× 打包失败，返回码 %d" % r.returncode)
        return r.returncode

    exe = os.path.join(DIST, NAME + ".exe")
    if not os.path.exists(exe):
        print("× 未找到产物 %s" % exe)
        return 2

    size = os.path.getsize(exe) / 1048576.0
    print("=" * 70)
    print(" √ 打包完成：%s" % exe)
    print("   体积：%.1f MB" % size)
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
