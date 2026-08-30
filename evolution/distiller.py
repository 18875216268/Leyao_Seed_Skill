"""蒸馏器（只读·生产者）：Lane A 注册派生 / Lane B 轨迹经验 / Lane C 库级结构。只读取、只产出，绝不修改受管资产。"""

import hashlib
import os
import re

from core.frontmatter import parse_frontmatter

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def read_skill_md(root, rel_path):
    path = os.path.join(root, rel_path, "SKILL.md")
    if not os.path.exists(path):
        raise FileNotFoundError("SKILL.md not found under %s" % rel_path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _preserve_manual(base, derived):
    out = dict(derived)
    manual = base.get("manual_overrides") or []
    for key in manual:
        if key in base:
            out[key] = base[key]
    out["manual_overrides"] = manual
    return out


def derive_entry(skill_id, root, rel_path=None,
base=None):
    # 始终用正斜杠，保证 registry 跨平台一致（Windows 上 os.path.join 会写反斜杠）。
    rel = rel_path or ("skills/" + skill_id)
    fm = parse_frontmatter(read_skill_md(root, rel))
    mode = fm.get("mode")
    if mode not in ("llm", "native"):
        mode = "native" if os.path.exists(os.path.join(root, rel, "handler.py")) else "llm"
    entry = {
        "id": skill_id,
        "name": fm.get("name") or skill_id,
        "domain": fm.get("domain") or [],
        "triggers": fm.get("triggers") or [],
        "mode": mode,
        "path": rel,
        "priority": fm.get("priority", 0),
        "scope": fm.get("scope") or "*",
        "version_pin": str(fm.get("version") or "0.0.0"),
        "enabled": fm.get("enabled", True),
        "depends": fm.get("depends") or [],
        "negative_triggers": fm.get("negative_triggers") or [],
        "description": fm.get("description") or "",
    }
    if base:
        entry = _preserve_manual(base, entry)
    # 可召回性门禁与 contract.validate 一致：triggers 或 description 有一个即可召回。
    # 仅当两者皆空才要求 AI 从 body 做一次性提取（evidence-anchored）。
    if not entry["triggers"] and not entry["description"]:
        raise ValueError(
            "skill %s: frontmatter has no triggers and no description; "
            "AI one-shot extraction from body required (evidence-anchored)" % skill_id
        )
    return entry


def tokenize(text):
    tokens = []
    for match in TOKEN_RE.findall(str(text).lower()):
        tokens.append(match)
        if len(match) >= 3 and not re.match(r"[a-z0-9_]", match):
            tokens.extend(match[i : i + 2] for i in range(len(match) - 1))
    return tokens


def _intersection(token_lists):
    if not token_lists:
        return []
    common = set(token_lists[0])
    for tokens in token_lists[1:]:
        common &= set(tokens)
    return sorted(common)


def _most_common(token_lists):
    counts = {}
    for tokens in token_lists:
        for token in set(tokens):
            counts[token] = counts.get(token, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _rule_id(kind, pattern, target):
    raw = "%s|%s|%s" % (kind, "|".join(pattern), target)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _pattern_for(groups, limit=3):
    pattern = _intersection([g[0] for g in groups])[:limit]
    if not pattern:
        pattern = _most_common([g[0] for g in groups])[:limit]
    return pattern


def distill_traces(traces, min_support=2):
    override_groups = {}
    failure_groups = {}
    for trace in traces:
        if not isinstance(trace, dict):
            continue
        tokens = tokenize(trace.get("query", ""))
        if not tokens:
            continue
        override = trace.get("user_override")
        if override:
            override_groups.setdefault(override, []).append((tokens, bool(trace.get("success", True))))
        elif trace.get("success") is False and trace.get("routed_skill"):
            failure_groups.setdefault(trace["routed_skill"], []).append((tokens, False))

    rules = []
    for target, group in override_groups.items():
        if len(group) < min_support:
            continue
        pattern = _pattern_for(group)
        if not pattern:
            continue
        success_rate = sum(1 for g in group if g[1]) / len(group)
        rules.append(
            {
                "id": _rule_id("route", pattern, target),
                "kind": "route",
                "pattern": pattern,
                "target": target,
                "support": len(group),
                "success_rate": round(success_rate, 4),
                "state": "candidate",
                "source": "laneB",
            }
        )
    for target, group in failure_groups.items():
        if len(group) < min_support:
            continue
        pattern = _pattern_for(group)
        if not pattern:
            continue
        rules.append(
            {
                "id": _rule_id("avoid", pattern, target),
                "kind": "avoid",
                "pattern": pattern,
                "target": target,
                "support": len(group),
                "success_rate": 0.0,
                "state": "candidate",
                "source": "laneB",
            }
        )
    return rules


def distill_library(entries):
    by_trigger = {}
    for e in entries:
        for trigger in e.get("triggers", []):
            by_trigger.setdefault(trigger, []).append(e["id"])
    overlaps = [{"trigger": t, "skills": ids} for t, ids in sorted(by_trigger.items()) if len(ids) > 1]

    conflicts = []
    for overlap in overlaps:
        members = [e for e in entries if e["id"] in overlap["skills"]]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a.get("priority") == b.get("priority") and a.get("scope") == b.get("scope"):
                    conflicts.append(
                        {"skills": [a["id"], b["id"]], "trigger": overlap["trigger"], "reason": "same priority and scope"}
                    )

    sets = {e["id"]: set(e.get("triggers", [])) for e in entries}
    redundancies = []
    for sid, sset in sets.items():
        if not sset:
            continue
        for other, oset in sets.items():
            if sid != other and sset < oset:
                redundancies.append({"subset": sid, "superset": other})
    return {
        "skill_count": len(entries),
        "overlaps": overlaps,
        "conflicts": conflicts,
        "redundancies": redundancies,
    }
