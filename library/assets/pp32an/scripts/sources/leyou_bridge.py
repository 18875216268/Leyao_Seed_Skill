#!/usr/bin/env python3
"""乐药云智库桥接 —— 第二优先（兜底）源。

原则（红线）：
- **只查不弹窗**：登录预检用 `status`（绝不触发扫码）；失效返回 LOGIN_REQUIRED + 手动扫码指引；
- **原样复用**：命令透传给随包的原生客户端 `sources/leyou/leyou_cloud.py`（单一来源，不重写）；
- 输出容错：客户端若返回 JSON 则解析；若为文本则截取作为 answer（evidence 标 cli），不臆造结构。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import LEYOU_TOKEN_F, SKILL_ROOT

CLI_DEFAULT = Path(__file__).resolve().parent / "leyou" / "leyou_cloud.py"


def _cli(cli: str | None = None) -> Path:
    """CLI 路径：registry 声明的 `cli`（相对 skill 根）优先，缺省用包内默认位置（换源不改代码）。"""
    return (SKILL_ROOT / cli) if cli else CLI_DEFAULT


def _run(cli: Path, args: list, timeout: float) -> tuple[int, str, str]:
    # 登录态只落用户区：客户端默认路径在包内 ✗ → 统一以全局参数 --token-file 指定（须在子命令前）
    try:
        p = subprocess.run([sys.executable, str(cli), "--token-file", str(LEYOU_TOKEN_F), *args],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(cli.parent), timeout=timeout,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})   # 子进程零写包（不落 __pycache__）
        # 客户端在准备登录时可能生成二维码占位图；本 skill 绝不扫码 → 清掉运行产物，保持包干净
        qr = cli.parent / "qrcode.png"
        if qr.exists():
            qr.unlink()
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %.0fs" % timeout
    except OSError as exc:
        return 125, "", str(exc)[:120]


def _looks_login_required(text: str) -> bool:
    return any(k in text for k in ("LOGIN_REQUIRED", "登录已过期", "未登录", "credential", "AUTH"))


def status(timeout: float = 10.0, cli: str | None = None) -> dict:
    """登录态预检（scan=False 语义：只复用本地/库凭证）。

    实测教训（2026-09-12）：客户端 status 返回退出码 0 但内容 `{"ok":true,"logged_in":false}`
    → **必须解析 JSON 的 logged_in**，只看退出码会把"未登录"误判为已登录（假阳性）。
    """
    path = _cli(cli)
    if not path.is_file():
        return {"ok": False, "reason": "DEPENDENCY_MISSING", "detail": "缺 %s" % path}
    rc, out, err = _run(path, ["status"], timeout)
    text = (out + err).strip()
    logged = None
    try:
        logged = bool(json.loads(text).get("logged_in"))
    except (json.JSONDecodeError, AttributeError):
        logged = None
    ok = (logged is True) if logged is not None else (rc == 0 and not _looks_login_required(text))
    if ok:
        return {"ok": True, "detail": text[:200]}
    return {"ok": False, "reason": "LOGIN_REQUIRED",
            "detail": ("未登录（logged_in=false）" if logged is False else text[:160]) or "退出码 %d" % rc,
            "next": "请人工扫码登录一次（本 skill 不代扫、不弹窗）："
                    "`python scripts/sources/leyou/leyou_firebase_login.py auto`；"
                    "登录态只落用户数据区（%s）" % LEYOU_TOKEN_F}


def search(problem: str, *, timeout: float = 12.0, limit: int = 5, cli: str | None = None) -> dict:
    """云智库检索（兜底）：未登录 → ok:false + LOGIN_REQUIRED（不弹窗）。"""
    st = status(min(8.0, timeout), cli=cli)
    if not st.get("ok"):
        return {"ok": False, "items": [], "ms": 0, "reason": st.get("reason"),
                "error": st.get("detail"), "next": st.get("next")}
    t0 = time.perf_counter()
    rc, out, err = _run(_cli(cli), ["search", problem], timeout)
    ms = int((time.perf_counter() - t0) * 1000)
    text = (out or err).strip()
    if rc != 0:
        return {"ok": False, "items": [], "ms": ms, "error": text[:200] or "退出码 %d" % rc,
                "reason": "LOGIN_REQUIRED" if _looks_login_required(text) else "FAILED"}
    items = _parse_items(text, limit)
    return {"ok": bool(items), "items": items, "ms": ms,
            "error": "" if items else "云智库无命中（原文前 120 字：%s）" % text[:120]}


def _parse_items(text: str, limit: int) -> list:
    """JSON 优先；否则按行/整段折成一条（诚实：evidence 标 cli，不臆造字段）。"""
    items = []
    try:
        data = json.loads(text)
        rows = data.get("data") or data.get("items") or data.get("results") or []
        for row in rows[:limit]:
            if isinstance(row, dict):
                items.append({
                    "answer": str(row.get("content") or row.get("summary") or row.get("title") or "")[:1200],
                    "title": str(row.get("title") or ""),
                    "source": "leyou", "trust": "reference", "confidence": 0.6,
                    "evidence": ["leyou#%s" % (row.get("slug") or row.get("id") or "?")],
                    "tags": [], "score": 0.0,
                })
    except json.JSONDecodeError:
        pass
    if not items and text:
        items.append({
            "answer": text[:1200], "title": text.splitlines()[0][:80] if text else "",
            "source": "leyou", "trust": "reference", "confidence": 0.5,
            "evidence": ["leyou#cli"], "tags": [], "score": 0.0,
        })
    return items
