#!/usr/bin/env python3
"""优先链调度（本 skill 的核心）：精确缓存 → 语义缓存 → 本地记忆 → 公共池 → 云智库 → 拒答。

约定（设计 v2）：
- **早停**：任一层命中即返回（默认不查后续源）——"公共池优先、查不到才走云智库"即由此保证；
- `--expand`：不早停，公共池与云智库都取（多可能性/交叉验证）；
- 预算：总预算来自 registry.budget_seconds，逐层超时来自各资产 timeout_s；到点即停并如实记录；
- 每次 ask 写 ask-log（query_id 锚点）→ 供 feedback/reflect 闭环；
- 命中后把结果写入缓存与本地记忆（越用越快、越用越准）。
"""
from __future__ import annotations

import time
from datetime import datetime

import cache
import feedback
import infer as infer_mod
import memory
import query_norm
import rank
import registry as reg
import source_loader
from common import CN, sha1

MEMORY_RELEVANCE_FLOOR = 0.25      # 本地记忆入选下限（低于视为不相关，避免"什么都像"）
ANSWER_TRUNC = 300                 # 默认返回摘要长度（--full 展开）


def _query_id(norm: str) -> str:
    return "q_%s_%s" % (datetime.now(CN).strftime("%Y%m%d%H%M%S"), sha1(norm)[:4])


def _poss(m: dict) -> dict:
    """本地记忆 → 可能性结构。"""
    return {"answer": m.get("a", ""), "title": m.get("q", ""), "source": "memory",
            "trust": m.get("trust") or "local", "confidence": min(1.0, round(m.get("score", 0) / 3.0, 3)),
            "version": m.get("version"), "freshness": m.get("freshness"),
            "evidence": m.get("evidence") or ["memory#%s" % m.get("id")],
            "tags": m.get("tags") or [], "score": m.get("score", 0.0), "memory_id": m.get("id")}


def _from_cache(entry: dict, kind: str, confidence: float) -> dict:
    """缓存命中 → 恢复**完整条目**（实测教训：只存 answer 会丢 title/version/confidence ✗）。"""
    best = dict(entry.get("best") or {})
    best.setdefault("answer", entry.get("answer", ""))
    best.setdefault("source", entry.get("source", "cache"))
    best.setdefault("trust", entry.get("trust", ""))
    best.setdefault("evidence", entry.get("evidence") or [])
    best["confidence"] = confidence
    best["cache"] = kind
    best["cached_at"] = entry.get("at") or ""    # 透出缓存写入时间（陈旧度可审计）
    best["score"] = 1.0 if kind == "exact" else round(confidence, 4)
    best.setdefault("title", "")
    return best


def _finalize(out: dict, t0: float, limit: int, full: bool, query_id: str,
              path: list, early_stop: bool, suggestions: list) -> dict:
    items = out.get("items") or []
    total = len(items)
    items = items[:limit]
    for it in items:
        if not full:
            it["answer"] = str(it.get("answer") or "")[:ANSWER_TRUNC]
    elapsed = int((time.perf_counter() - t0) * 1000)
    return {
        "ok": True, "plugin": "leyao-knowledge", "protocol": "1.0",
        "problem": out.get("problem", ""), "need_type": out.get("need_type", ""),
        "answer": (items[0].get("answer") if items else ""),
        "best": items[0] if items else {},
        "possibilities": items,
        "path": path, "resolved": True, "early_stop": early_stop,
        "elapsed_ms": elapsed, "query_id": query_id,
        "has_more": total > len(items), "next_offset": len(items) if total > len(items) else None,
        "total_count": total,
        "suggestions": suggestions,
        "need_type_why": out.get("need_type_why", ""),
        "time_hint": out.get("time_hint", ""),
    }


