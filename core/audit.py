"""结构化审计日志（企业级可审计性）。

依据 Klarna「routing they could point at the code and prove」与 OWASP Agentic Trust：
每个路由决策、发现动作必须带时间戳、trace_id、关键字段，落盘为 JSONL，可被审计/回放。

事件结构对齐 OpenTelemetry GenAI Semantic Conventions（2026-06 已 stable）：
trace_id / span_id / parent_span_id / operation.name / duration_ms / status。
这样未来接入任意 OTel 后端是零成本的——但**不引入 OTel SDK**，保持本套件零依赖定位。

trace 贯穿（此前断裂的地方）：
旧实现每次 record 都现生成一个 trace_id 且不回传，于是路由、执行、成长变更
各自成孤立的日志行，无法还原"这次请求到底发生了什么"。现在 trace_id 可由
调用方传入并随返回值透出，一次用户请求可串成完整的一条 trace。

并发安全（线程锁）；任一异常仅返回 None，不影响主流程。
"""

import json
import os
import threading
import time
import uuid

_lock = threading.Lock()

# 单文件软上限，超出即轮转。审计日志无上限增长会拖垮磁盘与检索；
# 且它是唯一的历史证据载体，不能像缓存那样随手清空。
MAX_BYTES = 5 * 1024 * 1024
KEEP_ROTATED = 3


def new_trace():
    return uuid.uuid4().hex[:16]


def new_span():
    return uuid.uuid4().hex[:16]


def _base(root):
    """审计日志根目录。**调用方必须显式传 root**——缺省落 cwd 会让日志散落在进程
    启动目录，事后根本找不回来，可审计性直接归零。
    """
    return root or os.getcwd()


def _log_path(base):
    return os.path.join(base, "state", "audit.log")


def _log_files(path):
    """当前日志 + 轮转副本，回放时需要跨文件查找历史 trace。"""
    files = [path]
    for i in range(1, KEEP_ROTATED + 1):
        rotated = "%s.%d" % (path, i)
        if os.path.exists(rotated):
            files.append(rotated)
    return files


def _rotate_if_needed(path):
    try:
        if not os.path.exists(path) or os.path.getsize(path) < MAX_BYTES:
            return
        for i in range(KEEP_ROTATED - 1, 0, -1):
            src = "%s.%d" % (path, i)
            dst = "%s.%d" % (path, i + 1)
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(path, path + ".1")
    except OSError:
        pass


def record(event_type, root=None, trace_id=None, span_id=None, parent_span_id=None,
           status="ok", duration_ms=None, **fields):
    """追加一行 JSONL 到 <root>/state/audit.log。返回 trace_id，失败返回 None。

    trace_id 由调用方传入即可把多次 record 串成同一条 trace（贯穿）；
    不传则新建，等价于旧行为。
    """
    try:
        base = _base(root)
        path = _log_path(base)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tid = trace_id or new_trace()
        entry = {
            "ts": round(time.time(), 3),
            "trace_id": tid,
            "span_id": span_id or new_span(),
            "parent_span_id": parent_span_id,
            "event": event_type,
            "operation.name": event_type,
            "status": status,
        }
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 3)
        entry.update(fields)
        with _lock:
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return tid
    except Exception:
        return None


def _read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


def tail(n=50, root=None, trace_id=None):
    """读取最近 n 条审计记录（供 CLI/调试），失败返回 []。指定 trace_id 时按 trace 过滤。"""
    try:
        base = _base(root)
        path = _log_path(base)
        lines = _read_lines(path)
        out = []
        for ln in lines[-n:]:
            try:
                item = json.loads(ln)
            except Exception:
                continue
            if trace_id is not None and item.get("trace_id") != trace_id:
                continue
            out.append(item)
        return out
    except Exception:
        return []


def replay(trace_id, root=None):
    """按 trace_id 还原整条链路（跨轮转文件，按 ts 升序）。

    自进化系统的可审计性依赖这个能力：ASG-SI 指出"behavioral drift is difficult
    to audit or reproduce"，而可回放的 trace 正是把漂移变成可解释的前提。
    """
    if not trace_id:
        return []
    try:
        base = _base(root)
        out = []
        for path in _log_files(_log_path(base)):
            for ln in _read_lines(path):
                try:
                    item = json.loads(ln)
                except Exception:
                    continue
                if item.get("trace_id") == trace_id:
                    out.append(item)
        out.sort(key=lambda e: e.get("ts", 0))
        return out
    except Exception:
        return []
