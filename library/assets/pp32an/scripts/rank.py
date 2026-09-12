#!/usr/bin/env python3
"""精排与融合（研究依据：Generative Agents 三因子检索）：

score = α·recency + α·importance + α·relevance（三分量 min-max 归一化后等权，α=1）
- recency：0.995^小时 指数衰减（last_access_at）
- importance：1–10 → /10
- relevance：查询 token 与记忆（q+a）的相似度（common.similarity）

融合：外部源（公共池/云智库）结果与本地记忆统一为"可能性"列表；
按 (trust 序, score) 排序；同题多源冲突**显式并列**（不静默择一）。
"""
from __future__ import annotations

from common import age_hours, norm_text, similarity

TRUST_ORDER = {"authority": 0, "reference": 1, "candidate": 2, "": 3}


def _minmax(values: list) -> list:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def score_memories(mems: list, norm_query: str) -> list:
    """给本地记忆打分（返回带 score 的副本，按 score 降序）。"""
    if not mems:
        return []
    rec = [0.995 ** age_hours(m.get("last_access_at") or m.get("created_at", "")) for m in mems]
    imp = [float(m.get("importance", 5)) / 10.0 for m in mems]
    rel = [similarity(norm_query, "%s %s" % (m.get("q", ""), m.get("a", ""))) for m in mems]
    nrec, nimp, nrel = _minmax(rec), _minmax(imp), _minmax(rel)
    out = []
    for i, m in enumerate(mems):
        item = dict(m)
        item["score"] = round(nrec[i] + nimp[i] + nrel[i], 4)
        # relevance_raw：**未归一化**的原始相似度——入选门槛必须看它
        # （min-max 在"全体都≈0"时会把所有项归成 1.0，照归一化值过滤会放过不相关记忆）
        item["score_parts"] = {"recency": round(nrec[i], 3), "importance": round(nimp[i], 3),
                               "relevance": round(nrel[i], 3),
                               "relevance_raw": round(rel[i], 4)}
        out.append(item)
    out.sort(key=lambda x: -x["score"])
    return out


REMOTE_RELEVANCE_FLOOR = 0.15      # 远程命中门槛：低于此视为"不相关命中"（不得阻止兜底）


def relevance_of(terms: list, item: dict) -> float:
    """检索词 × 候选 的相关性（0..1）——**标题命中优先**。

    实测教训（2026-09-12）：用"整句问题 vs 长文正文"算相似度必然低分，会把精确条目也滤掉 ✗；
    改为按检索词（来自 query_norm.search_terms）逐词判定：词在标题 → 1.0 · 在正文 → 0.65 ·
    否则取与标题/正文的相似度折扣。
    """
    title, ans = norm_text(item.get("title") or ""), norm_text(str(item.get("answer") or "")[:300])
    best = 0.0
    for t in terms or []:
        nt = norm_text(t)
        if not nt:
            continue
        if nt in title:
            best = max(best, 1.0)
        elif nt in ans:
            best = max(best, 0.65)
        else:
            best = max(best, 0.5 * similarity(nt, title), 0.35 * similarity(nt, ans))
    return round(best, 4)


def fuse(terms: list, local_items: list, remote_items: list) -> list:
    """本地 + 远程统一排序：**trust 优先，同级按相关性**（实测教训：只按 trust+quality 会让
    含关键词的长文压过精确条目 ✗）——远程分 = 0.7×相关性 + 0.3×源置信；本地分归一到 [0,1]。

    冲突标注：同主题存在 ≥2 个不同内容的项 → 打 conflict 标记（不静默择一）。
    """
    merged = []
    for it in local_items:
        item = dict(it)
        item["score"] = round(min(1.0, float(it.get("score", 0)) / 3.0), 4)
        merged.append(item)
    for it in remote_items:
        item = dict(it)
        rel = relevance_of(terms, item)
        item["score"] = round(0.7 * rel + 0.3 * float(it.get("confidence") or 0), 4)
        item["score_parts"] = {"relevance_raw": round(rel, 4)}
        merged.append(item)
    merged.sort(key=lambda x: (TRUST_ORDER.get(x.get("trust", ""), 3), -float(x.get("score", 0))))
    seen, conflicts = {}, []
    for it in merged:
        content = str(it.get("answer") or "")[:80]
        title = str(it.get("title") or it.get("q") or "")[:40]
        k = title or content
        if k and k in seen and content and content != seen[k]:
            conflicts.append(k)
        seen.setdefault(k, content)
    for it in merged:
        k = str(it.get("title") or it.get("q") or "")[:40]
        if k in conflicts:
            it["conflict"] = "同一主题存在多个不同来源口径（已并列，未静默择一）"
    return merged
