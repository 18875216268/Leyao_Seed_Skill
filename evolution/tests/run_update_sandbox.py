#!/usr/bin/env python3
"""更新链路回归（零联网沙箱）：把包与用户区都复制到临时目录，全链路演练版本维护层的落地器。

覆盖（对应 `version/VERSION.md` 与 `evolution/actions.py` 的 framework_update）：
  1. 干净更新（含本地新增文件）：覆盖 / 新增 / 删除数量、备份、版本记录、提案出队、更新后自检
  2. 本地偏离 + 本地新增：逐文件检出、备份内容逐字节等于覆盖前原文
  3. 证环不过 → 整体回滚 ×2（staging 破坏 routes.json / 只改 manifest 触发 version_sync 失配）
  4. 契约拒收 ×5（staging 不存在 / 版本不一致 / 缺 manifest / 包自身 / 包内目录）
  5. 门禁回归（非维护者 route_update 仍被拒）+ 终检

版本链由包内当前版本动态推导（x.y.z → x.y.z+1 …），**发版后无需改测试**。

安全保证：全程不触网、不写真实包与真实用户区（子进程用 LEYAO_SEED_HOME 指向沙箱）；
通过后自动清理沙箱，失败则保留目录并把路径写进 JSON 汇总（便于取证）。

用法：
  python evolution/tests/run_update_sandbox.py            # 跑完自动清理
  python evolution/tests/run_update_sandbox.py --keep     # 保留沙箱目录（排障用）

输出：过程逐条 PASS/FAIL，末尾一段 JSON 汇总（ok / passed / total / sandbox），退出码 0/1。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]      # evolution/tests/run_update_sandbox.py → 框架根
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".leyao-data")

VER: dict = {}      # main() 按包内当前版本填充：base / v1 / v2 / v3 / v4


def version_chain(version: str) -> dict:
    """由包内当前版本推导本次回归的版本链：x.y.z → x.y.(z+1) …（发版后无需改测试）。"""
    parts = [int(x) for x in version.split(".")]

    def at(n: int) -> str:
        p = list(parts)
        p[-1] += n
        return ".".join(str(x) for x in p)

    return {"base": version, "v1": at(1), "v2": at(2), "v3": at(3), "v4": at(4)}


def sha1(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run(args, cwd: Path, home: Path):
    env = {**os.environ, "LEYAO_SEED_HOME": str(home), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, *args], cwd=str(cwd), env=env,
                       capture_output=True, timeout=300)
    txt = p.stdout.decode("utf-8", "replace")
    try:
        data = json.loads(txt)
    except Exception:
        data = None
    return p.returncode, data, txt, p.stderr.decode("utf-8", "replace")


def bump(stage: Path, old: str, new: str, targets=("manifest.json", "SKILL.md")) -> None:
    """把 staging 的版本号从 old 提到 new（默认两处同改，保证 version_sync 通过）。"""
    for name in targets:
        f = stage / name
        text = f.read_text(encoding="utf-8")
        assert '"%s"' % old in text, "版本号 %s 未出现在 %s" % (old, name)
        f.write_text(text.replace('"%s"' % old, '"%s"' % new), encoding="utf-8")


class Test:
    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="leyao_sb_"))
        self.home = self.base / "home"
        self.home.mkdir(parents=True)
        self.skill = self.base / "sb" / "leyao-seed-core"
        shutil.copytree(ROOT, self.skill, ignore=IGNORE)
        self.items: list = []

    def ok(self, name: str, cond, detail="") -> bool:
        cond = bool(cond)
        line = ("PASS  " if cond else "FAIL  ") + name
        if detail not in ("", None):
            line += "  | " + str(detail)[:300]
        print(line)
        self.items.append({"name": name, "ok": cond, "detail": str(detail)[:300]})
        return cond

    def checks(self, tag: str) -> dict:
        rc, data, txt, err = run(["evolution/tests/run_checks.py"], self.skill, self.home)
        data = data or {"ok": False, "raw": (txt[-200:] + err[-200:])}
        self.ok("%s 自检 22/22" % tag, data.get("ok") and data.get("total") == 22,
                "%s/%s" % (data.get("passed"), data.get("total")))
        return data

    def versions(self) -> dict:
        return json.loads((self.home / "data" / "versions.json").read_text(encoding="utf-8"))

    def proposals(self) -> list:
        d = self.home / "data" / "state" / "proposals"
        return sorted(p.name for p in d.glob("p_*.json")) if d.exists() else []

    def audit(self) -> str:
        f = self.home / "data" / "state" / "audit.log"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def propose_apply(self, staging: Path, version: str, summary=""):
        payload = {"staging": str(staging), "version": version}
        if summary:
            payload["summary"] = summary
        rc, res, txt, err = run(["evolution/grow.py", "propose", "--kind", "framework_update",
                                 "--payload", json.dumps(payload)], self.skill, self.home)
        if not (rc == 0 and res and res.get("ok")):
            return rc, None, (txt[-200:] + err[-200:])
        rc, res, txt, err = run(["evolution/grow.py", "apply", "--id", res["proposal"]], self.skill, self.home)
        return rc, res, (txt[-200:] + err[-200:])


def scenario_clean_update(t: Test) -> None:
    """T1 干净更新 base → v1（含本地新增文件）。"""
    rel = t.base / "staging1"
    shutil.copytree(t.skill, rel, ignore=IGNORE)
    bump(rel, VER["base"], VER["v1"])
    with (rel / "library" / "admin" / "README.md").open("a", encoding="utf-8") as fh:
        fh.write("\n- [%s] 沙箱发布说明。\n" % VER["v1"])
    (rel / "library" / "admin" / "web" / "_probe.txt").write_text("probe\n", encoding="utf-8")
    local_note, local_extra = t.skill / "_local_note.md", t.skill / "library" / "_local_extra.md"
    local_note.write_text("本地随手记\n", encoding="utf-8")
    local_extra.write_text("本地实验文件\n", encoding="utf-8")

    rc, res, err = t.propose_apply(rel, VER["v1"], "沙箱更新 1")
    t.ok("T1 apply 成功", rc == 0 and res and res.get("ok") is True, err)
    if not (res and res.get("ok")):
        return
    t.ok("T1 版本号", res.get("version") == VER["v1"], res.get("version"))
    t.ok("T1 新增 1 / 删除 2（本地新增）", res.get("added") == 1 and res.get("removed") == 2,
         "%s/%s" % (res.get("added"), res.get("removed")))
    t.ok("T1 首轮无偏离（无基线）", res.get("deviations") == [], res.get("deviations"))
    t.ok("T1 本地新增清单", res.get("additions") == ["_local_note.md", "library/_local_extra.md"],
         res.get("additions"))
    t.ok("T1 已备份（非静默）", bool(res.get("backup")), res.get("backup"))
    t.ok("T1 版本记录已写", res.get("record_written") is True)
    t.ok("T1 本地新增已删除", not local_note.exists() and not local_extra.exists())
    t.ok("T1 新版文件已落地", (t.skill / "library" / "admin" / "web" / "_probe.txt").exists())
    t.ok("T1 manifest 与 staging 一致", sha1(t.skill / "manifest.json") == sha1(rel / "manifest.json"))
    t.ok("T1 SKILL.md 与 staging 一致", sha1(t.skill / "SKILL.md") == sha1(rel / "SKILL.md"))
    v = t.versions()
    t.ok("T1 versions.local=%s" % VER["v1"], v.get("local", {}).get("version") == VER["v1"], v.get("local"))
    t.ok("T1 history=1 条", len(v.get("history", [])) == 1, v.get("history"))
    t.ok("T1 baseline 含 manifest 且哈希正确",
         v.get("baseline", {}).get("manifest.json") == sha1(t.skill / "manifest.json"))
    t.ok("T1 baseline 不含本地新增", "_local_note.md" not in v.get("baseline", {}))
    t.ok("T1 提案已出队", t.proposals() == [])
    c = t.checks("T1 更新后")
    t.ok("T1 自检版本同步 %s" % VER["v1"],
         any(x["name"] == "version_sync" and VER["v1"] in x["detail"] and x["ok"] for x in c.get("checks", [])))
    t.ok("T1 版本记录结构合法",
         any(x["name"] == "versions_shape" and x["ok"] for x in c.get("checks", [])))
    t.ok("T1 审计含 propose/apply",
         '"event": "propose"' in t.audit() and '"event": "apply"' in t.audit())


def scenario_deviation(t: Test, rel: Path) -> Path:
    """T2 本地偏离检测 v1 → v2；返回发布树（供后续场景派生）。"""
    readme = t.skill / "library" / "admin" / "README.md"
    local_readme = readme.read_text(encoding="utf-8") + "\n本地实验行：只有本机有\n"
    readme.write_text(local_readme, encoding="utf-8")
    (t.skill / "library" / "_local_extra2.md").write_text("本地实验文件 2\n", encoding="utf-8")

    rel2 = t.base / "staging2"
    shutil.copytree(rel, rel2, ignore=IGNORE)      # 从发布树派生，不带本地实验行
    bump(rel2, VER["v1"], VER["v2"])
    with (rel2 / "library" / "admin" / "README.md").open("a", encoding="utf-8") as fh:
        fh.write("\n- [%s] 沙箱发布说明。\n" % VER["v2"])

    rc, res, err = t.propose_apply(rel2, VER["v2"], "沙箱更新 2")
    t.ok("T2 apply 成功", rc == 0 and res and res.get("ok") is True, err)
    if res and res.get("ok"):
        t.ok("T2 检出本地偏离", res.get("deviations") == ["library/admin/README.md"], res.get("deviations"))
        t.ok("T2 本地新增清单", res.get("additions") == ["library/_local_extra2.md"], res.get("additions"))
        t.ok("T2 已备份", bool(res.get("backup")), res.get("backup"))
        bak = t.home / res["backup"] / "library" / "admin" / "README.md"
        t.ok("T2 备份内容=偏离前原文", bak.exists() and bak.read_text(encoding="utf-8") == local_readme)
    v = t.versions()
    t.ok("T2 versions.local=%s" % VER["v2"], v.get("local", {}).get("version") == VER["v2"])
    t.ok("T2 history=2 条且新在前",
         len(v.get("history", [])) == 2 and v["history"][0]["version"] == VER["v2"], v.get("history"))
    t.ok("T2 本地新增已删除", not (t.skill / "library" / "_local_extra2.md").exists())
    t.ok("T2 README 已采用发布版", sha1(readme) == sha1(rel2 / "library" / "admin" / "README.md"))
    t.checks("T2 更新后")
    return rel2


def scenario_rollback_routes(t: Test, rel: Path) -> None:
    """T3a 证环不过 → 整体回滚（staging 破坏 routes.json）。"""
    rel3 = t.base / "staging3_bad_routes"
    shutil.copytree(rel, rel3, ignore=IGNORE)
    bump(rel3, VER["v2"], VER["v3"])
    (rel3 / "library" / "routes.json").write_text('{"nodes": [', encoding="utf-8")
    pre_routes = (t.skill / "library" / "routes.json").read_text(encoding="utf-8")
    pre_manifest = (t.skill / "manifest.json").read_text(encoding="utf-8")
    rc, res, err = t.propose_apply(rel3, VER["v3"])
    t.ok("T3a apply 拒绝且回滚",
         rc == 1 and res and res.get("ok") is False and res.get("rolled_back") is True,
         json.dumps(res, ensure_ascii=False)[:200] if res else err)
    t.ok("T3a routes.json 已还原",
         (t.skill / "library" / "routes.json").read_text(encoding="utf-8") == pre_routes)
    t.ok("T3a manifest 已还原", (t.skill / "manifest.json").read_text(encoding="utf-8") == pre_manifest)
    t.ok("T3a 版本记录未动", t.versions().get("local", {}).get("version") == VER["v2"])
    t.ok("T3a 提案已出队", t.proposals() == [])
    t.ok("T3a 审计含回滚", '"event": "apply.rollback"' in t.audit())
    t.checks("T3a 回滚后")


def scenario_rollback_version_skew(t: Test, rel: Path, pre_manifest: str) -> None:
    """T3b 证环不过 → 整体回滚（只改 manifest → version_sync 失配）。"""
    rel4 = t.base / "staging4_version_skew"
    shutil.copytree(rel, rel4, ignore=IGNORE)
    bump(rel4, VER["v2"], VER["v4"], targets=("manifest.json",))     # 故意只改一半
    rc, res, err = t.propose_apply(rel4, VER["v4"])
    t.ok("T3b apply 拒绝且回滚",
         rc == 1 and res and res.get("ok") is False and res.get("rolled_back") is True,
         json.dumps(res, ensure_ascii=False)[:200] if res else err)
    t.ok("T3b version_sync 是拦截因",
         res and res.get("checks") and
         any(x["name"] == "version_sync" and not x["ok"] for x in res["checks"].get("checks", [])))
    t.ok("T3b manifest 已还原", (t.skill / "manifest.json").read_text(encoding="utf-8") == pre_manifest)
    t.ok("T3b 版本记录未动", t.versions().get("local", {}).get("version") == VER["v2"])
    t.ok("T3b 审计含两次回滚", t.audit().count('"event": "apply.rollback"') == 2)
    t.checks("T3b 回滚后")


def scenario_contract(t: Test, rel_skew: Path) -> None:
    """T4 契约拒收：propose 即拒，不留悬空提案、不污染包。"""

    def reject(name: str, payload: dict, kw: str) -> None:
        n0 = len(t.proposals())
        rc, res, txt, err = run(["evolution/grow.py", "propose", "--kind", "framework_update",
                                 "--payload", json.dumps(payload)], t.skill, t.home)
        msg = (res or {}).get("error", txt[-200:] + err[-200:])
        t.ok("T4 " + name,
             rc == 1 and res and res.get("ok") is False and kw in msg and len(t.proposals()) == n0,
             msg[:200])

    reject("staging 不存在", {"staging": str(t.base / "nope"), "version": "9.9.9"}, "存在的目录")
    reject("版本不一致", {"staging": str(rel_skew), "version": "9.9.9"}, "不一致")
    empty = t.base / "emptydir"
    empty.mkdir()
    reject("缺 manifest", {"staging": str(empty), "version": "9.9.9"}, "缺合法 manifest.json")
    reject("staging=当前包自身", {"staging": str(t.skill), "version": VER["v2"]}, "自身")
    inside = t.skill / "library" / "_sub_test"
    inside.mkdir()
    shutil.copy2(t.skill / "manifest.json", inside / "manifest.json")
    for layer in ("processor", "library", "evolution", "version"):
        (inside / layer).mkdir()
    reject("staging=包内目录", {"staging": str(inside), "version": VER["v2"]}, "包内目录")
    shutil.rmtree(inside)
    t.checks("T4 拒收后")


def scenario_regression(t: Test) -> None:
    """T5 门禁与既有链路回归。"""
    rc, res, txt, err = run(["evolution/grow.py", "propose", "--kind", "route_update",
                             "--payload", json.dumps({"cmd": "list", "args": []})], t.skill, t.home)
    t.ok("T5 非维护者仍被 route_update 拒绝",
         rc == 1 and res and res.get("ok") is False and "非维护者实例" in (res.get("error") or ""),
         (res or {}).get("error"))
    rc, res, txt, err = run(["evolution/grow.py", "trace", "--task", "沙箱回归",
                             "--routed", "none", "--outcome", "success"], t.skill, t.home)
    t.ok("T5 trace 正常", rc == 0 and res and res.get("ok") is True)
    rc, res, txt, err = run(["evolution/grow.py", "status"], t.skill, t.home)
    t.ok("T5 status 正常且自检 ok",
         rc == 0 and res and res.get("ok") is True and res.get("checks", {}).get("ok") is True, err)
    rc, res, txt, err = run(["evolution/grow.py", "apply", "--id", "p_nonexistent"], t.skill, t.home)
    t.ok("T5 未知提案被拒", rc == 1 and res and res.get("ok") is False)


def main() -> int:
    ap = argparse.ArgumentParser(description="更新链路回归（零联网沙箱）")
    ap.add_argument("--keep", action="store_true", help="保留沙箱目录（排障用；默认通过后清理）")
    args = ap.parse_args()

    VER.update(version_chain(json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]))
    t = Test()
    print("sandbox = %s" % t.base)
    print("version chain = %s" % VER)
    t.checks("T0 基线")
    t.ok("T0 用户区尚无版本记录", not (t.home / "data" / "versions.json").exists())

    scenario_clean_update(t)
    rel2 = scenario_deviation(t, t.base / "staging1")
    pre_manifest = (t.skill / "manifest.json").read_text(encoding="utf-8")
    scenario_rollback_routes(t, rel2)
    scenario_rollback_version_skew(t, rel2, pre_manifest)
    scenario_contract(t, t.base / "staging4_version_skew")
    scenario_regression(t)
    t.checks("T6 终检")
    v = t.versions()
    t.ok("T6 版本记录终态",
         v.get("local", {}).get("version") == VER["v2"] and len(v.get("history", [])) == 2, v.get("local"))

    failed = [x["name"] for x in t.items if not x["ok"]]
    passed, total = len(t.items) - len(failed), len(t.items)
    result = {"ok": not failed, "passed": passed, "total": total, "checks": t.items,
              "sandbox": str(t.base), "kept": bool(args.keep or failed)}
    if failed or args.keep:
        print("\n沙箱保留：%s" % t.base)
    else:
        shutil.rmtree(t.base, ignore_errors=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
