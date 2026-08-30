"""原子写回归测试。

锁死三件事：
  1. 正常写入后文件是完整 JSON，且不留临时文件；
  2. 序列化失败时**原文件毫发无损**——直接 open('w') 会先截断成 0 字节，
     路由表/完整性 pin 这类唯一事实源一旦写坏，整个套件不可用；
  3. 失败后不留临时文件，不让 .srs-tmp-* 垃圾堆积。

第 2 条是这个文件存在的理由：`open(path, "w")` 的截断发生在写入之前，
所以"写一半崩溃"只是最坏情况，"payload 里有不可序列化对象"就能日常触发。
"""

import json
import os
import shutil
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.atomic import write_json  # noqa: E402
from core.contract import normalize  # noqa: E402
from core.registry import Registry  # noqa: E402

GOOD = [{
    "id": "pms", "name": "pms", "triggers": ["报表"], "description": "查报表",
    "mode": "llm", "path": "skills/pms", "priority": 0, "scope": "*",
}]


def make_dir():
    return tempfile.mkdtemp(prefix="srs-atomic-")


def tmp_files(directory):
    return [n for n in os.listdir(directory) if n.startswith(".srs-tmp-")]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_write_json_roundtrip():
    d = make_dir()
    try:
        p = os.path.join(d, "skills.json")
        write_json(p, GOOD)
        assert load(p) == GOOD
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_write_json_creates_missing_parent_dirs():
    d = make_dir()
    try:
        p = os.path.join(d, "a", "b", "skills.json")
        write_json(p, GOOD)
        assert load(p) == GOOD
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_write_json_leaves_no_temp_files():
    d = make_dir()
    try:
        write_json(os.path.join(d, "x.json"), GOOD)
        assert tmp_files(d) == [], tmp_files(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unserializable_payload_keeps_original_intact():
    """核心保证：序列化失败时原文件一个字节都不能少。"""
    d = make_dir()
    try:
        p = os.path.join(d, "skills.json")
        write_json(p, GOOD)
        before = open(p, encoding="utf-8").read()
        try:
            write_json(p, [{"bad": object()}])
        except TypeError:
            pass
        else:
            raise AssertionError("object() 不可序列化，应抛 TypeError")
        after = open(p, encoding="utf-8").read()
        assert after == before, "原文件被破坏了"
        assert load(p) == GOOD, "原文件不再是有效 JSON"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unserializable_payload_leaves_no_temp_files():
    d = make_dir()
    try:
        p = os.path.join(d, "skills.json")
        write_json(p, GOOD)
        try:
            write_json(p, [{"bad": object()}])
        except TypeError:
            pass
        assert tmp_files(d) == [], tmp_files(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_registry_save_is_atomic():
    """路由表是唯一事实源：save 失败绝不能把已注册内容清空。"""
    d = make_dir()
    try:
        os.makedirs(os.path.join(d, "registry"))
        p = os.path.join(d, "registry", "skills.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump([normalize(dict(e)) for e in GOOD], f)
        reg = Registry(p)
        assert reg.get("pms") is not None
        reg.entries.append({"bad": object()})
        try:
            reg.save()
        except TypeError:
            pass
        reloaded = Registry(p)
        assert reloaded.get("pms") is not None, "save 失败后路由表丢失了已注册 skill"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_write_json_overwrites_cleanly():
    d = make_dir()
    try:
        p = os.path.join(d, "x.json")
        write_json(p, {"a": 1})
        write_json(p, {"b": 2})
        assert load(p) == {"b": 2}, "覆盖写入后内容不对"
        assert tmp_files(d) == []
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
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
