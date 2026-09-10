#!/usr/bin/env python3
"""自我进化层 · 变环（蒸馏，只读）：轨迹 → route/avoid 候选；库体检/主动探索 → 诊断。

只读取、只产出，绝不修改任何资产。墓碑指纹在此拦截（糟粕不复活）。
"""
from __future__ import annotations

import re
import sys

import store

sys.path.insert(0, str(store.ROOT / "library"))
import engine  # noqa: E402  （复用资产层唯一的路由契约校验；只读，不写）

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> list:
    tokens = []
    for m in TOKEN_RE.findall(str(text).lower()):
        tokens.append(m)
        if re.match(r"[\u4e00-\u9fff]", m) and len(m) >= 2:
            tokens.extend(m[i:i + 2] for i in range(len(m) - 1))
    return tokens


def _pattern(groups, limit: int = 3) -> list:
    sets = [set(g[0]) for g in groups]
    common = set.intersection(*sets) if sets else set()
    if not common:
        counts = {}
        for s in sets:
            for t in s:
                counts[t] = counts.get(t, 0) + 1
        common = set(counts)
    ranked = sorted(common, key=lambda t: (-sum(1 for s in sets if t in s), t))
    return ranked[:limit]


def distill_traces(items: list, min_support: int = 2) -> list:
    """Lane B：用户纠正（最强信号）→route 候选；同路由失败→avoid 候选。墓碑拦截。"""
    exp = store.experience()
    override_groups, failure_groups = {}, {}
    for t in items:
        if not isinstance(t, dict):
            continue
        tokens = tokenize(t.get("task", ""))
        if not tokens:
            continue
        if t.get("user_override"):
            override_groups.setdefault(t["user_override"], []).append((tokens, t.get("outcome") == "success"))
        elif t.get("outcome") == "fail" and t.get("routed_to"):
            failure_groups.setdefault(t["routed_to"], []).append((tokens, t.get("failure_reason", "")))

    rules = []
    for target, group in override_groups.items():
        if len(group) < min_support:
            continue
        pattern = _pattern(group)
        rid = store.rule_id("route", pattern, target)
        if store.tombstoned(rid, exp):
            continue
        rules.append({
            "id": rid, "kind": "route", "pattern": pattern, "target": target,
            "support": len(group),
            "success_rate": round(sum(1 for g in group if g[1]) / len(group), 4),
            "state": "candidate", "source": "trace",
            "summary": "任务含「%s」→ 优先走 %s" % ("/".join(pattern), target),
            "created_at": store.now(), "hits": 0, "misses": 0, "observed": 0,
        })
    for target, group in failure_groups.items():
        if len(group) < min_support:
            continue
        pattern = _pattern(group)
        rid = store.rule_id("avoid", pattern, target)
        if store.tombstoned(rid, exp):
            continue
        reasons = sorted({g[1] for g in group if g[1]})[:2]
        rules.append({
            "id": rid, "kind": "avoid", "pattern": pattern, "target": target,
            "support": len(group), "success_rate": 0.0,
            "state": "candidate", "source": "trace",
            "summary": "「%s」场景下 %s 曾失败（%s），使用前先核验" % ("/".join(pattern), target, "；".join(reasons) or "原因见轨迹"),
            "created_at": store.now(), "hits": 0, "misses": 0, "observed": 0,
        })
    return rules


def check_library() -> list:
    """Lane C：库体检——复用资产层引擎的路由契约校验（唯一实现，不另写一套路径检查）。

    与路由契约对齐：命中节点后要进其挂载目录读 SKILL.md／README.md，
    因此挂载目录必须有入口文档，否则 AI 进得去却用不了。
    """
    return engine.validate(engine.load(), store.ROOT)


def explore_signal(items: list, min_support: int = 2):
    """主动探索信号（防局部最优）：命中率最低的路由目标。

    条件实时计算、不入库——信号持续存在则持续提示，资产改善即自动消解；
    动作由 AI 依信号审查资产后构造变异提案。
    """
    stats = {}
    for t in items:
        if not isinstance(t, dict) or not t.get("routed_to"):
            continue
        s = stats.setdefault(t["routed_to"], {"n": 0, "ok": 0})
        s["n"] += 1
        s["ok"] += 1 if t.get("outcome") == "success" else 0
    worst = None
    for target, s in stats.items():
        if s["n"] < max(3, min_support):
            continue
        rate = s["ok"] / s["n"]
        if worst is None or rate < worst[1]:
            worst = (target, rate, s["n"])
    if worst is None or worst[1] >= 0.6:
        return None
    return {"target": worst[0], "success_rate": round(worst[1], 4), "n": worst[2],
            "summary": "命中率仅 %.0f%%（%d 次），建议审查该资产" % (worst[1] * 100, worst[2])}
