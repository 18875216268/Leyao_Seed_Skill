#!/usr/bin/env python3
"""vendor 接入体检（防重复登录）：扫描 vendor/ 下每个子包，检出会引发「重复登录」的残留。

背景：本 skill 是**唯一登录口**；子包一律不登录、不读取父凭证仓库——凭证由 Agent 取用后
经 `--token` / `PMS_TOKEN` / `--state-file` 直接传入（见 vendor/SUBSKILL_ROUTING.md §3 第 6 条与 §4 接入验收）。

检查规则（命中即报，退出码 1）：
  L1 登录实现嫌疑：login/scan/qr 函数或类定义、二维码/鉴权域依赖、登录相关文件名
  L2 指路父登录：文档或代码出现 pms_login / 扫码 / 企微登录 / 重新登录 等「去登录」指引
  L3 读取父凭证仓库：pms-operations-query / PMS_OPERATIONS_HOME（父仓库路径）
  （vendor/SUBSKILL_ROUTING.md 是父 skill 自己的路由文档，不在体检范围。）

用法：
  python scripts/vendor_lint.py            # 体检全部子包（人读）
  python scripts/vendor_lint.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "vendor"
TEXT_EXT = {".py", ".md", ".json", ".yaml", ".yml", ".txt"}
SKIP_NAMES = {"SUBSKILL_ROUTING.md", ".gitkeep"}

RULES = [
    ("L1", "登录实现嫌疑", re.compile(
        r"(?:def|class)\s+\w*(?:login|scan|qrcode|qrcode)\w*"
        r"|login\.work\.weixin\.qq\.com|/cgi-bin/|auth\.leyopharm\.com"
        r"|import\s+(?:qrcode|PyQt5|pyautogui)", re.I)),
    ("L2", "指路父登录", re.compile(r"pms_login|扫码|企微登录|重新登录", re.I)),
    ("L3", "读取父凭证仓库", re.compile(r"pms-operations-query|PMS_OPERATIONS_HOME", re.I)),
]


def packages() -> list[Path]:
    """体检对象：vendor/leyo-sys（集团基础，如有）+ vendor/optimizers/ 下每个优化包。"""
    out = []
    base = VENDOR / "leyo-sys"
    if base.is_dir():
        out.append(base)
    opt = VENDOR / "optimizers"
    if opt.is_dir():
        out.extend(sorted(p for p in opt.iterdir() if p.is_dir()))
    return out


def scan(pkg: Path) -> list[dict]:
    findings: list[dict] = []
    for f in sorted(pkg.rglob("*")):
        if not f.is_file() or f.name in SKIP_NAMES or f.suffix.lower() not in TEXT_EXT:
            continue
        rel = str(f.relative_to(VENDOR))
        if re.search(r"(login|scan|qrcode)", f.name, re.I):
            findings.append({"rule": "L1", "label": "登录实现嫌疑",
                             "file": rel, "line": 0, "text": "文件名含登录/扫码字样"})
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            for rule, label, rx in RULES:
                if rx.search(ln):
                    findings.append({"rule": rule, "label": label, "file": rel,
                                     "line": i, "text": ln.strip()[:120]})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="vendor 接入体检（防重复登录）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    pkgs = packages()
    report, total = [], 0
    for pkg in pkgs:
        fs = scan(pkg)
        total += len(fs)
        report.append({"package": str(pkg.relative_to(VENDOR)), "ok": not fs, "findings": fs})

    if args.json:
        print(json.dumps({"ok": total == 0, "vendor": str(VENDOR), "packages": report},
                         ensure_ascii=False, indent=1))
    else:
        for r in report:
            print("[%s] %s" % ("OK" if r["ok"] else "FINDING", r["package"]))
            for f in r["findings"]:
                print("  - %s %s  %s:%s  %s" % (f["rule"], f["label"], f["file"], f["line"], f["text"]))
        print("=" * 48)
        print("接入体检：%d 个子包，%d 处发现%s"
              % (len(pkgs), total, "（干净：无重复登录风险）" if total == 0 else "（需处理）"))
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
