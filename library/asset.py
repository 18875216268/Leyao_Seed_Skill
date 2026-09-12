#!/usr/bin/env python3
"""资产直读工具（只读 · 确定性访问；**不依赖搜索 / 宿主文件工具**）。

  python library/asset.py resolve <相对路径|节点id>   # 解析：类型 / 大小 / 行数 / 入口文件；给节点 id 则给挂载目录
  python library/asset.py list <相对目录>             # 列一层（确定性；不递归——更深层按相对路径继续 list）
  python library/asset.py read <相对路径> [--head N] [--tail N] [--lines A-B]
                                                      # 读文件：带行号输出 + **读取凭据**（路径 / 行数 / 字节 / sha1）

约定：路径一律相对**包根**（本文件所在 `library/` 的上一级；**参数与 cwd 无关**，脚本路径按你的 cwd 写全即可）；`..` / 绝对路径一律拒绝 ✗；**只读** ✗ 不写。
示例：python library/asset.py read library/assets/bvix9o/高频场景指引/子公司（店铺）整月销售目标拆解.txt

分层：本工具只做「已知路径 → 内容」；**路由（选哪个资产）在 `library/engine.py` / `ROUTES.md`**，两者不重叠。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def _safe(arg: str):
    """相对包根解析；越界（绝对路径 / ..）→ None。"""
    if not arg or Path(arg).is_absolute():
        return None
    p = (ROOT / arg).resolve()
    try:
        p.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return p


def _node(arg: str):
    f = ROOT / "library" / "routes.json"
    if not f.is_file():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))

    def walk(ns):
        for n in ns:
            yield n
            yield from walk(n.get("children") or [])

    return next((n for n in walk(data.get("nodes") or []) if n.get("id") == arg), None)


def _entries(p: Path):
    return sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))


def cmd_resolve(args) -> int:
    n = _node(args.target)
    if n is not None:
        m = n.get("mount")
        print("[asset] 节点 %s「%s」→ 挂载 `%s`" % (n.get("id"), n.get("title"), m or "（无·容器节点，下钻子节点）"))
        if m:
            p = _safe(m.rstrip("/"))
            if p and p.is_dir():
                print("[asset] 挂载存在 ✓ ｜ 直属条目 %d" % len(_entries(p)))
                for e in _entries(p)[:20]:
                    print("  - %s%s" % (_rel(e), "/" if e.is_dir() else ""))
                for cand in ("SKILL.md", "README.md"):
                    if (p / cand).is_file():
                        print("[asset] 入口文档：%s" % _rel(p / cand))
            else:
                print("[asset] ✗ 挂载不存在 → 视为不可用（见 ROUTES.md ⚠ 标记 / `control.md`〈卡壳处置〉）")
        return 0
    p = _safe(args.target)
    if p is None:
        print("[asset] ✗ 拒绝：只接受相对包根的路径（不可 `..` / 绝对路径）")
        return 1
    if not p.exists():
        _hint = "（该词形似节点 id，但未在 `library/routes.json` 命中——先 resolve <节点id> 拿挂载目录）" \
            if len(args.target) == 6 and args.target.isalnum() else "（可先用 resolve <节点id> 拿挂载目录）"
        print("[asset] ✗ 不存在：%s %s" % (_rel(p), _hint))
        return 1
    if p.is_dir():
        print("[asset] 目录 %s ｜ 直属条目 %d" % (_rel(p), len(_entries(p))))
        for e in _entries(p)[:30]:
            print("  - %s%s" % (_rel(e), "/" if e.is_dir() else ""))
    else:
        b = p.read_bytes()
        try:
            lines = b.decode("utf-8").count("\n") + 1
        except UnicodeDecodeError:
            lines = -1
        print("[asset] 文件 %s ｜ 字节 %d ｜ 行数 %s ｜ sha1 %s"
              % (_rel(p), len(b), lines if lines > 0 else "（二进制）", hashlib.sha1(b).hexdigest()[:8]))
    return 0


def cmd_list(args) -> int:
    p = _safe(args.dir)
    if p is None or not p.is_dir():
        print("[asset] ✗ 目录不存在或越界：%s" % args.dir)
        return 1
    ents = _entries(p)
    print("[asset] %s ｜ 直属条目 %d（不递归；更深层按相对路径继续 list）" % (_rel(p), len(ents)))
    for e in ents:
        print("  - %s%s%s" % (_rel(e), "/" if e.is_dir() else "", "" if e.is_dir() else "  (%d 字节)" % e.stat().st_size))
    return 0


def cmd_read(args) -> int:
    p = _safe(args.path)
    if p is None or not p.is_file():
        print("[asset] ✗ 文件不存在或越界：%s（先 resolve <节点id> 或 list <目录>）" % args.path)
        return 1
    b = p.read_bytes()
    sha = hashlib.sha1(b).hexdigest()[:8]
    try:
        text = b.decode("utf-8")
    except UnicodeDecodeError:
        print("[asset] （二进制文件，不展示内容）读取凭据：%s ｜ 字节 %d ｜ sha1(全文) %s（以此充当锚点）"
              % (_rel(p), len(b), hashlib.sha1(b).hexdigest()))
        return 0
    lines = text.split("\n")
    lo, hi = 1, len(lines)
    if args.lines:
        a, _, z = args.lines.partition("-")
        lo = int(a or 1)
        hi = int(z) if z else len(lines)
    elif args.head:
        hi = min(hi, lo + args.head - 1)
    elif args.tail:
        lo = max(1, len(lines) - args.tail + 1)
    lo = max(1, lo)
    hi = min(hi, len(lines))
    for i in range(lo, hi + 1):
        print("%4d| %s" % (i, lines[i - 1]))
    print("[asset] 读取凭据：%s ｜ 行数 %d ｜ 字节 %d ｜ sha1 %s ｜ 本次展示 %d-%d（全文共 %d 行）"
          % (_rel(p), len(lines), len(b), sha, lo, hi, len(lines)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="资产直读（只读 · 相对包根 · 零搜索依赖）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="解析相对路径或节点 id")
    r.add_argument("target")
    r.set_defaults(func=cmd_resolve)
    l = sub.add_parser("list", help="列一层目录")
    l.add_argument("dir")
    l.set_defaults(func=cmd_list)
    d = sub.add_parser("read", help="读文件（带读取凭据）")
    d.add_argument("path")
    d.add_argument("--head", type=int, help="只展示前 N 行（与 --tail/--lines 同给时按 lines > head > tail 取一）")
    d.add_argument("--tail", type=int, help="只展示后 N 行")
    d.add_argument("--lines", help="展示 A-B 行")
    d.set_defaults(func=cmd_read)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
