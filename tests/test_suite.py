"""套件框架测试：core 路由裁决 / evolution 蒸馏成长 / deploy 非对称同步 / 版本与完整性。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from _harness import setup  # noqa: E402

setup()

from core import contract, executor  # noqa: E402
from core import resolver  # noqa: E402
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
    for sub in ("registry", "skills", "state"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    with open(os.path.join(tmp, "registry", "skills.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"suite": "LeyaoSeedSkill", "version": "0.1.0", "skills": {}}, f)
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
    assert e["enabled"] is True and e["version_pin"] == "0.0.0"
    # 死字段必须保持删除：auth / health 曾写在条目里却无人消费，
    # 会让读注册表的一方（含 AI）误以为框架在跟踪鉴权要求与健康度。
    assert "auth" not in e, "auth 是无人消费的死字段，不得重新引入"
    assert "health" not in e, "health 条目字段是死字段（health() 作为 native 契约函数保留）"
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


def test_experience_scope_is_ranking_only():
    """经验规则只在「已召回的候选」内调序，救不活零召回。

    为什么必须锁死这条边界：
        一旦 `recall()` 开始接受经验数据，"你以前手动选过它"就能把一个语义上毫不相干的
        skill 硬推进候选集——历史偏好就此推翻语义相关性，套件「宁可 fallback 也不错召」
        的底层取舍随之失效，且这种错召极难归因。

    为什么同一条测试里要先验证"规则确实活着"：
        零召回返回 [] 有两种解释：规则被消费了但无处施加，或规则压根没生效。只断言空
        结果的话后者会假绿——规则被静默丢弃时，结果同样是空的。所以先用多候选场景证明
        规则确实被 `rank` 消费，再证明它在零召回下依然无能为力。

    为什么还要断言"排序确实变了"：
        若目标 skill 恰好已排第一，规则生效与否看不出差别，整条测试会退化成恒真断言
        （本套件设计时踩过这个坑）。`plain != tilted` 就是防这点的。
    """
    def _rule(pattern, target, state="locked"):
        return {"id": "r-%s" % target, "kind": "route", "pattern": pattern,
                "target": target, "state": state, "support": 6, "success_rate": 1.0}

    sales = entry("sales-report", ["报表", "销售报表"])
    stock = entry("stock-check", ["报表", "库存报表"])
    base = [sales, stock]

    # 1) 多候选场景下规则确实被消费：先证明它活着，后面的空结果才有解释力。
    assert resolver.recall(base, "看一下报表") != [], "夹具失效：本应召回两个候选"
    plain = [r["entry"]["id"] for r in resolve(base, "看一下报表")]
    tilted = [r["entry"]["id"] for r in resolve(base, "看一下报表", experience=[_rule(["报表"], "sales-report")])]
    assert plain != tilted, "规则未改变排序，后续零召回断言将退化为恒真（假绿风险）"
    assert tilted[0] == "sales-report", "经验规则应把目标 skill 顶到首位，实际 %r" % (tilted,)

    # 2) 零召回场景：query 与任何 trigger 都不匹配。
    assert resolver.recall(base, "看一下业绩") == [], "夹具失效：本应零召回"

    # 3) 规则确实命中了这个 query（boost 非 0），却仍然救不活——这才是有牙齿的断言。
    assert resolver.experience_boost(
        [_rule(["业绩"], "sales-report")], sales,
        "看一下业绩".lower(), resolver.norm("看一下业绩")) > 0
    rescued = resolve(base, "看一下业绩", experience=[_rule(["业绩"], "sales-report")])
    assert rescued == [], ("经验规则不得救活零召回：一旦 recall 接受经验数据，历史偏好就能推翻"
                           "语义相关性，实际召回了 %r" % ([r["entry"]["id"] for r in rescued],))

    # 4) 晋升门槛：candidate 规则永不进生产，排序不得因此改变。
    assert [r["entry"]["id"] for r in resolve(base, "看一下报表",
            experience=[_rule(["报表"], "sales-report", state="candidate")])] == plain


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
        # 自主动作：只读与框架自运维，不该打断任何人
        for action in ("read_skill", "update_registry_entry", "trigger_deploy"):
            assert permissions.guard(action)["allowed"] is True
        # 改写用户资产的三类动作一律需授权：改内容、增条目、删条目
        for action in ("add_skill", "remove_skill", "modify_skill_content"):
            try:
                permissions.guard(action, store, {"skill_id": "a"})
                raise AssertionError("%s must require authorization" % action)
            except permissions.AuthorizationRequired as exc:
                assert exc.proposal_id, "%s 未产出提案" % action
        assert len(store.pending()) == 3
        for item in store.pending():
            assert store.approve(item["id"])["state"] == "approved"
        assert store.pending() == []
        expect_raises(lambda: permissions.guard("unknown_action"), "unknown action")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_add_and_remove_require_user_authorization():
    """增删子 skill 与改内容同级，都需用户授权；discover 是唯一豁免。

    为什么增删也要守：
        静默加一条，用户不知道自己套件里多了什么、会被哪些 query 命中；静默删一条，
        等于让某些 query 的承接方凭空消失。两者与"改内容"同属对外部可见行为的变更。

    为什么 discover 豁免：
        它扫的是用户**自己放进** skills/ 的目录。把文件放进去这个动作本身就是授权，
        再要一次确认是重复且打断自动化的。真正要守的是 AI 程序化调用增删。

    为什么必须断言"未经批准不得写入"而不只是"返回了提案"：
        只断言返回提案，无法区分"真的拦住了"与"写进去了但顺手也给了个提案 id"。
        后者同样让前面的断言通过，却完全没守住边界。
    """
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        s = Suite(tmp)

        # 1) discover 不受授权门约束：放入即授权，且不产生待办提案
        assert [a for _, a, _ in s.discover()] == ["registered"]
        assert s.registry.get("rep") is not None
        assert s.pending_proposals() == []

        # 2) add_skill 只产出提案，绝不直接写入
        make_skill(tmp, "extra", "name: 附加\ndomain: [pms]\ntriggers: [附加]\nversion: 1.0.0")
        prop = s.add_skill("extra", "user_drop")
        assert prop["allowed"] is False and prop["action"] == "add_skill" and prop["proposal_id"], (
            "add_skill 必须拦下并产出提案，实际返回 %r" % (prop,))
        assert s.registry.get("extra") is None, "未经批准不得写入路由表"

        # 3) 批准后真正登记，且 source 等参数确实从 payload 取回（批准可能跨进程）
        assert s.approve_proposal(prop["proposal_id"])["state"] == "approved"
        assert s.registry.get("extra") is not None, (
            "批准 add 提案后必须真的登记进路由表——只改提案状态不执行的批准等于死循环，"
            "用户点了同意却什么都没发生")
        assert s.manifest["skills"]["extra"]["source"] == "user_drop", (
            "source 必须从 payload 取回并落盘，批准可能发生在另一个进程，调用栈早已不在，"
            "实际 manifest=%r" % (s.manifest["skills"].get("extra"),))
        assert integrity.verify(s.manifest, tmp)["ok"] is True, (
            "登记后必须复 pin 完整性，否则刚拉进来的 skill 立即处于未 pin 状态")

        # 4) remove_skill 同样只产出提案，未经批准不得删除
        rm = s.remove_skill("rep")
        # 先判类型再取值：绕过门的实现会直接返回 bool，用 isinstance 才不会
        # 退化成一句无关的 TypeError，而是明确说出"没拦住"。
        assert isinstance(rm, dict) and rm.get("allowed") is False and rm.get("action") == "remove_skill", (
            "remove_skill 必须拦下并产出提案，实际返回 %r" % (rm,))
        assert s.registry.get("rep") is not None, "未经批准不得删除"
        assert s.approve_proposal(rm["proposal_id"])["state"] == "approved"
        assert s.registry.get("rep") is None, (
            "批准 remove 提案后必须真的从路由表摘除，仅置 approved 不执行等于批准无效")
        assert "rep" not in s.manifest["skills"], (
            "删除必须同时清路由表与 manifest，只清一处会造成 registry/manifest 不一致")

        # 5) 拒绝则一切不变
        rm2 = s.remove_skill("extra")
        s.reject_proposal(rm2["proposal_id"])
        assert s.registry.get("extra") is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_remove_proposal_edge_cases():
    """删提案的两个边界：source 非法当场拒；删不存在的 skill 在批准时报错而非静默成功。

    source 为何要当场拒：它来自调用方入参，与提案是否批准无关，拖到批准时才报错
    会让用户面对一条注定失败的提案却不知道为什么。

    删空气为何在批准时报错而非静默通过：批准一个"删不存在的东西"的提案，
    通常意味着 id 写错了。静默成功会掩盖这个错误，让它看起来像正常删除。
    """
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        s = Suite(tmp)
        s.discover()

        expect_raises(lambda: s.add_skill("bad", "not_a_source"), "source must be one of")

        orphan = s.proposals.propose("remove_skill", {"skill_id": "ghost"})
        expect_raises(lambda: s.approve_proposal(orphan["id"]), "skill not registered")
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


def test_integrity_reports_version_drift_as_diagnostic():
    """version_pin 必须被真正消费：pin 时刷新，漂移时给出可读的版本变化。

    版本漂移只是诊断，不改变 ok 结论——version 写在 SKILL.md 里已被 content_hash
    覆盖，单独再判一次是重复检测。它的价值是让"改了什么"可读。
    """
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\nversion: 1.0.0\ntriggers: [报表]")
        manifest = {"skills": {}}
        integrity.pin(manifest, tmp, ["rep"])
        assert manifest["skills"]["rep"]["version_pin"] == "1.0.0", manifest

        # 改内容同时改版本：hash 漂移 + 版本诊断
        path = os.path.join(tmp, "skills", "rep", "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\nname: 报表\nversion: 2.0.0\ntriggers: [报表]\n---\n\n改过了\n")
        report = integrity.verify(manifest, tmp)
        assert report["ok"] is False and report["drift"]
        assert report["version_drift"] == [{"skill": "rep", "expected": "1.0.0", "actual": "2.0.0"}], report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_integrity_repin_refreshes_version_pin():
    """复 pin 必须同步刷新版本号，否则 manifest 里留旧版本，漂移报告会误导。

    approve_proposal 落地变更后就是走这条复 pin 路径。
    """
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\nversion: 1.0.0\ntriggers: [报表]")
        manifest = {"skills": {}}
        integrity.pin(manifest, tmp, ["rep"])
        assert manifest["skills"]["rep"]["version_pin"] == "1.0.0"

        path = os.path.join(tmp, "skills", "rep", "SKILL.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("---\nname: 报表\nversion: 1.5.0\ntriggers: [报表]\n---\n\n新版本\n")
        integrity.pin(manifest, tmp, ["rep"])
        assert manifest["skills"]["rep"]["version_pin"] == "1.5.0", manifest
        assert integrity.verify(manifest, tmp)["ok"] is True
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
        # 用 discover 而非 add_skill 做前置登记：用户丢入目录即授权，
        # discover 是不经授权门的正规路径（add_skill 自 v2.1 起需用户批准）。
        s.discover()
        assert len(s.registry.all()) == 2 and set(s.manifest["skills"]) == {"report", "login"}

        assert s.route("查一下销售报表")["result"]["skill"] == "report"
        assert s.route("帮我登录")["result"]["skill"] == "login"
        assert s.route("完全无关的话")["routed"] == "fallback"

        mod = s.modify_skill("report", {"SKILL.md": "改写"})
        assert mod["allowed"] is False and mod["proposal_id"]
        assert len(s.proposals.pending()) == 1

        s.learn([{"query": "跑一下报表导出", "routed_skill": "report", "success": False}] * 3)
        assert s.store.library_map()["skill_count"] == 2
        # 用户建模必须有出口：观察到 3 次 report，usage 就得记下来，
        # 否则写进知识库的是死数据。
        assert s.users.usage() == {"report": 3}

        verdict = s.evaluate("routing", [{"prompt": "查报表", "expect_contains": ["报表"]}], lambda q: "报表结果")
        assert verdict["kept"] is True and verdict["score"] == 1.0

        assert not hasattr(s, "deploy") and not hasattr(s, "connect")
        assert s.version()["state"] == RemoteStatus.NOT_A_REPO
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_async_selfcheck_noop_when_not_git_repo():
    tmp = make_suite_root()
    try:
        s = Suite(tmp)
        # 非受管 git 套件仓库（仅临时目录、无 git 上下文）：后台自检应启发式 no-op
        # —— 不依赖硬编码目录名，由 sync_before_use 据真实仓库状态判定；不触网、不抛错、不破坏路由表
        s._async_selfcheck_and_sync()
        assert s.registry is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_async_selfcheck_pulls_when_configured_repo_has_updates():
    upstream = author = consumer_parent = None
    try:
        # 上游：本地裸仓库；作者工作副本（非裸）提交后推上去
        upstream = tempfile.mkdtemp(prefix="async-up-")
        assert subprocess.run(["git", "init", "--bare", "-b", "main", upstream], capture_output=True).returncode == 0
        author = tempfile.mkdtemp(prefix="async-author-")
        assert subprocess.run(["git", "init", "-b", "main", author], capture_output=True).returncode == 0
        subprocess.run(["git", "-C", author, "config", "user.email", "a@b.c"], capture_output=True)
        subprocess.run(["git", "-C", author, "config", "user.name", "A"], capture_output=True)
        subprocess.run(["git", "-C", author, "remote", "add", "origin", upstream], capture_output=True)
        with open(os.path.join(author, "seed.txt"), "w", encoding="utf-8") as f:
            f.write("seed")
        subprocess.run(["git", "-C", author, "add", "-A"], capture_output=True)
        assert subprocess.run(["git", "-C", author, "commit", "-m", "seed"], capture_output=True).returncode == 0
        assert subprocess.run(["git", "-C", author, "push", "-u", "origin", "main"], capture_output=True).returncode == 0

        # 消费副本（clone 自上游，父目录非 skills，证明不依赖目录名）
        consumer_parent = tempfile.mkdtemp(prefix="async-cons-")
        consumer = os.path.join(consumer_parent, "suite")
        assert subprocess.run(["git", "clone", upstream, consumer], capture_output=True).returncode == 0
        os.makedirs(os.path.join(consumer, "registry"), exist_ok=True)
        with open(os.path.join(consumer, "registry", "skills.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        with open(os.path.join(consumer, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"suite": "LeyaoSeedSkill", "version": "0.1.0", "skills": {},
                       "deploy": {"provider": "git", "mode": "read-only", "remote": "origin",
                                  "remote_url": upstream, "branch": "main"}}, f)

        s = Suite(consumer)
        assert s.sync()["pulled"] is False  # 已是最新，no-op

        # 上游再发一次更新
        with open(os.path.join(author, "marker.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        subprocess.run(["git", "-C", author, "add", "-A"], capture_output=True)
        assert subprocess.run(["git", "-C", author, "commit", "-m", "up"], capture_output=True).returncode == 0
        assert subprocess.run(["git", "-C", author, "push", "origin", "main"], capture_output=True).returncode == 0

        # 启发式自检：配置完备且有更新 → 应拉取（与目录名无关）
        s._async_selfcheck_and_sync()
        assert os.path.exists(os.path.join(consumer, "marker.txt"))
    finally:
        for p in (upstream, author, consumer_parent):
            if p:
                shutil.rmtree(p, ignore_errors=True)


def test_schedule_background_sync_returns_without_blocking():
    tmp = make_suite_root()
    try:
        s = Suite(tmp)
        # 启动异步后台不应阻塞首用、不应触网（非受管仓库由 sync_before_use 内部 no-op）
        s.schedule_background_sync()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_discover_registers_unseen_skills():
    tmp = make_suite_root()
    try:
        # 预先把 skill 原样放进 skills/ 但路由表为空：discover 应幂等登记、逐 skill 隔离。
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        make_skill(tmp, "nat", "name: 原生\ndomain: [pms]\ntriggers: [登录]", handler=handler_source("nat", ["登录"]))
        os.makedirs(os.path.join(tmp, "skills", "junk"), exist_ok=True)  # 无 SKILL.md：跳过，不阻断其余
        s = Suite(tmp)
        results = s.discover()
        actions = {sid: act for sid, act, _ in results}
        assert actions["rep"] == "registered"
        assert actions["nat"] == "registered"
        assert actions["junk"] == "skipped"
        assert {e["id"] for e in s.registry.all()} == {"rep", "nat"}
        # 幂等：再次 discover 已注册者跳过
        actions2 = {sid: act for sid, act, _ in s.discover()}
        assert actions2["rep"] == "skipped"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_discover_registers_skill_without_domain():
    # 壳层完善性：用户丢一个只带 triggers、无 domain 的最小 skill，不应被契约拦截。
    tmp = make_suite_root()
    try:
        make_skill(tmp, "mini", "name: 最小\nmode: llm\ntriggers: [最小触发]\nversion: 0.1.0")
        s = Suite(tmp)
        results = s.discover()
        actions = {sid: act for sid, act, _ in results}
        assert actions["mini"] == "registered", "无 domain 的最小 skill 应被登记而非跳过"
        entry = s.registry.get("mini")
        assert entry["domain"] == []  # 缺省空列表，路由按 triggers 命中
        routed = s.route("最小触发一下")
        assert routed["routed"] == "direct" and routed["result"]["skill_id"] == "mini"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_approve_proposal_executes():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        s = Suite(tmp)
        s.discover()
        mod = s.modify_skill("rep", {"description": "改写后的描述"})
        assert mod["allowed"] is False and mod["proposal_id"]
        # 批准应执行：重派生 entry + 写回结构化字段 + 复 pin
        proposal = s.approve_proposal(mod["proposal_id"])
        assert proposal["state"] == "approved"
        assert s.registry.get("rep")["description"] == "改写后的描述"
        assert integrity.verify(s.manifest, tmp)["ok"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_reject_proposal_closes_the_loop():
    """提案状态机必须有拒绝这个终态。

    只有 approved 的话，一条没人认领的提案会永远悬在 pending，
    pending 列表随时间长成噪音，最后没人再看它。
    """
    tmp = make_suite_root()
    try:
        make_skill(tmp, "rep", "name: 报表\ntriggers: [报表]")
        s = Suite(tmp)
        s.discover()
        mod = s.modify_skill("rep", {"description": "不该生效的改写"})
        assert len(s.pending_proposals()) == 1

        assert s.reject_proposal(mod["proposal_id"])["state"] == "rejected"
        assert s.pending_proposals() == []
        # 拒绝是终态：不得再被批准，也不得改动路由表。
        assert s.registry.get("rep")["description"] != "不该生效的改写"
        assert len(s.proposals.items) == 1
        assert s.proposals.items[0]["state"] == "rejected"
        # 持久化：换一个实例从磁盘重读，状态必须仍是 rejected。
        # 少了 save() 时内存里是 rejected、磁盘上还挂着 pending，重启即回潮。
        reloaded = permissions.ProposalStore(os.path.join(tmp, "state", "proposals.json"))
        assert [i["id"] for i in reloaded.pending()] == [], reloaded.items
        assert reloaded.get(mod["proposal_id"])["state"] == "rejected", reloaded.items
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_usage_boost_tilts_without_overriding_semantics():
    """用户历史频次只能在同一档内调序，不能推翻语义更相关的结果。

    上限定为 0.5，刻意小于一个 trigger 命中（2.0）。
    """
    entries = [
        {"id": "used", "name": "used", "triggers": [], "domain": [], "description": "销售报表查询"},
        {"id": "fresh", "name": "fresh", "triggers": ["促销毛利"], "domain": [], "description": ""},
    ]
    # 1) 零历史：语义胜出，与没有这个特性时完全一致。
    top = resolve(entries, "促销毛利")[0]["entry"]["id"]
    assert top == "fresh", top
    # 2) 有历史但语义差距更大：used 只有 description 弱信号，仍不得反超。
    top = resolve(entries, "促销毛利", usage={"used": 100})[0]["entry"]["id"]
    assert top == "fresh", top
    # 3) 同档内（都是 description 命中、score 相同）时，用得多的往前排。
    #    零历史下并列项由 id 反向决定（sort reverse），所以基线顺序是 b 在 a 前；
    #    给 a 更多历史后必须反超——这才证明加成真的进了排序。
    ties = [
        {"id": "a", "name": "a", "triggers": [], "domain": [], "description": "销售报表查询"},
        {"id": "b", "name": "b", "triggers": [], "domain": [], "description": "销售报表查询"},
    ]
    assert [r["entry"]["id"] for r in resolve(ties, "销售报表")] == ["b", "a"]
    assert [r["entry"]["id"] for r in resolve(ties, "销售报表", usage={"a": 20, "b": 1})] == ["a", "b"]


def test_usage_boost_is_bounded():
    """加成上限必须小于一个 trigger 命中，否则"用过"会压过"更相关"。"""
    assert resolver.W_USAGE_SCALE < resolver.W_TRIGGER
    entry = {"id": "a", "name": "a", "triggers": [], "domain": []}
    assert resolver.usage_boost({"a": 999999}, entry) <= resolver.W_USAGE_SCALE
    assert resolver.usage_boost({}, entry) == 0.0
    assert resolver.usage_boost({"a": 0}, entry) == 0.0


def test_sync_reports_compatibility_problems():
    """拉取后必须做 registry / manifest / 文件系统三方对账。

    上游完全可能提交不一致的状态（加了 entry 却忘了 pin），
    用户端拉下来若不报，就会带着一份自相矛盾的配置继续跑。
    """
    tmp = make_suite_root()
    try:
        # 路由表里有、文件系统里没有：典型的"上游提交了 entry 却没带 skill 目录"。
        report = integrity.compatibility(
            {"skills": {}}, [{"id": "ghost", "name": "ghost", "mode": "llm", "path": "skills/ghost"}], tmp)
        assert report["ok"] is False, report
        assert "SKILL.md missing" in " ".join(p["reason"] for p in report["problems"]), report

        # native 模式必须有 handler.py，否则执行层会在运行时才炸。
        make_skill(tmp, "nat", "name: nat\nmode: native\ntriggers: [nat]")
        report = integrity.compatibility(
            {"skills": {}}, [{"id": "nat", "name": "nat", "mode": "native", "path": "skills/nat"}], tmp)
        assert report["ok"] is False, report
        assert "handler.py" in " ".join(p["reason"] for p in report["problems"]), report

        # 三者一致时不得报警——否则对账本身就是噪音。
        make_skill(tmp, "ok", "name: ok\ntriggers: [ok]")
        report = integrity.compatibility(
            {"skills": {"ok": {}}}, [{"id": "ok", "name": "ok", "mode": "llm", "path": "skills/ok"}], tmp)
        assert report["ok"] is True, report
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_minimal_skill_end_to_end_smoke():
    """最小 skill 的真实端到端冒烟：discover → route → 提案 → 批准 → 复 pin → 校验。

    验收铁律：壳层"就绪"不能只看单测数字。真实用户丢进来的往往是最小的 skill
    ——只有 name + description + triggers，没有 domain / version / scope / mode。
    而单测样本几乎都带了这些字段，契约层的隐性强制项会因此漏测（历史上 domain
    就曾强制非空，把所有只带 triggers 的最小 skill 挡在门外）。
    """
    tmp = make_suite_root()
    try:
        # 刻意只给官方规范要求的两个字段（name / description）+ 本套件要求的主召回词 triggers。
        make_skill(
            tmp,
            "minimal",
            "name: minimal\n"
            "description: 当用户需要核对门店日结对账单、比对收银流水时使用\n"
            "triggers: [对账, 日结]",
        )
        s = Suite(tmp)

        actions = s.discover()
        assert ("minimal", "registered", "user_drop") in actions, actions

        entry = s.registry.get("minimal")
        assert entry["domain"] == [], "domain 缺失必须注入空列表，而非强制非空"
        assert entry["scope"] == "*" and entry["mode"] == "llm", entry
        assert entry["version_pin"] == "0.0.0", "version 缺失必须有兜底"
        assert entry["enabled"] is True

        # 路由必须命中，且 trace 同时留下 route 与 skill.invoke
        got = s.route("帮我核一下今天的日结对账")
        assert got["routed"] == "direct", got
        assert got["result"]["skill_id"] == "minimal", got
        from core.audit import replay
        kinds = [e["event"] for e in replay(got["trace_id"], root=tmp)]
        assert kinds.count("route") == 1 and kinds.count("skill.invoke") == 1, kinds

        # 提案 → 批准 → 执行：写回结构化字段并复 pin，完整性仍自洽
        mod = s.modify_skill("minimal", {"description": "改写后的描述，用于验证批准链路"})
        assert mod["allowed"] is False and mod["proposal_id"]
        assert s.approve_proposal(mod["proposal_id"])["state"] == "approved"
        assert s.registry.get("minimal")["description"].startswith("改写后的描述")
        assert integrity.verify(s.manifest, tmp)["ok"] is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_executor_native_opt_in():
    tmp = make_suite_root()
    try:
        make_skill(tmp, "ok", "name: OK\ntriggers: [报表]", handler=handler_source("ok", ["报表"]))
        e = entry("ok", ["报表"], mode="native", path=os.path.join("skills", "ok"))
        # 默认允许原生执行
        assert executor.invoke_one(e, "查报表", root=tmp)["skill"] == "ok"
        # 显式关闭 → 拒绝执行未知 handler.py，不静默跑代码
        expect_raises(lambda: executor.invoke_one(e, "查报表", root=tmp, allow_native=False), "disabled")
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
