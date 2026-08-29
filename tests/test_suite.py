"""套件框架测试：core 路由裁决 / evolution 蒸馏成长 / deploy 非对称同步 / 版本与完整性。"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 测试密闭化：临时目录建在套件仓库的同级（非 git 仓库内），既避沙箱区外拦截，
# 又避免 temp 目录被套件自身的 git 上下文污染，导致 NOT_A_REPO 断言失效。
tempfile.tempdir = os.path.join(os.path.dirname(ROOT), ".suite_test_tmp")
os.makedirs(tempfile.tempdir, exist_ok=True)

from core import contract, executor  # noqa: E402
from core.arbitrator import arbitrate  # noqa: E402
from core.registry import Registry  # noqa: E402
from core.resolver import resolve, scope_specificity  # noqa: E402
from core.router import route  # noqa: E402
from deploy import integrity  # noqa: E402
from deploy.pull import remote_version, sync_before_use  # noqa: E402
from deploy.remote import GitRemote, RemoteStatus  # noqa: E402
from evolution import distiller, permissions, pipeline  # noqa: E402
from evolution.gate import Gate, run_eval  # noqa: E402
from evolution.growth import GrowthEngine  # noqa: E402
from evolution.store import KnowledgeStore  # noqa: E402
from suite import Suite  # noqa: E402


def make_suite_root():
    tmp = tempfile.mkdtemp(prefix="skill-suite-")
    for sub in ("registry", "skills", "state", "obs", "bridge"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    with open(os.path.join(tmp, "registry", "skills.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"suite": "skill-router-suite", "version": "0.1.0", "skills": {}}, f)
    return tmp


def make_skill(root, skill_id, frontmatter, handler=None, body="用法说明。"):
    directory = os.path.join(root, "skills", skill_id)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\n%s\n---\n\n%s\n" % (frontmatter, body))
    if handler is not None:
        with open(os.path.join(directory, "handler.py"), "w", encoding="utf-8") as f:
            f.write(handler)
    return directory


def handler_source(sid, triggers, fail=False, unhealthy=False):
    body = "    raise RuntimeError('boom')" if fail else "    return {'ok': True, 'skill': '%s', 'echo': data.get('query')}" % sid
    return "\n".join(
        [
            "def describe():",
            "    return {'id': '%s', 'summary': 'demo'}" % sid,
            "",
            "",
            "def can_handle(query):",
            "    return 1 if any(t in query for t in %r) else 0" % (triggers,),
            "",
            "",
            "def invoke(data):",
            body,
            "",
            "",
            "def health():",
            "    return {'ok': %s}" % ("False" if unhealthy else "True"),
            "",
        ]
    )


def pipe_handler(sid):
    return "\n".join(
        [
            "def describe():",
            "    return {'id': '%s'}" % sid,
            "",
            "",
            "def can_handle(query):",
            "    return 1",
            "",
            "",
            "def invoke(data):",
            "    order = list(data.get('order', []))",
            "    order.append('%s')" % sid,
            "    return {'order': order}",
            "",
            "",
            "def health():",
            "    return {'ok': True}",
            "",
        ]
    )


def entry(sid, triggers, **kwargs):
    base = {
        "id": sid,
        "name": sid,
        "domain": ["demo"],
        "triggers": triggers,
        "mode": "llm",
        "path": os.path.join("skills", sid),
        "priority": 0,
        "scope": "*",
    }
    base.update(kwargs)
    return base


def expect_raises(fn, needle):
    try:
        fn()
    except Exception as ex:
        assert needle in str(ex), "expected %r in %r" % (needle, str(ex))
        return
    raise AssertionError("expected exception containing %r" % needle)


def test_contract():
    e = contract.normalize(
        {"id": "a", "name": "A", "domain": "pms", "triggers": ["报表"], "mode": "llm", "path": "skills/a", "priority": 1, "scope": "*"}
    )
    assert e["domain"] == ["pms"]
    assert e["auth"] == "none" and e["enabled"] is True and e["version_pin"] == "0.0.0"
    assert contract.validate(e) is True
    expect_raises(lambda: contract.validate({"id": "a"}), "missing fields")
    expect_raises(lambda: contract.validate({**e, "mode": "weird"}), "mode")
    expect_raises(lambda: contract.validate({**e, "triggers": []}), "triggers")


def test_registry_crud():
    tmp = make_suite_root()
    try:
        reg = Registry(os.path.join(tmp, "registry", "skills.json"))
        reg.upsert(entry("a", ["报表"]))
        reg.upsert(entry("b", ["登录"]))
        assert len(reg.all()) == 2 and reg.get("a")["id"] == "a"
        reg.upsert(entry("a", ["报表"], priority=9))
        assert len(reg.all()) == 2 and reg.get("a")["priority"] == 9
        assert reg.remove("a") is True and reg.remove("zzz") is False
        reloaded = Registry(os.path.join(tmp, "registry", "skills.json"))
        assert [e["id"] for e in reloaded.all()] == ["b"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_resolver_two_stage():
    base = [
        entry("wide", ["报表"], scope="pms.*", priority=5),
        entry("narrow", ["报表"], scope="pms.report", priority=5),
        entry("off", ["报表"], scope="pms.report", priority=5, enabled=False),
    ]
    ranked = resolve(base, "查一下今年报表")
    ids = [r["entry"]["id"] for r in ranked]
    assert "off" not in ids
    assert ids[0] == "narrow" and ids.index("narrow") < ids.index("wide")
    assert scope_specificity("pms.report") > scope_specificity("pms.*")

    with_neg = base + [entry("neg", ["报表"], scope="pms.report", priority=99, negative_triggers=["昨年"])]
    assert "neg" in [r["entry"]["id"] for r in resolve(with_neg, "查一下今年报表")]
    assert "neg" not in [r["entry"]["id"] for r in resolve(with_neg, "查一下昨年报表")]

    boost = [{"id": "r1", "kind": "route", "pattern": ["报表"], "target": "wide", "state": "validated", "support": 3, "success_rate": 1.0}]
    assert resolve(base, "查一下今年报表", experience=boost)[0]["entry"]["id"] == "wide"
    candidate = [{**boost[0], "target": "narrow", "state": "candidate"}]
    assert resolve(base, "查一下今年报表", experience=candidate)[0]["entry"]["id"] == "narrow"
    avoid = [{"id": "r2", "kind": "avoid", "pattern": ["报表"], "target": "narrow", "state": "locked", "support": 4, "success_rate": 0.0}]
    assert resolve(base, "查一下今年报表", experience=avoid)[0]["entry"]["id"] == "wide"


def test_arbitrator():
    assert arbitrate([])[0] == "fallback"
    tied = resolve([entry("a", ["报表"], priority=5, scope="pms.*"), entry("b", ["报表"], priority=5, scope="pms.*")], "查报表")
    assert arbitrate(tied)[0] == "direct" and len(arbitrate(tied)[1]) == 1
    assert arbitrate(tied, allow_parallel=True)[0] == "parallel"
    distinct = resolve([entry("a", ["报表"], priority=5), entry("b", ["报表"], priority=1)], "查报表")
    assert arbitrate(distinct, allow_parallel=True)[0] == "direct"


def test_executor_llm_handoff():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "demo", "name: 演示\ntriggers: [演示]\nscope: demo.x")
        out = executor.invoke_one(entry("demo", ["演示"], path=os.path.join("skills", "demo")), "请演示", root=tmp)
        assert out["mode"] == "llm" and out["skill_id"] == "demo"
        assert out["skill_md"].endswith(os.path.join("skills", "demo", "SKILL.md"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_executor_native():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "ok", "name: OK\ntriggers: [报表]", handler=handler_source("ok", ["报表"]))
        make_skill(tmp, "bad", "name: BAD\ntriggers: [报表]", handler=handler_source("bad", ["报表"], fail=True))
        make_skill(tmp, "sick", "name: SICK\ntriggers: [报表]", handler=handler_source("sick", ["报表"], unhealthy=True))
        e_ok = entry("ok", ["报表"], mode="native", path=os.path.join("skills", "ok"))
        assert executor.invoke_one(e_ok, "查报表", root=tmp)["skill"] == "ok"
        expect_raises(lambda: executor.invoke_one(e_ok, "查登录", root=tmp), "cannot handle")
        expect_raises(
            lambda: executor.invoke_one(entry("sick", ["报表"], mode="native", path=os.path.join("skills", "sick")), "查报表", root=tmp),
            "unhealthy",
        )
        expect_raises(
            lambda: executor.invoke_one(entry("bad", ["报表"], mode="native", path=os.path.join("skills", "bad")), "查报表", root=tmp),
            "boom",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_executor_strategies():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "first", "name: F\ntriggers: [报表]", handler=handler_source("first", ["报表"], fail=True))
        make_skill(tmp, "second", "name: S\ntriggers: [报表]", handler=handler_source("second", ["报表"]))
        make_skill(tmp, "stepa", "name: A\ntriggers: [管道]", handler=pipe_handler("stepa"))
        make_skill(tmp, "stepb", "name: B\ntriggers: [管道]", handler=pipe_handler("stepb"))

        ranked = [
            {"entry": entry("first", ["报表"], mode="native", path=os.path.join("skills", "first"), priority=9), "score": 3},
            {"entry": entry("second", ["报表"], mode="native", path=os.path.join("skills", "second"), priority=1), "score": 2},
        ]
        assert executor.cascade(ranked, "查报表", root=tmp)["skill"] == "second"

        pipe = [
            {"entry": entry("stepb", ["管道"], mode="native", path=os.path.join("skills", "stepb"), depends=["stepa"]), "score": 2},
            {"entry": entry("stepa", ["管道"], mode="native", path=os.path.join("skills", "stepa")), "score": 2},
        ]
        assert executor.pipeline(pipe, "管道", root=tmp)["order"] == ["stepa", "stepb"]

        par = [
            {"entry": entry("first", ["报表"], mode="native", path=os.path.join("skills", "first")), "score": 1},
            {"entry": entry("second", ["报表"], mode="native", path=os.path.join("skills", "second")), "score": 1},
        ]
        out = executor.parallel(par, "查报表", root=tmp)
        assert out["second"]["skill"] == "second" and out["first"]["error"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_router_strategies():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "a", "name: A\ntriggers: [报表]")
        entries = [entry("a", ["报表"], priority=5, path=os.path.join("skills", "a"))]
        assert route(entries, "查报表", strategy="direct", root=tmp)["routed"] == "direct"
        assert route(entries, "查报表", strategy="cascade", root=tmp)["routed"] == "cascade"
        assert route(entries, "查报表", strategy="pipeline", root=tmp)["routed"] == "pipeline"
        fallback = route([], "任意", root=tmp)
        assert fallback["routed"] == "fallback" and fallback["mode"] == "llm"
        assert route([], "任意", fallback=lambda q: {"echo": q}, root=tmp)["result"] == {"echo": "任意"}
        expect_raises(lambda: route(entries, "查报表", strategy="nope", root=tmp), "strategy")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_distiller_frontmatter():
    text = (
        "---\nname: 报表\ndomain: [pms, report]\ntriggers:\n  - 报表\n  - 销售\n"
        "priority: 7\nenabled: false\nmode: native\nversion: 1.4.0\n---\n\n正文\n"
    )
    fm = distiller.parse_frontmatter(text)
    assert fm["name"] == "报表"
    assert fm["domain"] == ["pms", "report"]
    assert fm["triggers"] == ["报表", "销售"]
    assert fm["priority"] == 7 and fm["enabled"] is False and fm["version"] == "1.4.0"


def test_distiller_derive_entry():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表, 销售]\nscope: pms.report\nversion: 2.0.0")
        e = distiller.derive_entry("rep", tmp)
        assert e["mode"] == "llm" and e["triggers"] == ["报表", "销售"] and e["version_pin"] == "2.0.0"

        make_skill(tmp, "nat", "name: 原生\ndomain: [pms]\ntriggers: [登录]", handler=handler_source("nat", ["登录"]))
        assert distiller.derive_entry("nat", tmp)["mode"] == "native"

        make_skill(tmp, "empty", "name: 无触发\ndomain: [pms]")
        expect_raises(lambda: distiller.derive_entry("empty", tmp), "triggers")

        preserved = distiller.derive_entry("rep", tmp, base={"id": "rep", "manual_overrides": ["priority"], "priority": 99})
        assert preserved["priority"] == 99
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_distiller_traces():
    traces = [
        {"query": "查一下pms报表", "routed_skill": "a", "user_override": "b", "success": True},
        {"query": "查pms报表数据", "routed_skill": "a", "user_override": "b", "success": True},
        {"query": "跑一下登录", "routed_skill": "a", "success": False},
        {"query": "跑一下登录啊", "routed_skill": "a", "success": False},
    ]
    rules = distiller.distill_traces(traces, min_support=2)
    kinds = {r["kind"] for r in rules}
    assert "route" in kinds and "avoid" in kinds
    route_rule = [r for r in rules if r["kind"] == "route"][0]
    assert route_rule["target"] == "b" and route_rule["support"] == 2 and route_rule["state"] == "candidate"
    avoid_rule = [r for r in rules if r["kind"] == "avoid"][0]
    assert avoid_rule["target"] == "a" and avoid_rule["state"] == "candidate"


def test_distiller_library():
    entries = [
        entry("a", ["报表", "销售"], priority=5, scope="pms.*"),
        entry("b", ["报表"], priority=5, scope="pms.*"),
        entry("c", ["报表", "销售", "统计"], priority=1, scope="other"),
    ]
    lib = distiller.distill_library(entries)
    assert lib["skill_count"] == 3
    assert {"报表", "销售"} <= {o["trigger"] for o in lib["overlaps"]}
    assert any(set(c["skills"]) == {"a", "b"} for c in lib["conflicts"])
    assert any(r["subset"] == "b" and r["superset"] == "a" for r in lib["redundancies"])


def test_store_lifecycle():
    tmp = make_suite_root()
    try:
        store = KnowledgeStore(os.path.join(tmp, "state", "knowledge.json"))
        rule = {"id": "r1", "kind": "route", "pattern": ["报表"], "target": "a", "support": 2, "success_rate": 1.0, "state": "candidate"}
        store.add_experience([rule])
        assert store.experience() == []
        store.add_experience([{**rule, "support": 1}])
        assert store.data["experience"][0]["support"] == 3
        assert store.data["experience"][0]["state"] == "validated"
        assert len(store.experience()) == 1
        store.add_experience([{**rule, "support": 3}])
        assert store.data["experience"][0]["state"] == "locked"
        store.add_experience(
            [{"id": "r2", "kind": "route", "pattern": ["x"], "target": "b", "support": 1, "success_rate": 0.0, "state": "candidate"}]
        )
        assert "r2" not in {r["id"] for r in store.data["experience"]}
        assert len(KnowledgeStore(os.path.join(tmp, "state", "knowledge.json")).data["experience"]) == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_permissions():
    tmp = make_suite_root()
    try:
        store = permissions.ProposalStore(os.path.join(tmp, "state", "proposals.json"))
        for action in ("read_skill", "add_skill", "remove_skill", "update_registry_entry", "trigger_deploy"):
            assert permissions.guard(action)["allowed"] is True
        try:
            permissions.guard("modify_skill_content", store, {"skill_id": "a"})
            raise AssertionError("modify must require authorization")
        except permissions.AuthorizationRequired as exc:
            pid = exc.proposal_id
        assert len(store.pending()) == 1
        assert store.approve(pid)["state"] == "approved"
        assert store.pending() == []
        expect_raises(lambda: permissions.guard("unknown_action"), "unknown action")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pipeline_register():
    tmp = make_suite_root()
    try:
        reg = Registry(os.path.join(tmp, "registry", "skills.json"))
        manifest = {"skills": {}}
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        for source in pipeline.SOURCES:
            pipeline.register(reg, manifest, "rep", source, tmp)
            assert reg.get("rep") is not None
        assert manifest["skills"]["rep"]["source"] == "remote_pull"
        assert manifest["skills"]["rep"]["version_pin"] == "1.0.0"
        assert pipeline.unregister(reg, manifest, "rep") is True
        assert reg.get("rep") is None and "rep" not in manifest["skills"]
        result = pipeline.propose_modify(permissions.ProposalStore(os.path.join(tmp, "state", "proposals.json")), "rep", {"SKILL.md": "改写"})
        assert result["allowed"] is False and result["proposal_id"]
        expect_raises(lambda: pipeline.register(reg, manifest, "rep", "unknown_source", tmp), "source")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_growth_loop():
    tmp = make_suite_root()
    try:
        reg = Registry(os.path.join(tmp, "registry", "skills.json"))
        store = KnowledgeStore(os.path.join(tmp, "state", "knowledge.json"))
        proposals = permissions.ProposalStore(os.path.join(tmp, "state", "proposals.json"))
        reg.upsert(entry("a", ["报表"], scope="pms.*", priority=5))
        reg.upsert(entry("b", ["报表"], scope="pms.*", priority=5))
        engine = GrowthEngine(tmp, reg, store, proposals=proposals)

        traces = [
            {"query": "跑一下登录", "routed_skill": "a", "success": False},
            {"query": "跑一下登录啊", "routed_skill": "a", "success": False},
            {"query": "跑一下登录嘛", "routed_skill": "a", "success": False},
        ]
        assert engine.reflect(traces)
        lib = store.library_map()
        assert lib["skill_count"] == 2 and lib["conflicts"]
        avoid = store.experience(kinds=("avoid",))
        assert avoid and avoid[0]["state"] == "validated"

        mutations = engine.evolve()
        assert {m["kind"] for m in mutations} >= {"negative_trigger", "priority_adjust"}
        applied = engine.apply(mutations)
        assert applied["pending_authorization"] == []
        assert reg.get("a")["negative_triggers"]
        assert reg.get("a")["priority"] != reg.get("b")["priority"]
        assert "priority" in reg.get("a")["manual_overrides"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate_ratchet():
    tmp = make_suite_root()
    try:
        gate = Gate(tmp)
        first = gate.keep_or_rollback("routing", 0.8, {"entries": [1, 2]})
        assert first["kept"] is True and first["action"] == "accept"
        worse = gate.keep_or_rollback("routing", 0.6, {"entries": [9]})
        assert worse["kept"] is False and worse["action"] == "rollback"
        assert worse["restored"] == {"entries": [1, 2]}
        assert gate.keep_or_rollback("routing", 0.9, {"entries": [3]})["kept"] is True

        prompts = [
            {"prompt": "查报表", "expect_contains": ["报表"], "expect_not_contains": ["错误"], "weight": 2},
            {"prompt": "查登录", "expect_contains": ["nothing"], "weight": 1},
        ]
        assert run_eval(prompts, lambda q: "这是报表结果") == round(2 / 3, 4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_integrity():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]")
        manifest = {"skills": {}, "pending_deploy": False}
        integrity.pin(manifest, tmp, ["rep"])
        assert integrity.verify(manifest, tmp)["ok"] is True
        with open(os.path.join(tmp, "skills", "rep", "SKILL.md"), "a", encoding="utf-8") as f:
            f.write("\ntampered\n")
        report = integrity.verify(manifest, tmp)
        assert report["ok"] is False and report["drift"]
        compat = integrity.compatibility(manifest, [entry("rep", ["报表"], mode="native", path=os.path.join("skills", "rep"))], tmp)
        assert compat["ok"] is False and any("handler.py" in p["reason"] for p in compat["problems"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_update_layer_is_read_only():
    tmp = make_suite_root()
    try:
        reg = Registry(os.path.join(tmp, "registry", "skills.json"))
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]")
        reg.upsert(entry("rep", ["报表"]))

        remote = GitRemote(tmp)
        assert not hasattr(remote, "commit"), "部署层不得具备提交能力（作者端职责）"
        assert not hasattr(remote, "push"), "部署层不得具备推送能力（作者端职责）"
        assert not hasattr(remote, "bootstrap"), "部署层不得初始化仓库（用户端只消费）"
        assert remote.state() == RemoteStatus.NOT_A_REPO

        sync = sync_before_use(tmp, reg, {})
        assert sync["pulled"] is False and sync["reloaded"] is False and "remote" in sync["reason"]

        version = remote_version(tmp, {})
        assert version["state"] == RemoteStatus.NOT_A_REPO and version["has_updates"] is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_suite_end_to_end():
    tmp = make_suite_root()
    try:
        make_skill(
            tmp,
            "report",
            "name: 报表\ndomain: [pms]\ntriggers: [报表, 销售]\nscope: pms.report\nversion: 1.0.0",
            handler=handler_source("report", ["报表", "销售"]),
        )
        make_skill(
            tmp,
            "login",
            "name: 登录\ndomain: [pms]\ntriggers: [登录]\nscope: pms.auth\nversion: 1.0.0",
            handler=handler_source("login", ["登录"]),
        )
        s = Suite(tmp)
        assert s.sync()["pulled"] is False
        s.add_skill("report", "user_drop")
        s.add_skill("login", "user_drop")
        assert len(s.registry.all()) == 2 and set(s.manifest["skills"]) == {"report", "login"}

        assert s.route("查一下销售报表")["result"]["skill"] == "report"
        assert s.route("帮我登录")["result"]["skill"] == "login"
        assert s.route("完全无关的话")["routed"] == "fallback"

        mod = s.modify_skill("report", {"SKILL.md": "改写"})
        assert mod["allowed"] is False and mod["proposal_id"]
        assert len(s.proposals.pending()) == 1

        s.learn([{"query": "跑一下报表导出", "routed_skill": "report", "success": False}] * 3)
        assert s.store.library_map()["skill_count"] == 2
        assert s.users.familiarity() == "novice"

        verdict = s.evaluate("routing", [{"prompt": "查报表", "expect_contains": ["报表"]}], lambda q: "报表结果")
        assert verdict["kept"] is True and verdict["score"] == 1.0

        assert not hasattr(s, "deploy") and not hasattr(s, "connect")
        assert s.version()["state"] == RemoteStatus.NOT_A_REPO
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
