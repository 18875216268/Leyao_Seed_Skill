"""路由编排入口：两段式召回 → 裁决 → 四策略执行。"""

from core import executor
from core.arbitrator import arbitrate
from core.resolver import resolve

STRATEGIES = ("direct", "cascade", "pipeline", "parallel")


def route(entries, query, strategy="direct", experience=None, root=None, fallback=None):
    if strategy not in STRATEGIES:
        raise ValueError("strategy must be one of %s" % list(STRATEGIES))
    ranked = resolve(entries, query, experience)
    kind, picked = arbitrate(ranked, allow_parallel=(strategy == "parallel"))
    if kind == "fallback":
        if fallback:
            return {"routed": "fallback", "result": fallback(query)}
        return {"routed": "fallback", "mode": "llm", "reason": "no skill matched", "query": query}
    if strategy == "cascade":
        return {"routed": "cascade", "result": executor.cascade(ranked, query, root=root)}
    if strategy == "pipeline":
        return {"routed": "pipeline", "result": executor.pipeline(picked, query, root=root)}
    if strategy == "parallel" and kind == "parallel":
        return {"routed": "parallel", "result": executor.parallel(picked, query, root=root)}
    return {"routed": "direct", "result": executor.direct(picked, query, root=root)}
