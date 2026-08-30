"""部署层集成测试（用户端只读）：用本地裸仓库充当上游，验证 版本查询 / 拉取更新 / 热更新 / 完整性上报。

发布动作在本文件中一律由裸 git 命令模拟"作者端"，不经过套件框架——这正是职责分离的落点。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

from _harness import setup  # noqa: E402

setup()

from deploy import integrity  # noqa: E402
from deploy.pull import remote_version  # noqa: E402
from deploy.remote import GitRemote, RemoteStatus, from_manifest  # noqa: E402
from suite import Suite  # noqa: E402
from test_suite import make_skill, make_suite_root  # noqa: E402


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def make_upstream():
    path = tempfile.mkdtemp(prefix="skill-upstream-")
    assert git(["init", "--bare", "-b", "main", path], cwd=path).returncode == 0
    return path


def build_author_root(upstream):
    """作者工作副本：内容由套件生成，但发布走裸 git，不调用框架任何发布接口。"""
    root = make_suite_root()
    manifest_path = os.path.join(root, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["deploy"] = {
        "provider": "git",
        "mode": "read-only",
        "remote": "origin",
        "remote_url": upstream,
        "branch": "main",
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    git(["init", "-b", "main"], cwd=root)
    git(["config", "user.email", "author@example.local"], cwd=root)
    git(["config", "user.name", "Author"], cwd=root)
    git(["remote", "add", "origin", upstream], cwd=root)
    return root


def author_publish(source_root, message="chore: publish"):
    git(["add", "-A"], cwd=source_root)
    git(["commit", "-m", message], cwd=source_root)
    result = git(["push", "-u", "origin", "main"], cwd=source_root)
    assert result.returncode == 0, result.stderr


def clone_as_consumer(upstream):
    parent = tempfile.mkdtemp(prefix="skill-consumer-")
    target = os.path.join(parent, "suite")
    assert git(["clone", upstream, target], cwd=parent).returncode == 0
    return parent, Suite(target)


def test_consumer_pulls_upstream_update():
    upstream = author = consumer_parent = None
    try:
        upstream = make_upstream()
        author = build_author_root(upstream)
        make_skill(author, "report", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        s_author = Suite(author)
        # 用户丢入目录即授权：discover 是不经授权门的正规登记路径
        s_author.discover()
        integrity.pin(s_author.manifest, author)
        s_author.save()
        author_publish(author)

        consumer_parent, s = clone_as_consumer(upstream)
        assert {e["id"] for e in s.registry.all()} == {"report"}

        version = s.version()
        assert version["state"] == RemoteStatus.CONFIGURED
        assert version["has_updates"] is False
        assert s.sync()["pulled"] is False

        make_skill(author, "login", "name: 登录\ndomain: [pms]\ntriggers: [登录]\nversion: 1.0.0")
        s_author.discover()
        integrity.pin(s_author.manifest, author)
        s_author.save()
        author_publish(author, "chore: add login skill")

        assert s.version()["has_updates"] is True
        synced = s.sync()
        assert synced["pulled"] is True and synced["reloaded"] is True
        assert {e["id"] for e in s.registry.all()} == {"report", "login"}
        assert s.sync()["reason"] == "up to date"
    finally:
        for path in (upstream, author, consumer_parent):
            if path:
                shutil.rmtree(path, ignore_errors=True)


def test_integrity_drift_reported_after_pull():
    upstream = author = consumer_parent = None
    try:
        upstream = make_upstream()
        author = build_author_root(upstream)
        make_skill(author, "report", "name: 报表\ndomain: [pms]\ntriggers: [报表]\nversion: 1.0.0")
        s_author = Suite(author)
        # 用户丢入目录即授权：discover 是不经授权门的正规登记路径
        s_author.discover()
        integrity.pin(s_author.manifest, author)
        s_author.save()
        author_publish(author)

        consumer_parent, s = clone_as_consumer(upstream)
        clean = s.sync(force=True)
        assert clean["pulled"] is True
        assert clean["integrity"]["ok"] is True

        with open(os.path.join(s.root, "skills", "report", "SKILL.md"), "a", encoding="utf-8") as f:
            f.write("\ntampered\n")
        drifted = s.sync(force=True)
        assert drifted["integrity"]["ok"] is False
        assert drifted["integrity"]["drift"]
        assert "integrity drift detected" in drifted["reason"]
    finally:
        for path in (upstream, author, consumer_parent):
            if path:
                shutil.rmtree(path, ignore_errors=True)


def test_from_manifest_is_read_only():
    root = make_suite_root()
    try:
        manifest = {
            "deploy": {"provider": "git", "mode": "read-only", "remote": "origin", "branch": "main", "remote_url": ""}
        }
        remote = from_manifest(root, manifest)
        assert remote.branch == "main"
        assert not hasattr(remote, "commit") and not hasattr(remote, "push")
        assert not hasattr(remote, "bootstrap") and not hasattr(remote, "set_remote")

        assert remote_version(root, manifest)["state"] == RemoteStatus.NOT_A_REPO
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_accelerator_retries_defaults_to_twenty():
    root = make_suite_root()
    try:
        # 缺省落 20
        assert from_manifest(root, {"deploy": {"remote_url": ""}}).accelerator_retries == 20
        # manifest 显式 20
        assert from_manifest(root, {"deploy": {"remote_url": "", "accelerator_retries": 20}}).accelerator_retries == 20
        # 构造参数可覆盖（非缺省场景）
        assert GitRemote(root, accelerator_retries=7).accelerator_retries == 7
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_every_cli_command_is_classified_for_background_sync():
    """每条 CLI 子命令都必须被显式归类为「只读 / 变更 / 自带同步」三者之一。

    守的是**完备性**而不是取值本身：registry 是整表覆盖写，后台 pull 会
    `registry.load()` 覆盖内存副本，与变更命令并发时后写的一方抹掉对方的落盘结果。
    漏归类的新命令若默认放行同步，就把自己暴露在这个写丢失窗口里。

    用与 parser 实际子命令的**精确相等**来锁，新增/删除子命令都会立刻失败。
    """
    from scripts import cli

    parser = cli.build_parser()
    actual = None
    for action in parser._actions:
        if getattr(action, "dest", None) == "command":
            actual = set(action.choices)
    assert actual, "未从 parser 取到子命令列表"

    classified = set(cli.CLASSIFIED_COMMANDS)
    assert actual == classified, (
        "CLI 子命令与归类不一致：未归类=%s 已废弃=%s" % (sorted(actual - classified),
                                                    sorted(classified - actual)))

    # 只读命令必须真能同步（防止为躲并发把同步一刀切关掉）
    for cmd in cli.READ_ONLY_COMMANDS:
        assert cli.should_background_sync(cmd) is True, cmd
    # 变更命令必须串行（这是上面那条风险的正面断言）
    for cmd in cli.MUTATING_COMMANDS + cli.SELF_SYNCING_COMMANDS:
        assert cli.should_background_sync(cmd) is False, cmd


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
