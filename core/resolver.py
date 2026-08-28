"""两段式路由：廉价召回只读元数据，全量 skill 描述永不每 query 入 ctx。"""


def scope_specificity(scope):
    parts = [p for p in str(scope or "").split(".") if p]
    concrete = sum(1 for p in parts if p != "*")
    return concrete * 10 + len(parts)


def recall(entries, query):
    q = str(query).lower()
    cands = []
    for e in entries:
        if not e.get("enabled", True):
            continue
        if any(t.lower() in q for t in e.get("negative_triggers", [])):
            continue
        trigger_hits = [t for t in e.get("triggers", []) if t.lower() in q]
        domain_hits = [d for d in e.get("domain", []) if d.lower() in q]
        if not trigger_hits and not domain_hits:
            continue
        cands.append({"entry": e, "trigger_hits": trigger_hits, "domain_hits": domain_hits})
    return cands


def experience_boost(rules, entry, q):
    boost = 0.0
    for r in rules or []:
        if r.get("target") != entry["id"]:
            continue
        state = r.get("state")
        if state == "candidate":
            continue
        tokens = r.get("pattern", [])
        if not tokens or not all(str(t).lower() in q for t in tokens):
            continue
        weight = 1.5 if state == "locked" else 1.0
        boost += -weight if r.get("kind") == "avoid" else weight
    return boost


def sort_key(item):
    e = item["entry"]
    return (item["score"], e.get("priority", 0), scope_specificity(e.get("scope")), e["id"])


def rank(cands, query, experience=None):
    q = str(query).lower()
    scored = []
    for c in cands:
        e = c["entry"]
        score = len(c["trigger_hits"]) * 2.0 + len(c["domain_hits"])
        score += e.get("priority", 0) * 0.1
        score += scope_specificity(e.get("scope")) * 0.05
        score += experience_boost(experience, e, q)
        scored.append({"entry": e, "trigger_hits": c["trigger_hits"], "domain_hits": c["domain_hits"], "score": round(score, 4)})
    scored.sort(key=sort_key, reverse=True)
    return scored


def resolve(entries, query, experience=None):
    return rank(recall(entries, query), query, experience)
