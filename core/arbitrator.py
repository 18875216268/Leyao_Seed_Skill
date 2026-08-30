"""裁决：排序已编码 priority↓ 与 scope 窄胜宽，此处只定"谁赢 / 是否并行 / 是否兜底"。"""

from core.resolver import scope_specificity


def arbitrate(ranked, allow_parallel=False, max_parallel=3):
    if not ranked:
        return ("fallback", [])
    top = ranked[0]
    if allow_parallel:
        tied = [
            r
            for r in ranked
            if r["score"] == top["score"]
            and r["entry"].get("priority", 0) == top["entry"].get("priority", 0)
            and scope_specificity(r["entry"].get("scope")) == scope_specificity(top["entry"].get("scope"))
        ]
        if len(tied) > 1:
            return ("parallel", tied[:max_parallel])
    return ("direct", [top])
