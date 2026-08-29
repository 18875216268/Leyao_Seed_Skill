"""四型路由策略 + llm/native 双模式调用。llm 模式框架不代执行，只交接 SKILL.md 路径。"""

import importlib.util
import os

from core.contract import validate_native_module


def skill_md_path(entry, root):
    return os.path.join(root, entry["path"], "SKILL.md")


def load_native(entry, root, allow_native=True):
    if not allow_native:
        # 原生 skill = 任意本地代码执行（exec_module）。默认信任但可被 Suite 显式关闭：
        # 仅注册你信任的 skill；关闭后匹配到 native skill 直接拒绝，不静默执行未知代码。
        raise RuntimeError("native execution disabled for %s; enable allow_native to run handler.py" % entry["id"])
    path = os.path.join(root, entry["path"], "handler.py")
    if not os.path.exists(path):
        raise RuntimeError("native skill %s missing handler.py under %s" % (entry["id"], entry["path"]))
    spec = importlib.util.spec_from_file_location("skill_handler_%s" % entry["id"], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    validate_native_module(mod, entry["id"])
    return mod


def invoke_one(entry, query, input_data=None, check_can=True, root=None, allow_native=True):
    root = root or os.getcwd()
    if entry.get("mode") == "llm":
        return {
            "mode": "llm",
            "skill_id": entry["id"],
            "path": entry["path"],
            "skill_md": skill_md_path(entry, root),
            "query": query,
        }
    mod = load_native(entry, root, allow_native=allow_native)
    if check_can and mod.can_handle(query) <= 0:
        raise RuntimeError("skill %s cannot handle" % entry["id"])
    state = mod.health()
    if isinstance(state, dict) and state.get("ok") is False:
        raise RuntimeError("skill %s unhealthy" % entry["id"])
    data = input_data if input_data is not None else {"query": query}
    return mod.invoke(data)


def order_by_depends(entries):
    by_id = {e["id"]: e for e in entries}
    ordered, done, stack = [], set(), set()

    def visit(e):
        if e["id"] in stack:
            raise RuntimeError("circular depends at %s" % e["id"])
        if e["id"] in done:
            return
        stack.add(e["id"])
        for dep in e.get("depends", []):
            if dep in by_id:
                visit(by_id[dep])
        stack.discard(e["id"])
        done.add(e["id"])
        ordered.append(e)

    for e in entries:
        visit(e)
    return ordered


def direct(picked, query, root=None, allow_native=True):
    return invoke_one(picked[0]["entry"], query, root=root, allow_native=allow_native)


def cascade(ranked, query, max_try=3, root=None, allow_native=True):
    last = None
    for item in ranked[:max_try]:
        try:
            return invoke_one(item["entry"], query, root=root, allow_native=allow_native)
        except Exception as ex:
            last = ex
    raise RuntimeError("cascade exhausted: %s" % last)


def pipeline(picked, query, root=None, allow_native=True):
    ordered = order_by_depends([item["entry"] for item in picked])
    data = {"query": query}
    for entry in ordered:
        data = invoke_one(entry, query, data, check_can=False, root=root, allow_native=allow_native)
    return data


def parallel(picked, query, root=None, allow_native=True):
    out = {}
    for item in picked:
        entry = item["entry"]
        try:
            out[entry["id"]] = invoke_one(entry, query, root=root, allow_native=allow_native)
        except Exception as ex:
            out[entry["id"]] = {"error": True, "reason": str(ex)}
    return out
