"""路由编排入口：两段式召回 → 裁决 → 四策略执行。"""

from core import executor
from core.arbitrator import arbitrate
from core.resolver import resolve

STRATEGIES = ("direct", "cascade", "pipeline", "parallel")


def route(entries, query, strategy="direct", experience=None, usage=None, root=None, fallback=None,
          allow_native=True, trace_id=None):
    if strategy not in STRATEGIES:
        raise ValueError("strategy must be one of %s" % list(STRATEGIES))
    ranked = resolve(entries, query, experience, usage)
    kind, picked = arbitrate(ranked, allow_parallel=(strategy == "parallel"))
    if kind == "fallback":
        if fallback:
            return {"routed": "fallback", "result": fallback(query)}
        return {"routed": "fallback", "mode": "llm", "reason": "no skill matched", "query": query}
    # trace_id 透传到执行层：否则路由记一条、执行各记一条，链路是断的。
    if strategy == "cascade":
        return {"routed": "cascade",
                "result": executor.cascade(ranked, query, root=root,
                                           allow_native=allow_native, trace_id=trace_id)}
    if strategy == "pipeline":
        return {"routed": "pipeline",
                "result": executor.pipeline(picked, query, root=root,
                                            allow_native=allow_native, trace_id=trace_id)}
    if strategy == "parallel":
        return {"routed": "parallel",
                "result": executor.parallel(picked, query, root=root,
                                            allow_native=allow_native, trace_id=trace_id)}
    return {"routed": "direct",
            "result": executor.direct(picked, query, root=root,
                                      allow_native=allow_native, trace_id=trace_id)}
