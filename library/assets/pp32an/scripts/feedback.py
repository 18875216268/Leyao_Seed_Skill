#!/usr/bin/env python3
"""反馈闭环（越用越聪明的驱动件）：

- 每次 ask 写一条 ask-log（query_id → 命中的本地记忆 id / 源 / 是否解决）；
- 调用方一行命令回写采纳/否决：`hub.py feedback --query-id <id> --verdict adopt|reject [--note …]`；
- 采纳累积 → 本地记忆晋升（candidate→semantic）；否决累积 → 存疑；结果写 feedback.jsonl（可审计）。
"""
from __future__ import annotations

from common import FEEDBACK_F, append_jsonl, load_config, now_iso, read_jsonl
import cache
import memory
import registry as reg
from sources import pool


def log_ask(query_id: str, norm: str, need_type: str, resolved: bool,
            sources: list, memory_ids: list, pool_id: str | None = None) -> None:
    append_jsonl(FEEDBACK_F, {"kind": "ask", "query_id": query_id, "at": now_iso(),
                              "norm": norm, "need_type": need_type, "resolved": resolved,
                              "sources": sources, "memory_ids": memory_ids, "pool_id": pool_id})


def _report_adopt(pool_id: str) -> dict:
    """采纳价值信号上报公共池（对齐池侧 record_adopt：需 token、短超时、失败静默）。

    写令牌只在本地配置（数据区 `config.local.json` → `pool.write_token`）；未配置不见网（静默）。
    """
    if not pool_id:
        return {"ok": False, "error": "MISSING_ID"}
    token = str((load_config().get("pool") or {}).get("write_token") or "").strip()
    if not token:
        return {"ok": False, "error": "NO_WRITE_TOKEN"}
    try:
        data = reg.load()
        asset = next((a for a in data["ordered_assets"] if a.get("kind") == "pool"), None)
        if not asset or not asset.get("report_adopt", True):
            return {"ok": False, "error": "DISABLED"}
        r = pool.record_adopt(asset.get("endpoint", ""), pool_id, token=token,
                              timeout=float(asset.get("write_timeout_s") or 5.0))
        return {"ok": bool(r.get("ok")), "pool_id": pool_id, "error": r.get("error")}
    except Exception as exc:  # noqa: BLE001 —— 价值信号失败绝不阻塞反馈主流程
        return {"ok": False, "pool_id": pool_id,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:80])}


def submit(query_id: str, verdict: str, note: str = "") -> dict:
    """回写反馈：更新 ask-log 关联的本地记忆计数；采纳且 best 来自公共池时上报价值信号。

    无本地记忆时仅落审计（外部源无法本地记账）；上报失败**静默**（不阻塞主流程；
    `registry.report_adopt=false` 可整体关闭）。
    """
    asks = [r for r in read_jsonl(FEEDBACK_F) if r.get("kind") == "ask" and r.get("query_id") == query_id]
    if not asks:
        return {"ok": False, "reason": "UNKNOWN_QUERY_ID", "detail": "未找到该 query_id 的 ask-log"}
    ask = asks[-1]
    touched = []
    for mid in ask.get("memory_ids") or []:
        got = memory.record_feedback(mid, verdict, note)
        if got:
            touched.append({"id": mid, "adopt": got.get("adopt"), "fail": got.get("fail"),
                            "tier": got.get("tier")})
    reported = _report_adopt(ask.get("pool_id")) if verdict == "adopt" and ask.get("pool_id") else None
    invalidated = None
    if verdict != "adopt" and ask.get("norm"):
        # 拒答/纠错 → 缓存失效（防"错答被语义缓存复利"；依据：Tian Pan《Cache Invalidation for AI》2026）
        invalidated = cache.invalidate(ask["norm"], ask.get("need_type") or "",
                                       reg.semantic_threshold())
    append_jsonl(FEEDBACK_F, {"kind": "verdict", "query_id": query_id, "at": now_iso(),
                              "verdict": verdict, "note": str(note)[:200],
                              "sources": ask.get("sources"), "memory": touched,
                              "adopt_reported": reported, "cache_invalidated": invalidated})
    out = {"ok": True, "query_id": query_id, "verdict": verdict, "memory_updated": touched,
           "note": "无本地记忆命中（回答来自外部源）——已仅记审计；若该答案值得复用，可让调用方写入本地记忆" if not touched else ""}
    if reported is not None:
        out["adopt_reported"] = reported
    if invalidated is not None:
        out["cache_invalidated"] = invalidated
    return out


def stats() -> dict:
    rows = read_jsonl(FEEDBACK_F)
    asks = [r for r in rows if r.get("kind") == "ask"]
    verdicts = [r for r in rows if r.get("kind") == "verdict"]
    adopt = sum(1 for r in verdicts if r.get("verdict") == "adopt")
    reject = sum(1 for r in verdicts if r.get("verdict") != "adopt")
    hit = sum(1 for r in asks if r.get("resolved"))
    return {"asks": len(asks), "resolved": hit,
            "resolve_rate": round(hit / len(asks), 4) if asks else 0.0,
            "verdicts": len(verdicts), "adopt": adopt, "reject": reject,
            "file": str(FEEDBACK_F)}
