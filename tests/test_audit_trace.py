"""审计与可回放性回归测试。

锁死自进化系统的可解释性底线（ASG-SI：behavioral drift is difficult to audit
or reproduce）：

1. trace_id 必须能跨调用贯穿——旧实现每次 record 现生成一个且不回传，
   导致路由、执行、成长变更各自孤立，事后无法还原"这次请求发生了什么"。
2. 自进化变更必须留痕：依据什么证据、把什么改成了什么、由谁授权。
3. 审计日志必须有容量上限，不能无界增长拖垮磁盘。
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from _harness import setup  # noqa: E402

setup()

from core import audit  # noqa: E402
from core.registry import Registry  # noqa: E402
from evolution import permissions  # noqa: E402
from evolution.growth import GrowthEngine  # noqa: E402
from evolution.store import KnowledgeStore  # noqa: E402
from suite import Suite  # noqa: E402


def make_suite_root():
    tmp = tempfile.mkdtemp(prefix="skill-audit-")
    for sub in ("registry", "skills", "state"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    with open(os.path.join(tmp, "registry", "skills.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"suite": "LeyaoSeedSkill", "version": "0.1.0", "skills": {}}, f)
    return tmp


def make_skill(root, skill_id, frontmatter, body="用法说明。"):
    directory = os.path.join(root, "skills", skill_id)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\n%s\n---\n\n%s\n" % (frontmatter, body))
    return directory


def entry(sid, triggers, **kwargs):
    base = {
        "id": sid,
        "name": sid,
        "domain": ["demo"],
        "triggers": triggers,
        "mode": "llm",
        "path": os.path.join("skills", sid),
        "priority": 5,
        "scope": "pms.*",
    }
    base.update(kwargs)
    return base


def test_route_returns_trace_id():
    """route 必须透出 trace_id，否则调用方无从续接链路。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "report", "name: report\ndescription: 用于查询销售报表的数据技能\ntriggers: [报表]")
        suite = Suite(tmp)
        suite.discover()
        got = suite.route("查一下销售报表")
        assert got.get("trace_id"), "route 未透出 trace_id"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_trace_id_spans_multiple_calls():
    """传入同一 trace_id 时，多次调用的事件必须落在同一条 trace 上。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "report", "name: report\ndescription: 用于查询销售报表的数据技能\ntriggers: [报表]")
        suite = Suite(tmp)
        suite.discover()

        first = suite.route("查一下销售报表")
        tid = first["trace_id"]
        second = suite.route("再查一次销售报表", trace_id=tid)
        assert second["trace_id"] == tid

        events = audit.replay(tid, root=tmp)
        # 一次调用至少落两类事件：route（路由决策）+ skill.invoke（执行结果）。
        # 执行结果此前不落审计，trace 只能看到"路由到了谁"，看不到"跑得怎么样"。
        kinds = [e["event"] for e in events]
        assert kinds.count("route") == 2, "trace 未贯穿，route 只取到 %d 条" % kinds.count("route")
        assert kinds.count("skill.invoke") == 2, "执行结果未并入同一条 trace：%s" % kinds
        # OTel GenAI 结构对齐字段必须存在
        for e in events:
            assert e.get("span_id")
            assert e.get("operation.name") == e["event"]
            assert "duration_ms" in e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_distinct_calls_have_distinct_traces():
    """不传 trace_id 时两次调用应各自成链，不能误串。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "report", "name: report\ndescription: 用于查询销售报表的数据技能\ntriggers: [报表]")
        suite = Suite(tmp)
        suite.discover()
        a = suite.route("查报表")
        b = suite.route("查报表")
        assert a["trace_id"] != b["trace_id"]
        # 单次调用落 route + skill.invoke 两条，且都必须挂在同一条 trace 上
        events = audit.replay(a["trace_id"], root=tmp)
        assert {e["event"] for e in events} == {"route", "skill.invoke"}, events
        assert len(audit.replay(b["trace_id"], root=tmp)) == len(events)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_growth_changes_are_audited_with_evidence():
    """自进化变更必须留痕：证据、变更前后值、授权来源。"""
    tmp = make_suite_root()
    try:
        reg = Registry(os.path.join(tmp, "registry", "skills.json"))
        store = KnowledgeStore(os.path.join(tmp, "state", "knowledge.json"))
        proposals = permissions.ProposalStore(os.path.join(tmp, "state", "proposals.json"))
        reg.upsert(entry("a", ["报表"]))
        reg.upsert(entry("b", ["报表"]))
        engine = GrowthEngine(tmp, reg, store, proposals=proposals)

        traces = [
            {"query": "跑一下报表", "routed_skill": "a", "success": False},
            {"query": "跑一下报表啊", "routed_skill": "a", "success": False},
            {"query": "跑一下报表嘛", "routed_skill": "a", "success": False},
        ]
        engine.reflect(traces)
        result = engine.apply(engine.evolve())
        assert result["applied"], result

        events = audit.replay(result["trace_id"], root=tmp)
        applied_events = [e for e in events if e["event"] == "growth.apply"]
        assert applied_events, "自进化变更未留痕：%s" % [e["event"] for e in events]

        kinds = {e["kind"] for e in applied_events}
        assert "negative_trigger" in kinds, kinds

        neg = next(e for e in applied_events if e["kind"] == "negative_trigger")
        assert neg["authority"] == "autonomous"
        assert neg["before"], "缺少变更前取值，无法回放"
        assert neg["after"], "缺少变更后取值，无法回放"
        # 证据锚定：没有 support / success_rate 就无法判断这次变更是否合理
        assert neg["evidence"].get("support"), neg["evidence"]
        assert "success_rate" in neg["evidence"], neg["evidence"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_log_rotates():
    """审计日志必须有容量上限，不能无界增长。"""
    tmp = make_suite_root()
    try:
        original = audit.MAX_BYTES
        audit.MAX_BYTES = 2048
        try:
            for i in range(200):
                audit.record("smoke", root=tmp, index=i)
            path = os.path.join(tmp, "state", "audit.log")
            assert os.path.exists(path)
            assert os.path.getsize(path) < audit.MAX_BYTES + 4096, "日志未轮转，仍在无界增长"
            rotated = path + ".1"
            assert os.path.exists(rotated), "未生成轮转副本"
        finally:
            audit.MAX_BYTES = original
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_replay_spans_rotated_files():
    """replay 必须跨轮转文件查找，并按时间升序还原。

    历史证据不应只存在于当前日志里——否则一轮转，自进化变更的审计线索就断了。
    """
    tmp = make_suite_root()
    try:
        log = os.path.join(tmp, "state", "audit.log")
        tid = audit.new_trace()
        archived = {"ts": 1.0, "trace_id": tid, "span_id": "s0", "event": "marker", "tag": "oldest"}
        with open(log + ".1", "w", encoding="utf-8") as f:
            f.write(json.dumps(archived) + "\n")
        audit.record("marker", root=tmp, trace_id=tid, tag="newest")

        events = audit.replay(tid, root=tmp)
        tags = [e.get("tag") for e in events]
        assert tags == ["oldest", "newest"], "未按时间跨文件还原：%s" % tags
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_native_invocation_outcome_is_audited():
    """执行成败必须留痕，且 reason 要能支撑排障——此前 trace 里只有路由决策。"""
    tmp = make_suite_root()
    try:
        d = make_skill(tmp, "boom", "name: boom\ndescription: 用于演示失败执行的技能\ntriggers: [炸]")
        with open(os.path.join(d, "handler.py"), "w", encoding="utf-8") as f:
            f.write("\n".join([
                "def describe(): return {'id': 'boom'}",
                "def can_handle(q): return 1",
                "def health(): return {'ok': True}",
                "def invoke(data): raise RuntimeError('kaboom')",
                "",
            ]))
        suite = Suite(tmp)
        suite.discover()
        # direct 策略下 skill 异常会原样冒泡给调用方（既有行为，不在本测试范围）。
        # 这里预先生成 trace_id：既能在异常后仍取回审计，也顺带验证
        # 调用方传入的 trace_id 能一路贯穿到执行层。
        tid = audit.new_trace()
        raised = None
        try:
            got = suite.route("炸一下", trace_id=tid)
        except RuntimeError as ex:
            raised = ex
        assert raised is not None and "kaboom" in str(raised), "异常未冒泡给调用方：%r" % raised

        events = [e for e in audit.replay(tid, root=tmp) if e["event"] == "skill.invoke"]
        assert events, "执行结果未落审计"
        assert events[-1]["status"] == "error", events
        assert "kaboom" in events[-1]["reason"], events
        assert events[-1]["skill"] == "boom", events
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_audit_never_records_result_content():
    """审计只记"跑没跑成、花了多久"，不记 skill 的返回内容——返回可能含业务敏感数据。"""
    tmp = make_suite_root()
    try:
        secret_payload = "CONFIDENTIAL-PATIENT-RECORD-42"
        d = make_skill(tmp, "data", "name: data\ndescription: 用于查询敏感业务数据的技能\ntriggers: [病历]")
        with open(os.path.join(d, "handler.py"), "w", encoding="utf-8") as f:
            f.write("\n".join([
                "def describe(): return {'id': 'data'}",
                "def can_handle(q): return 1",
                "def health(): return {'ok': True}",
                "def invoke(data): return {'record': '%s'}" % secret_payload,
                "",
            ]))
        suite = Suite(tmp)
        suite.discover()
        got = suite.route("查病历")
        events = audit.replay(got["trace_id"], root=tmp)
        blob = json.dumps(events, ensure_ascii=False)
        assert secret_payload not in blob, "审计日志记录了 skill 返回的业务数据"
        assert any(e["event"] == "skill.invoke" and e["status"] == "ok" for e in events), events
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tail_reads_recent_events_and_filters_by_trace():
    """tail 是"不知道 trace_id 时怎么查日志"的唯一入口——replay 只认 trace。

    必须显式传 root：缺省落 cwd 会把日志写到进程启动目录，事后找不回来。
    """
    tmp = make_suite_root()
    try:
        a = audit.record("route", root=tmp, query="q1")
        audit.record("route", root=tmp, query="q2")
        b = audit.record("route", root=tmp, query="q3")

        recent = audit.tail(10, root=tmp)
        assert [e["query"] for e in recent] == ["q1", "q2", "q3"], recent

        assert len(audit.tail(10, root=tmp, trace_id=a)) == 1
        assert audit.tail(10, root=tmp, trace_id=a)[0]["query"] == "q1"
        assert audit.tail(10, root=tmp, trace_id=b)[0]["query"] == "q3"
        assert audit.tail(10, root=tmp, trace_id="nope") == []
        # n 是"最近 n 条"而不是"全部"
        assert len(audit.tail(2, root=tmp)) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, True, ""))
        except AssertionError as ex:
            results.append((name, False, "assert: %s" % ex))
        except Exception as ex:
            results.append((name, False, "%s: %s" % (type(ex).__name__, ex)))
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " -> " + detail))
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
