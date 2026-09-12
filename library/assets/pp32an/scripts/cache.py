#!/usr/bin/env python3
"""结果缓存（L0）：精确缓存 + 语义缓存（无模型依赖版）。

设计依据：
- 精确：规范化键（norm + need_type）；
- 语义：相似度 ≥ registry.semantic_threshold（GPT Semantic Cache 的 0.8 思路；
  无 embedding 时用字符/词典级相似降级，阈值需按真实语料校准——诚实边界）；
- TTL 分级：口径/制度/术语/课程 24h、搜索 1h（registry.ttl_seconds）；
- 缓存是**派生层**：可重建、可清理（`clear`），不承载唯一知识。
"""
from __future__ import annotations

import json

from common import CACHE_F, age_hours, append_jsonl, now_iso, read_jsonl, sha1, similarity
from registry import ttl_for


def key_of(norm: str, need_type: str) -> str:
    return sha1("%s|%s" % (need_type, norm))


def _fresh(entry: dict, ttl: int) -> bool:
    return age_hours(entry.get("at", "")) <= ttl


def get(norm: str, need_type: str) -> dict:
    """精确命中 → {"hit": "exact", "entry": …}；否则 None。"""
    k = key_of(norm, need_type)
    ttl = ttl_for(need_type)
    for entry in reversed(read_jsonl(CACHE_F)):
        if entry.get("key") == k and _fresh(entry, ttl):
            return {"hit": "exact", "entry": entry}
    return {}


def get_semantic(norm: str, need_type: str, threshold: float) -> dict:
    """语义命中 → {"hit": "semantic", "entry": …, "similarity": s}；threshold 由 registry 配置。"""
    ttl = ttl_for(need_type)
    best, best_s = None, 0.0
    for entry in reversed(read_jsonl(CACHE_F)):
        if entry.get("need_type") != need_type or not _fresh(entry, ttl):
            continue
        s = similarity(norm, entry.get("norm", ""))
        if s > best_s:
            best, best_s = entry, s
    if best and best_s >= threshold:
        return {"hit": "semantic", "entry": best, "similarity": round(best_s, 4)}
    return {}


def put(norm: str, need_type: str, payload: dict) -> None:
    """写缓存：payload 为答案摘要 + 来源/信任/版本/证据（key 扩展：同时存 norm 与 facts）。"""
    append_jsonl(CACHE_F, {
        "key": key_of(norm, need_type), "norm": norm, "need_type": need_type,
        "at": now_iso(), "answer": str(payload.get("answer") or "")[:600],
        "best": payload.get("best") or {}, "source": payload.get("source") or "",
        "trust": payload.get("trust") or "", "version": payload.get("version"),
        "evidence": payload.get("evidence") or [],
    })


def invalidate(norm: str, need_type: str, threshold: float = 0.85) -> dict:
    """拒答/纠错后的缓存失效（研究依据：语义缓存会把**错答**复利式喂给相似查询 →
    必须可失效，不能只靠 TTL）。删除精确键 + 同 need_type 中相似度 ≥ threshold 的条目。

    返回 {"exact": n, "semantic": n}；缓存是派生层，删除无害、可重建。
    """
    k = key_of(norm, need_type)
    exact = semantic = 0
    keep = []
    for entry in read_jsonl(CACHE_F):
        if entry.get("key") == k:
            exact += 1
            continue
        if entry.get("need_type") == need_type and similarity(norm, entry.get("norm", "")) >= threshold:
            semantic += 1
            continue
        keep.append(entry)
    if exact or semantic:
        CACHE_F.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep),
                           encoding="utf-8")
    return {"exact": exact, "semantic": semantic}


def stats() -> dict:
    rows = read_jsonl(CACHE_F)
    return {"entries": len(rows), "file": str(CACHE_F)}


def clear() -> int:
    n = len(read_jsonl(CACHE_F))
    if CACHE_F.is_file():
        CACHE_F.unlink()
    return n