def ask(problem: str, *, need_type: str | None = None, expand: bool = False,
        no_cache: bool = False, limit: int = 20, full: bool = False,
        tier: str | None = None, kind: str | None = None) -> dict:
    t0 = time.perf_counter()
    data = reg.load()
    budget = reg.budget_seconds(data)
    norm_info = query_norm.normalize(problem)
    norm, solved_by = norm_info["norm"], None
    terms = query_norm.search_terms(problem)     # 服务器检索词（核心词 + 别名扩展）
    gate = [query_norm.core_term(problem) or problem]   # 本地相关性门槛/排序只用**核心词**（防别名跑题命中）
    if not need_type:
        inf = infer_mod.infer(problem)
        need_type, nt_why = inf["need_type"], inf["why"]
    else:
        nt_why = "调用方显式指定"
    query_id = _query_id(norm)
    path, suggestions = [], []

    def left() -> float:
        return budget - (time.perf_counter() - t0)

    # ---------- L0 缓存 ----------
    if not no_cache:
        hit = cache.get(norm, need_type)
        path.append({"layer": "cache-exact", "ok": bool(hit), "ms": 2})
        if hit:
            return _finalize({"problem": problem, "need_type": need_type, "need_type_why": nt_why,
                              "time_hint": norm_info["time_hint"],
                              "items": [_from_cache(hit["entry"], "exact", 1.0)]},
                             t0, limit, full, query_id, path, True, suggestions)
        th = reg.semantic_threshold(data)
        sem = cache.get_semantic(norm, need_type, th)
        path.append({"layer": "cache-semantic", "ok": bool(sem), "ms": 3,
                     "threshold": th, "similarity": sem.get("similarity")})
        if sem:
            return _finalize({"problem": problem, "need_type": need_type, "need_type_why": nt_why,
                              "time_hint": norm_info["time_hint"],
                              "items": [_from_cache(sem["entry"], "semantic",
                                                    float(sem.get("similarity", 0.9)))]},
                             t0, limit, full, query_id, path, True, suggestions)

    # ---------- L1 本地记忆 ----------
    t = time.perf_counter()
    mems = [m for m in memory.all_active() if m.get("need_type") in (need_type, "search", "")]
    scored = rank.score_memories(mems, norm) if mems else []
    picked = [m for m in scored
              if m.get("score_parts", {}).get("relevance_raw", 0) >= MEMORY_RELEVANCE_FLOOR]
    path.append({"layer": "memory", "ok": bool(picked), "ms": int((time.perf_counter() - t) * 1000)})
    mem_items = [_poss(m) for m in picked]
    used_mids = [m.get("id") for m in picked[:limit]]
    for mid in used_mids:
        memory.touch(mid)

    # ---------- L2 公共池（第一优先源） ----------
    pool_items, pool_err = [], ""
    pool_asset = next((a for a in reg.by_need(need_type, data) if a.get("kind") == "pool"), None)
    if pool_asset and left() > 1:
        t = time.perf_counter()
        # 口径（caliber）只查注入库（tier=inject）——对齐池侧语义：口径必须权威
        tier_eff = tier or ("inject" if need_type == "caliber" else None)
        r = source_loader.pool_search(problem, need_type, pool_asset, min(limit * 2, 40),
                                      tier_eff, terms, kind)
        raw_items = r.get("items") or []
        # 相关性门槛：低相关命中视为"空"（否则会阻止云智库兜底）；--expand 时保留全收集
        pool_items = ([it for it in raw_items
                       if rank.relevance_of(gate, it) >= rank.REMOTE_RELEVANCE_FLOOR]
                      if not expand else raw_items)
        pool_err = r.get("error") or ""
        path.append({"layer": "pool", "ok": bool(pool_items), "ms": r.get("ms"),
                     "raw": len(raw_items), "kept": len(pool_items),
                     "tried": r.get("tried"), "error": pool_err or None})

    # ---------- 合并：早停判定 ----------
    merged = rank.fuse(gate, mem_items, pool_items)
    early = bool(merged) and not expand          # 只有"因命中而跳过后续源"才算早停
    if early:
        solved_by = "memory" if mem_items and merged[0].get("source") == "memory" else "pool"
        path.append({"layer": "leyou", "ok": False, "skipped": True, "why": "early_stop 已命中"})
    else:
        # ---------- L3 云智库（兜底） ----------
        ley_asset = next((a for a in reg.by_need(need_type, data) if a.get("kind") == "cli"), None)
        if ley_asset and left() > 1:
            t = time.perf_counter()
            r = source_loader.leyou_search(problem, ley_asset)
            ley_items = r.get("items") or []
            path.append({"layer": "leyou", "ok": bool(ley_items), "ms": r.get("ms"),
                         "reason": r.get("reason"), "error": (r.get("error") or "")[:160] or None})
            if r.get("reason") == "LOGIN_REQUIRED":
                suggestions.append(r.get("next") or "云智库未登录：请人工完成扫码登录后重试")
            merged = rank.fuse(gate, mem_items, pool_items + ley_items)
        else:
            path.append({"layer": "leyou", "ok": False, "skipped": True, "why": "need_type 无覆盖 / 预算不足"})

    if merged:
        best = merged[0]
        cache.put(norm, need_type, {"answer": best.get("answer", ""), "best": best,
                                    "source": best.get("source"), "trust": best.get("trust"),
                                    "version": best.get("version"),
                                    "evidence": best.get("evidence") or []})
        # 首次出现的有效答案进本地记忆（越用越准：后续可被 adopt 晋升）
        added_id = None
        if not any(m.get("q") and m.get("q")[:40] == problem[:40] for m in memory.all_active()):
            added = memory.add(problem, best.get("answer", ""), need_type,
                               source=best.get("source", ""), trust=best.get("trust", ""),
                               version=best.get("version"), freshness=best.get("freshness"),
                               evidence=best.get("evidence") or [], tags=best.get("tags") or [])
            added_id = added.get("id")
        # ask-log 锚点必须包含本次新增的记忆 id —— 否则 feedback 无处挂账（闭环断链）；
        # 若 best 来自公共池，同时记录 pool_id —— 供采纳价值信号上报（对齐池侧 record_adopt 机制）
        feedback.log_ask(query_id, norm, need_type, True,
                         sorted({i.get("source", "") for i in merged if i.get("source")}),
                         used_mids + ([added_id] if added_id else []),
                         pool_id=(best.get("pool_id") or None) if best.get("source") == "pool" else None)
        return _finalize({"problem": problem, "need_type": need_type, "need_type_why": nt_why,
                          "time_hint": norm_info["time_hint"], "items": merged},
                         t0, limit, full, query_id, path, early, suggestions)

    # ---------- L4 拒答 ----------
    suggestions += ["换个说法或 --expand（同时问两库）", "换 --need-type（如 policy / term / caliber）"]
    if pool_err:
        suggestions.append("公共池报错：%s（可稍后重试）" % pool_err[:80])
    feedback.log_ask(query_id, norm, need_type, False, [], used_mids)
    return {"ok": False, "plugin": "leyao-knowledge", "protocol": "1.0",
            "problem": problem, "need_type": need_type, "need_type_why": nt_why,
            "answer": "", "best": {}, "possibilities": [],
            "path": path, "resolved": False, "early_stop": False,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000), "query_id": query_id,
            "has_more": False, "next_offset": None, "total_count": 0,
            "reason": "no_match", "suggestions": suggestions,
            "time_hint": norm_info["time_hint"]}


def check(problem: str) -> dict:
    """口径校验：只认 authority（研究依据：单一权威源 / 知识冲突显式处理）。"""
    out = ask(problem, need_type="caliber", limit=10, expand=True)
    auth = [p for p in out.get("possibilities", []) if p.get("trust") == "authority"]
    out["authority_found"] = bool(auth)
    if not auth:
        out["ok"] = False
        out["reason"] = out.get("reason") or "no_authority"
        out["suggestions"] = (out.get("suggestions") or []) + ["未找到权威口径：请向维护者确认后补入公共池（authority）"]
    return out
