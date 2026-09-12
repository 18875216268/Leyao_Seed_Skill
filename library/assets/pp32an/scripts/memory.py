#!/usr/bin/env python3
"""本地记忆（越用越聪明的底座）——对象结构对齐 Generative Agents：

{q, a, need_type, created_at, last_access_at, access_count, importance(1–10),
 source, trust, version, freshness, adopt, fail, evidence[], tags[], tier, status}

- importance 规则式默认分（口径/制度高、搜索低），可被外部显式覆盖；
- tier：candidate（默认）→ semantic（adopt 达阈值晋升）→ pool-candidate（可显式提交公共池）；
- status：active / cold（低价值冷存，**不物理删除**）；
- 存取：JSONL 追加 + 内存扫描（小规模够用；FTS5 属可选增强，未实现）。
"""
from __future__ import annotations

from common import MEMORY_F, append_jsonl, now_iso, read_jsonl, sha1

IMPORTANCE_DEFAULT = {"caliber": 8, "policy": 8, "term": 6, "course": 6, "search": 4}
PROMOTE_ADOPT = 3          # 晋升阈值：adopt 达 3 次且无 fail → candidate→semantic


def add(q: str, a: str, need_type: str, *, source: str = "", trust: str = "",
        version=None, freshness=None, evidence=None, tags=None, importance=None,
        tier: str = "candidate") -> dict:
    obj = {
        "id": "m_" + sha1("%s|%s" % (need_type, q))[:10],
        "q": str(q)[:300], "a": str(a)[:1200], "need_type": need_type,
        "created_at": now_iso(), "last_access_at": now_iso(), "access_count": 0,
        "importance": int(importance or IMPORTANCE_DEFAULT.get(need_type, 5)),
        "source": source, "trust": trust, "version": version, "freshness": freshness,
        "adopt": 0, "fail": 0, "evidence": list(evidence or []), "tags": list(tags or []),
        "tier": tier, "status": "active",
    }
    append_jsonl(MEMORY_F, obj)
    return obj


def _latest() -> dict:
    """同 id 取最后一条（JSONL 追加式更新）——遍历的唯一实现。"""
    latest = {}
    for row in read_jsonl(MEMORY_F):
        latest[row.get("id")] = row
    return latest


def all_active() -> list:
    return [r for r in _latest().values() if r.get("status") == "active"]


def get(mid: str) -> dict | None:
    for row in reversed(read_jsonl(MEMORY_F)):
        if row.get("id") == mid:
            return row
    return None


def _update(mid: str, **fields) -> dict | None:
    base = get(mid)
    if not base:
        return None
    base.update(fields)
    append_jsonl(MEMORY_F, base)
    return base


def touch(mid: str) -> dict | None:
    base = get(mid)
    if not base:
        return None
    return _update(mid, last_access_at=now_iso(), access_count=int(base.get("access_count", 0)) + 1)


def record_feedback(mid: str, verdict: str, note: str = "") -> dict | None:
    """采纳 → adopt+1（达阈值自动晋升）；否决 → fail+1（连续否决转存疑）。"""
    base = get(mid)
    if not base:
        return None
    if verdict == "adopt":
        base["adopt"] = int(base.get("adopt", 0)) + 1
        if base["adopt"] >= PROMOTE_ADOPT and int(base.get("fail", 0)) == 0 and base.get("tier") == "candidate":
            base["tier"] = "semantic"          # 晋升：本地候选 → 本地确认
    else:
        base["fail"] = int(base.get("fail", 0)) + 1
    if note:
        base["note"] = str(note)[:200]
    base["last_access_at"] = now_iso()
    append_jsonl(MEMORY_F, base)
    return base


def mark_pool_candidate(mid: str) -> dict | None:
    """显式标记为「可提交公共池」候选（真正提交由维护者显式操作，默认不自动）。"""
    return _update(mid, tier="pool-candidate")


def cold(mid: str) -> dict | None:
    """冷存（低价值）：不物理删除。"""
    return _update(mid, status="cold")


def stats() -> dict:
    rows = read_jsonl(MEMORY_F)
    latest = {r.get("id"): r for r in rows}          # 同 id 取最后
    active = [r for r in latest.values() if r.get("status") == "active"]
    return {"entries": len(rows), "active": len(active),
            "semantic": sum(1 for r in active if r.get("tier") == "semantic"),
            "pool_candidate": sum(1 for r in active if r.get("tier") == "pool-candidate"),
            "cold": sum(1 for r in latest.values() if r.get("status") == "cold"),
            "adopt_total": sum(int(r.get("adopt", 0)) for r in active),
            "fail_total": sum(int(r.get("fail", 0)) for r in active),
            "file": str(MEMORY_F)}


def decay_candidates(min_importance: int = 4, max_age_days: float = 90.0) -> int:
    """低价值冷存：importance 低 + 长期未访问 → cold（返回处理数）。"""
    from common import age_hours
    n = 0
    for r in all_active():
        if int(r.get("importance", 5)) <= min_importance and \
                age_hours(r.get("last_access_at", "")) > max_age_days * 24:
            cold(r["id"])
            n += 1
    return n
