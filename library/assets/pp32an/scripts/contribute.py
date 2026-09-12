#!/usr/bin/env python3
"""沉淀上传（对齐池侧原机制：learn 沉淀 → 本地质量闸 → 显式提交公共池）。

红线：**知识写入是显式动作**——本模块只被 `hub.py contribute` 显式调用；
`--dry-run` 只打印将发送的 payload、不发起任何网络写请求（用于预检与测试）。

本地质量闸（对应池侧「验证门禁」的最小可用版，与池侧三层闸 + 帕累托互补）：
① 记忆已确认（tier=semantic / pool-candidate）且 fail=0；② 内容非空且 ≥20 字；
③ `quality_score` 透明启发式（0.5 起，采纳 +0.1 / 否决 −0.15，钳制 0.3–0.95）。
"""
from __future__ import annotations

import memory
import registry as reg
from sources import pool
from sources.pool import CATEGORY_MAP     # 单一事实源：服务器 category 枚举映射（不重复定义）

TYPE_MAP = {"caliber": "lesson", "policy": "lesson", "term": "lesson",
            "course": "lesson", "search": "workflow"}       # 对齐池侧 distill_type 三分类
MIN_CONTENT = 20


def quality_of(m: dict) -> float:
    """透明启发式质量分（可核可调）：0.5 起，采纳 +0.1、否决 −0.15，钳制 [0.3, 0.95]。"""
    q = 0.5 + 0.1 * int(m.get("adopt", 0)) - 0.15 * int(m.get("fail", 0))
    return round(max(0.3, min(0.95, q)), 2)


def to_payload(m: dict) -> dict:
    """本地记忆 → 池条目 payload（字段与池侧 submit 协议一致）。"""
    dist = TYPE_MAP.get(m.get("need_type", ""), "lesson")
    return {"title": str(m.get("q") or "")[:120],
            "content": str(m.get("a") or "")[:1200],
            "category": CATEGORY_MAP.get(m.get("need_type", ""), "experience"),
            "distill_type": dist,
            "trust": "reference",                     # 沉淀默认参考级；权威需显式 inject
            "quality_score": quality_of(m),
            "contributor": "leyao-knowledge",
            "kind": "procedure" if dist == "workflow" else "fact"}


def gate(m: dict) -> tuple:
    """质量闸：不合格返回 (False, 原因)；通过返回 (True, \"通过\")。"""
    if m.get("status") != "active":
        return False, "非 active（已冷存）"
    if m.get("tier") not in ("semantic", "pool-candidate"):
        return False, "未达本地确认（需 semantic / pool-candidate）"
    if int(m.get("fail", 0)) > 0:
        return False, "存在否决记录（fail>0）"
    if len(str(m.get("a") or "").strip()) < MIN_CONTENT:
        return False, "内容过短（<%d 字）" % MIN_CONTENT
    return True, "通过"


def _asset() -> dict:
    data = reg.load()
    return next((a for a in data["ordered_assets"] if a.get("kind") == "pool"), None) or {}


def submit_memory(mid: str, dry_run: bool = False) -> dict:
    """显式提交单条本地记忆到公共池（服务端再走三层闸 + 帕累托）。"""
    m = memory.get(mid)
    if not m:
        return {"ok": False, "reason": "UNKNOWN_MEMORY_ID", "detail": mid}
    ok, why = gate(m)
    payload = to_payload(m)
    if not ok:
        return {"ok": False, "reason": "GATE_REJECTED", "detail": why, "payload": payload}
    if dry_run:
        return {"ok": True, "dry_run": True, "payload": payload,
                "detail": "预检通过（未发起网络写请求）"}
    a = _asset()
    r = pool.submit(a.get("endpoint", ""), token=str(a.get("write_token") or ""),
                    timeout=float(a.get("write_timeout_s") or 10.0), **payload)
    if r.get("ok") and m.get("tier") != "pool-candidate":
        memory.mark_pool_candidate(mid)               # 已提交 → 标记候选态（幂等）
    return {"ok": bool(r.get("ok")), "response": r, "payload": payload}


def submit_candidates(dry_run: bool = False) -> dict:
    """批量提交全部 pool-candidate 记忆（reflect 标记过的那批）。"""
    cands = [m for m in memory.all_active() if m.get("tier") == "pool-candidate"]
    items = []
    for m in cands:
        items.append(dict(submit_memory(m["id"], dry_run=dry_run), memory_id=m["id"]))
    return {"ok": all(it.get("ok") for it in items) if items else True,
            "count": len(items), "items": items}


def inject(title: str, content: str, *, category: str = "experience", kind: str = "fact",
           quality_score: float = 0.9, contributor: str = "user", dry_run: bool = False) -> dict:
    """写注入库（authority；**仅用户显式要求注入时调用**，不得自动触发）。"""
    payload = {"title": title, "content": content, "category": category, "kind": kind,
               "quality_score": quality_score, "contributor": contributor, "distill_type": None}
    if dry_run:
        return {"ok": True, "dry_run": True, "payload": payload,
                "detail": "预检（未发起网络写请求）"}
    a = _asset()
    r = pool.inject(a.get("endpoint", ""), token=str(a.get("write_token") or ""),
                    timeout=float(a.get("write_timeout_s") or 10.0), **payload)
    return {"ok": bool(r.get("ok")), "response": r, "payload": payload}
