#!/usr/bin/env python3
"""反思（reflection）：把使用轨迹变成可执行改进建议（带着证据）。

依据：Generative Agents 的反思机制（触发生成高层洞察 + **证据引用**）；
本实现为**规则式统计版**（零 LLM、零依赖）：未命中热点 / 高否决主题 / 待晋升候选 / 陈旧口径，
每条洞察都带 evidence 指针（query_id / memory id）；同时执行**冷存**（低价值记忆只冷存、不物理删除）。
有 LLM 的调用方可在此基础上再做深度提炼（本模块不假装自己是 LLM ✗）。
"""
from __future__ import annotations

from collections import Counter

from common import FEEDBACK_F, REFLECT_F, age_hours, append_jsonl, now_iso, read_jsonl
import memory


def reflect(window: int = 100) -> dict:
    """生成洞察（每条带 evidence）。window = 参与统计的最近轨迹条数。"""
    asks = [r for r in read_jsonl(FEEDBACK_F) if r.get("kind") == "ask"][-window:]
    verdicts = [r for r in read_jsonl(FEEDBACK_F) if r.get("kind") == "verdict"][-window:]
    mems = memory.all_active()

    insights = []

    misses = Counter(r.get("norm", "") for r in asks if not r.get("resolved"))
    for norm, n in misses.most_common(5):
        if n >= 2:
            ev = [r["query_id"] for r in asks if not r.get("resolved") and r.get("norm") == norm][:3]
            insights.append({"insight": "「%s」反复未命中（%d 次）→ 建议补入公共池或补别名" % (norm[:40], n),
                             "kind": "miss_hotspot", "evidence": ev})

    rejects = Counter(r.get("norm", "") for r in verdicts if r.get("verdict") != "adopt")
    for norm, n in rejects.most_common(3):
        insights.append({"insight": "「%s」被否决 %d 次 → 检查口径是否陈旧或需澄清" % (norm[:40], n),
                         "kind": "reject_hotspot",
                         "evidence": [r["query_id"] for r in verdicts if r.get("verdict") != "adopt" and r.get("norm") == norm][:3]})

    promote = [m for m in mems if m.get("tier") == "semantic" and int(m.get("adopt", 0)) >= memory.PROMOTE_ADOPT]
    if promote:
        for m in promote:
            memory.mark_pool_candidate(m["id"])   # 标记为「可提交公共池」候选（真正提交仍需维护者显式操作）
        insights.append({"insight": "%d 条本地确认记忆已达「可提交公共池」条件（adopt≥%d）→ 已标记为候选，等待显式提交"
                                    % (len(promote), memory.PROMOTE_ADOPT),
                         "kind": "promote_candidates",
                         "evidence": [m["id"] for m in promote[:5]]})

    stale = [m for m in mems if m.get("need_type") in ("caliber", "policy")
             and age_hours(m.get("created_at", "")) > 24 * 90]
    if stale:
        insights.append({"insight": "%d 条口径/制度类记忆超过 90 天未刷新 → 建议对照权威源复核"
                                    % len(stale), "kind": "stale_check",
                         "evidence": [m["id"] for m in stale[:5]]})

    cold = memory.decay_candidates()     # 遗忘：低价值 + 长期未访问 → 冷存（不物理删除）
    out = {"ok": True, "window": window, "asks": len(asks), "verdicts": len(verdicts),
           "cold_marked": cold, "insights": insights, "generated_at": now_iso()}
    append_jsonl(REFLECT_F, out)
    return out
