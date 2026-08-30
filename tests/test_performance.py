"""性能与超时治理回归测试。

锁死三件事，防止"过渡实现降低性能"在后续改动中回来：
1. parallel 必须真并发（此前是 for 循环串行，耗时 3.01s 而非约 1s）。
2. 单个慢 skill 必须被超时隔离，不能拖垮整批。
3. 整批必须有时间预算封顶，否则 N 个超时会叠加成 N × timeout 的长尾。
"""

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from _harness import setup  # noqa: E402

setup()

from core import executor  # noqa: E402
from core.resolver import resolve  # noqa: E402
from suite import Suite  # noqa: E402


def make_suite_root():
    tmp = tempfile.mkdtemp(prefix="skill-perf-")
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


def slow_handler(sid, seconds):
    return "\n".join([
        "import time",
        "",
        "",
        "def describe():",
        "    return {'id': '%s'}" % sid,
        "",
        "",
        "def can_handle(query):",
        "    return 1",
        "",
        "",
        "def invoke(data):",
        "    time.sleep(%r)" % seconds,
        "    return {'ok': True, 'skill': '%s'}" % sid,
        "",
        "",
        "def health():",
        "    return {'ok': True}",
        "",
    ])


def build(root, count, seconds, prefix="slow"):
    for i in range(1, count + 1):
        sid = "%s%d" % (prefix, i)
        make_skill(
            root, sid,
            "name: %s\ndescription: 用于慢速批处理任务的技能 %s\ntriggers: [慢速]" % (sid, sid),
            handler=slow_handler(sid, seconds),
        )


def test_parallel_is_actually_concurrent():
    """3 个各耗时 1s 的 skill 并发执行，总耗时应接近 1s 而非 3s。"""
    tmp = make_suite_root()
    try:
        build(tmp, 3, 1.0)
        suite = Suite(tmp)
        suite.discover()
        picked = resolve(suite.registry.enabled(), "慢速")
        assert len(picked) == 3, picked

        start = time.time()
        out = executor.parallel(picked, "慢速", root=tmp)
        elapsed = time.time() - start

        assert set(out) == {"slow1", "slow2", "slow3"}, out
        assert all(v.get("skill") for v in out.values()), out
        assert elapsed < 2.0, "parallel 耗时 %.2fs，疑似退化为串行（并发应约 1s）" % elapsed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parallel_timeout_isolates_slow_skill():
    """单个远超时的 skill 不得拖垮整批。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "fast", "name: fast\ndescription: 用于快速处理的技能\ntriggers: [快速]",
                   handler=slow_handler("fast", 0.0))
        make_skill(tmp, "stuck", "name: stuck\ndescription: 用于卡住的超慢技能\ntriggers: [卡住]",
                   handler=slow_handler("stuck", 30.0))
        suite = Suite(tmp)
        suite.discover()
        picked = resolve(suite.registry.enabled(), "卡住")

        start = time.time()
        out = executor.parallel(picked, "卡住", root=tmp, timeout=0.5, overall_timeout=3.0)
        elapsed = time.time() - start

        assert out["stuck"].get("error") is True, out
        assert "timeout" in out["stuck"].get("reason", ""), out
        assert elapsed < 3.0, "慢 skill 未被隔离，耗时 %.2fs" % elapsed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parallel_overall_deadline_caps_total_time():
    """整体预算封顶：串行执行时，超预算的后续项直接失败而非逐个等待。"""
    tmp = make_suite_root()
    try:
        build(tmp, 4, 0.6)
        suite = Suite(tmp)
        suite.discover()
        picked = resolve(suite.registry.enabled(), "慢速")

        start = time.time()
        # max_workers=1 强制串行：每项 0.6s，4 项本需 2.4s；预算 1.0s 应在中途截断。
        out = executor.parallel(picked, "慢速", root=tmp, max_workers=1, timeout=5.0, overall_timeout=1.0)
        elapsed = time.time() - start

        assert len(out) == 4, out
        failed = [k for k, v in out.items() if v.get("error")]
        assert failed, "整体预算未生效，全部调用都完成了：%s" % out
        assert elapsed < 2.0, "整体预算未封顶，耗时 %.2fs" % elapsed
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parallel_preserves_request_order():
    """结果须按请求顺序返回——乱序回填会破坏调用方上下文一致性。"""
    tmp = make_suite_root()
    try:
        build(tmp, 3, 0.0)
        suite = Suite(tmp)
        suite.discover()
        order = ["slow3", "slow1", "slow2"]
        picked = [{"entry": suite.registry.get(sid)} for sid in order]
        out = executor.parallel(picked, "慢速", root=tmp)
        assert list(out.keys()) == order, "结果顺序与请求顺序不一致：%s" % list(out.keys())
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
