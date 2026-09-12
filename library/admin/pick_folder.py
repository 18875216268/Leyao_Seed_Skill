#!/usr/bin/env python3
"""原生文件夹选择对话框（独立进程，避免与 HTTP 工作线程冲突）。

用法：python pick_folder.py --initial "<初始目录>"
输出：所选文件夹的绝对路径（stdout）；取消则输出空。
退出码：0 正常；3 本机缺少 tkinter。
"""
from __future__ import annotations

import argparse
import sys

sys.dont_write_bytecode = True          # 运行期零写包（不在包内生成 __pycache__）


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--initial", default="")
    args = ap.parse_args()

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"tkinter unavailable: {e}\n")
        return 3

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    kwargs = {"title": "选择文件夹"}
    if args.initial:
        kwargs["initialdir"] = args.initial
    try:
        path = filedialog.askdirectory(mustexist=True, **kwargs)
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    sys.stdout.write(path or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
