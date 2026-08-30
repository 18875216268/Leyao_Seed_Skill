"""四型路由策略 + llm/native 双模式调用。llm 模式框架不代执行，只交接 SKILL.md 路径。"""

import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

from core.contract import validate_native_module

# parallel 默认参数取业界通用值（并发上限 5、单调用超时 30s）。
DEFAULT_MAX_WORKERS = 5
DEFAULT_CALL_TIMEOUT = 30.0


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


def _audit_invoke(root, skill_id, mode, trace_id, started, outcome):
    """执行结果落审计。

    可观测不得拖垮业务：任何异常一律吞掉。也不记录 result 内容——
    skill 的返回可能含业务敏感数据，审计只关心"跑没跑成、花了多久"。
    """
    try:
        from core.audit import record
        fields = dict(outcome)
        fields.update({"skill": skill_id, "mode": mode or "llm"})
        record("skill.invoke", root=root, trace_id=trace_id,
               duration_ms=(time.monotonic() - started) * 1000, **fields)
    except Exception:
        pass


def invoke_one(entry, query, input_data=None, check_can=True, root=None, allow_native=True,
               trace_id=None):
    """执行单个 skill。trace_id 用于把本次执行挂到路由那条链路上。"""
    root = root or os.getcwd()
    started = time.monotonic()
    outcome = {"status": "ok"}
    try:
        if entry.get("mode") == "llm":
            # llm 模式框架不代执行，只把 SKILL.md 路径交接给 agent——
            # 真正的执行在框架之外，审计只能记到"已交接"这一层。
            outcome["handoff"] = "llm"
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
    except Exception as ex:
        outcome = {"status": "error", "reason": str(ex)}
        raise
    finally:
        _audit_invoke(root, entry["id"], entry.get("mode"), trace_id, started, outcome)


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


def direct(picked, query, root=None, allow_native=True, trace_id=None):
    return invoke_one(picked[0]["entry"], query, root=root, allow_native=allow_native,
                      trace_id=trace_id)


def cascade(ranked, query, max_try=3, root=None, allow_native=True, trace_id=None):
    last = None
    for item in ranked[:max_try]:
        try:
            return invoke_one(item["entry"], query, root=root, allow_native=allow_native,
                              trace_id=trace_id)
        except Exception as ex:
            last = ex
    raise RuntimeError("cascade exhausted: %s" % last)


def pipeline(picked, query, root=None, allow_native=True, trace_id=None):
    ordered = order_by_depends([item["entry"] for item in picked])
    data = {"query": query}
    for entry in ordered:
        data = invoke_one(entry, query, data, check_can=False, root=root,
                          allow_native=allow_native, trace_id=trace_id)
    return data


def _invoke_catch(entry, query, root, allow_native, trace_id=None):
    """worker 侧统一兜异常：异常不冒泡到 future，由主线程统一处理超时。"""
    try:
        return invoke_one(entry, query, root=root, allow_native=allow_native, trace_id=trace_id)
    except Exception as ex:
        return {"error": True, "reason": str(ex)}


def parallel(picked, query, root=None, allow_native=True, trace_id=None,
             max_workers=DEFAULT_MAX_WORKERS, timeout=DEFAULT_CALL_TIMEOUT, overall_timeout=None):
    """真并发执行多个 skill。

    此前这里是 for 循环串行，是业界最常见的并行失效原因——模型/调用方看到
    N 个并行请求，服务端却在逐个 await，延迟收益为零。现改为有界线程池并发。

    两条硬约束（业界生产实践）：
    - 每调用超时：防止单个慢依赖拖垮整批。
    - 整体时间预算：防止 N 个调用各自超时叠加成 N × timeout 的长尾。

    结果按请求顺序返回（dict 按 picked 顺序写入），不按完成顺序——
    乱序回填会破坏调用方的上下文一致性。

    已知限制：Python 无法强制杀死仍在运行的线程，超时只是"不再等待"。
    卡死的 worker 会在后台继续跑完；本函数用 shutdown(wait=False) 避免等待它们，
    进程退出时解释器仍会 join，因此 native skill 自身应实现可中断逻辑。
    """
    if not picked:
        return {}
    workers = max(1, min(int(max_workers or 1), len(picked)))
    budget = float(overall_timeout) if overall_timeout is not None else float(timeout) * 1.5
    started = time.monotonic()
    deadline = started + budget

    out = {}
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="srs-parallel")
    try:
        futures = [
            pool.submit(_invoke_catch, item["entry"], query, root, allow_native, trace_id)
            for item in picked
        ]
        for item, fut in zip(picked, futures):
            sid = item["entry"]["id"]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fut.cancel()
                out[sid] = {"error": True, "reason": "overall deadline exceeded (%.1fs)" % budget}
                # invoke_one 内部的审计记不到这类失败：worker 仍在跑，是主线程放弃等待。
                _audit_invoke(root, sid, item["entry"].get("mode"), trace_id, started,
                              {"status": "error", "reason": "overall deadline exceeded"})
                continue
            try:
                out[sid] = fut.result(timeout=min(float(timeout), remaining))
            except FuturesTimeout:
                fut.cancel()
                out[sid] = {"error": True, "reason": "timeout after %.1fs" % timeout}
                _audit_invoke(root, sid, item["entry"].get("mode"), trace_id, started,
                              {"status": "error", "reason": "timeout after %.1fs" % timeout})
            except Exception as ex:
                out[sid] = {"error": True, "reason": str(ex)}
    finally:
        # 不等待超时/仍在运行的 worker，否则一个卡死的 skill 会把整批拖住。
        pool.shutdown(wait=False)
    return out
