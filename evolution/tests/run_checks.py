#!/usr/bin/env python3
"""证环评分（自我进化层的客观输入）：结构完整性 + 一致性 + 冒烟。

输出 JSON（ok/passed/total/score/checks），退出码 0/1。
棘轮与回滚的唯一客观输入；语义类判定由 AI 在 review 时承担、用户终审。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]      # evolution/tests/run_checks.py → 框架根

sys.path.insert(0, str(ROOT / "evolution"))
import paths  # noqa: E402  （导入即初始化用户区；用户区路径的唯一事实源）
import store  # noqa: E402  （仅取 deep_merge 与运行态路径常量）

REQUIRED = [
    "SKILL.md", "manifest.json",
    "processor/PROCESSOR.md", "processor/control.md",
    "processor/flow/1-understand.md", "processor/flow/2-plan.md", "processor/flow/3-execute.md",
    "processor/flow/4-accept.md", "processor/flow/5-deliver.md",
    "processor/shapes.md",
    "library/ROUTES.md", "library/routes.json", "library/engine.py",
    "library/assets",
    "library/admin/README.md", "library/admin/console.py", "library/admin/server.py",
    "library/admin/pick_folder.py",
    "library/admin/web/index.html", "library/admin/web/app.js", "library/admin/web/style.css",
    "evolution/EVOLUTION.md", "evolution/store.py", "evolution/paths.py",
    "evolution/templates/memory.md", "evolution/templates/meta.json",
    "evolution/distiller.py", "evolution/gate.py", "evolution/actions.py", "evolution/grow.py",
    "evolution/tests/run_checks.py", "evolution/tests/README.md",
    "version/VERSION.md",
]

MEMORY_SECTIONS = ("失效模式", "有效做法", "待验证", "墓碑")

DOC_REF = re.compile(r"`((?:library|processor|evolution|version|state|tests)/[^`\s]*)`")
DOC_FILES = ("SKILL.md", "processor/*.md", "processor/flow/*.md", "evolution/*.md", "version/*.md",
             "library/ROUTES.md", "library/admin/README.md", "evolution/tests/README.md")
CMD_REF = re.compile(r"python\s+([\w./-]+\.py)")
CMD_SEG = re.compile(r"python\s+([\w./-]+\.py[^\n`]*)")


def doc_refs() -> list[str]:
    """框架文档中反引号引用的**框架内**路径必须真实存在（文档 ↔ 框架文件闭环；`<占位>` 跳过）。

    只管框架自身：不扫资产内容——框架不得依赖任何资产（资产缺失也必须自检全绿）。
    """
    bad = []
    for pat in DOC_FILES:
        for doc in sorted(ROOT.glob(pat)):
            if not doc.is_file():
                continue
            for line in doc.read_text(encoding="utf-8").splitlines():
                for ref in DOC_REF.findall(line):
                    if any(ch in ref for ch in "<>*{}…") or ref.endswith("..."):
                        continue
                    if not (ROOT / ref.rstrip("/")).exists():
                        bad.append("%s: %s" % (doc.relative_to(ROOT).as_posix(), ref))
    return bad


def doc_commands() -> list[str]:
    """框架文档里的 `python <脚本路径>` 必须指向真实脚本（文档 ↔ 代码闭环）。

    代码块里的命令不会被 doc_refs 的反引号规则覆盖，单独校验。
    """
    bad = []
    for pat in DOC_FILES:
        for doc in sorted(ROOT.glob(pat)):
            if not doc.is_file():
                continue
            for line in doc.read_text(encoding="utf-8").splitlines():
                for ref in CMD_REF.findall(line):
                    if not (ROOT / ref).exists():
                        bad.append("%s: %s" % (doc.relative_to(ROOT).as_posix(), ref))
    return bad


def doc_cli_args() -> list[str]:
    """文档里的**子命令与 --flag** 必须是脚本真实支持的（文档 ↔ CLI 闭环）。

    与 doc_commands 互补：那个只管「脚本存在」，这里管「子命令 / 参数是否存在」——
    改名、删参数后文档不会静默失真。用 ast 解析 add_parser / add_argument，零第三方依赖。
    """
    import ast as _ast

    opts, subs = {}, {}
    for p in ROOT.rglob("*.py"):
        if "assets" in p.parts:
            continue
        try:
            tree = _ast.parse(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        o, s = set(), set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute):
                if n.func.attr == "add_argument":
                    for a in n.args:
                        if isinstance(a, _ast.Constant) and isinstance(a.value, str) and a.value.startswith("--"):
                            o.add(a.value)
                elif n.func.attr == "add_parser" and n.args and isinstance(n.args[0], _ast.Constant):
                    s.add(n.args[0].value)
        if o or s:
            opts[p.name], subs[p.name] = o, s

    bad = []
    for pat in DOC_FILES:
        for doc in sorted(ROOT.glob(pat)):
            if not doc.is_file():
                continue
            rel = doc.relative_to(ROOT).as_posix()
            for line in doc.read_text(encoding="utf-8").splitlines():
                for seg in CMD_SEG.findall(line):
                    seg = seg.split("#", 1)[0].strip()          # 去掉行尾注释
                    script = Path(seg.split()[0]).name
                    if script not in opts and script not in subs:
                        continue                                  # 脚本不存在由 doc_commands 报
                    toks = seg.split()[1:]
                    if subs.get(script):
                        first = next((t for t in toks if not t.startswith("-")), None)
                        if first and not first.startswith("<") and first not in subs[script]:
                            bad.append("%s: %s 无子命令 %s" % (rel, script, first))
                    for fl in sorted(set(re.findall(r"(--[\w-]+)", seg))):
                        if fl not in opts.get(script, set()):
                            bad.append("%s: %s 无参数 %s" % (rel, script, fl))
    return bad


def check(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def iter_nodes(nodes):
    for n in nodes:
        yield n
        yield from iter_nodes(n.get("children") or [])


def _engine():
    """惰性导入资产层引擎（复用唯一实现；导入失败由调用方 try 捕获）。"""
    sys.path.insert(0, str(ROOT / "library"))
    import engine  # noqa: E402
    return engine


def main() -> int:
    checks = []
    manifest = {}        # 供 manifest_layers / root_layout 共用（前项失败时后项不得引用未绑定名）
    nodes = []           # 供 routes_integrity / routes_described 共用

    missing = [f for f in REQUIRED if not (ROOT / f).exists()]
    checks.append(check("required_files", not missing,
                        "缺失: %s" % missing if missing else "%d 个必需文件齐全" % len(REQUIRED)))

    try:
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        ids = sorted(c["id"] for c in manifest.get("layers", []))
        checks.append(check("manifest_layers",
                            ids == ["evolution", "library", "main", "processor", "version"], ",".join(ids)))
    except Exception as exc:
        checks.append(check("manifest_layers", False, str(exc)))

    try:
        allowed = {c["path"].rstrip("/") for c in manifest.get("layers", [])}
        stray = sorted(d.name for d in ROOT.iterdir()
                       if d.is_dir() and not d.name.startswith(".") and d.name not in allowed)
        checks.append(check("root_layout", not stray,
                            "游离目录（未登记）: %s" % stray if stray else "根目录仅含 manifest 声明的层级"))
    except Exception as exc:
        checks.append(check("root_layout", False, str(exc)))

    try:
        routes = json.loads((ROOT / "library" / "routes.json").read_text(encoding="utf-8"))
        nodes = list(iter_nodes(routes.get("nodes", [])))
    except Exception:
        nodes = []

    try:
        engine = _engine()
        issues = engine.validate(engine.load(), ROOT)
        _hints = engine.hints(engine.load(), ROOT)
        checks.append(check("routes_contract", not issues,
                            "契约问题: %s" % issues if issues
                            else "挂载存在 · id 唯一（入口文档缺失 %d 项 → 软提示，不判失败）" % len(_hints)))
    except Exception as exc:
        checks.append(check("routes_contract", False, str(exc)))

    try:
        undesc = [n.get("id") for n in nodes if not (n.get("description") or "").strip()]
        checks.append(check("routes_described", not undesc,
                            "缺适用场景描述（AI 无法路由）: %s" % undesc if undesc
                            else "%d 个节点均有适用场景描述" % len(nodes)))
    except Exception as exc:
        checks.append(check("routes_described", False, str(exc)))

    try:
        memory = paths.MEMORY_F.read_text(encoding="utf-8")
        missing_sections = [s for s in MEMORY_SECTIONS if ("## " + s) not in memory]
        checks.append(check("memory_sections", not missing_sections,
                            "缺段: %s" % missing_sections if missing_sections else "四段齐备（用户区记忆）"))
    except Exception as exc:
        checks.append(check("memory_sections", False, str(exc)))

    try:
        sections = ("输入", "动作", "出口判据", "红旗", "引导")
        bad = []
        for f in sorted((ROOT / "processor" / "flow").glob("*.md")):
            text = f.read_text(encoding="utf-8")
            missing = [s for s in sections if ("## " + s) not in text]
            if missing:
                bad.append("%s 缺 %s" % (f.name, missing))
                continue
            if text.index("## 出口判据") > text.index("## 动作"):
                bad.append("%s 出口判据须排在动作之前（先定验收目标再讲做法）" % f.name)
        if "## 判据分级" not in (ROOT / "processor" / "PROCESSOR.md").read_text(encoding="utf-8"):
            bad.append("processor/PROCESSOR.md 缺「判据分级」")
        checks.append(check("processor_sections", not bad,
                            "；".join(bad) if bad else "五步 flow 五段齐备 · 出口判据前置 · 判据分级在场"))
    except Exception as exc:
        checks.append(check("processor_sections", False, str(exc)))

    try:
        # Agent Skills 官方规范的本地回归护栏（对应 skills-ref validate 的字段/命名两条硬规则）：
        # 字段白名单、name 为小写 kebab-case、且 name 必须等于目录名。只做标准库解析，不引第三方依赖。
        fm = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        keys = [ln.split(":", 1)[0].strip() for ln in fm.splitlines()
                if ln.strip() and not ln[0].isspace()]
        allowed = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
        name = ""
        for ln in fm.splitlines():
            if ln.startswith("name:"):
                name = ln.split(":", 1)[1].strip().strip('"').strip("'")
        probs = []
        extra = sorted(set(keys) - allowed)
        if extra:
            probs.append("非白名单字段 %s（官方只允许 %s）" % (extra, sorted(allowed)))
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name or ""):
            probs.append("name %r 必须是小写 kebab-case" % name)
        if name != ROOT.name:
            probs.append("name %r 必须等于目录名 %r（官方 skills-ref 硬规则）" % (name, ROOT.name))
        checks.append(check("skill_frontmatter", not probs,
                            "；".join(probs) if probs else
                            "字段白名单 · name=%s 为 kebab-case 且等于目录名" % name))
    except Exception as exc:
        checks.append(check("skill_frontmatter", False, str(exc)))

    try:
        config = store.load_json(paths.CONFIG_F, {})
        ready = (paths.HOME.is_dir() and paths.MEMORY_F.exists()
                 and isinstance(config.get("maintainer"), bool))
        checks.append(check("user_area", ready,
                            "用户区就绪：%s（role=%s）" % (paths.HOME, "maintainer" if config.get("maintainer") else "user")))
    except Exception as exc:
        checks.append(check("user_area", False, str(exc)))

    try:
        leftovers = [p.relative_to(ROOT).as_posix() for p in
                     (ROOT / "evolution" / "state", ROOT / "library" / ".memory.md",
                      ROOT / "evolution" / "meta.json", ROOT / "evolution" / "tests" / "trigger_results.json")
                     if p.exists()]
        checks.append(check("paths_external", not leftovers,
                            ("包内不应有运行态：%s" % leftovers) if leftovers
                            else "运行态只存用户区（.leyao-data/），包内零残留"))
    except Exception as exc:
        checks.append(check("paths_external", False, str(exc)))

    try:
        meta = store.deep_merge(store.load_json(paths.TPL_META, {}),
                                store.load_json(paths.META_F, {}))   # 模板 ⊕ 变更集
        th = meta.get("thresholds", {})
        sane = (th.get("min_support", 0) >= 1 and th.get("observation", 0) >= 1
                and th.get("demote", 0) >= 1 and th.get("retire", 0) >= 1)
        checks.append(check("meta_sanity", sane, json.dumps(th, ensure_ascii=False)))
    except Exception as exc:
        checks.append(check("meta_sanity", False, str(exc)))

    try:
        front = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        skill_ver = next((ln.split(":", 1)[1].strip().strip('"').strip("'")
                          for ln in front.splitlines() if ln.strip().startswith("version:")), "")
        manifest_ver = str(manifest.get("version", ""))
        checks.append(check("version_sync", bool(manifest_ver) and manifest_ver == skill_ver,
                            "manifest=%s · SKILL.md=%s" % (manifest_ver, skill_ver)))
    except Exception as exc:
        checks.append(check("version_sync", False, str(exc)))

    try:
        if not paths.VERSIONS_F.exists():
            # 版本记录由落地器在首次更新时生成；未生成同样计入项数（覆盖率恒定的同一约定）
            checks.append(check("versions_shape", True, "版本记录尚未生成，跳过（计入项数，保持覆盖恒定）"))
        else:
            rec = json.loads(paths.VERSIONS_F.read_text(encoding="utf-8"))
            hist = rec.get("history")
            ok = (isinstance(rec.get("local"), dict) and isinstance(rec.get("baseline"), dict)
                  and isinstance(hist, list) and len(hist) <= 10
                  and all(isinstance(h, dict) and h.get("version") and h.get("date") for h in hist))
            checks.append(check("versions_shape", ok,
                                "结构合法（local / history≤10 / baseline）" if ok
                                else "结构不合法：%s" % json.dumps(rec, ensure_ascii=False)[:160]))
    except Exception as exc:
        checks.append(check("versions_shape", False, str(exc)))

    try:
        engine = _engine()
        expected = engine.render(engine.load())
        actual = (ROOT / "library" / "ROUTES.md").read_text(encoding="utf-8")
        checks.append(check("routes_render", expected == actual,
                            "ROUTES.md 与 routes.json 一致（引擎渲染产物，未手工编辑）" if expected == actual
                            else "ROUTES.md 与 routes.json 不一致：禁止手工编辑，请跑 python library/engine.py 重绘"))
    except Exception as exc:
        checks.append(check("routes_render", False, str(exc)))

    bad_refs = doc_refs()
    checks.append(check("doc_refs", not bad_refs,
                        "失效引用: %s" % bad_refs if bad_refs else "文档引用路径全部可达"))

    bad_cmds = doc_commands()
    checks.append(check("doc_commands", not bad_cmds,
                        "失效命令: %s" % bad_cmds if bad_cmds else "文档命令全部指向真实脚本"))

    bad_cli = doc_cli_args()
    checks.append(check("doc_cli_args", not bad_cli,
                        "失效子命令/参数: %s" % bad_cli if bad_cli else "文档子命令与 --参数全部真实存在"))

    for name, path in (("library/routes.json", ROOT / "library" / "routes.json"),
                       ("meta.json", paths.META_F),
                       ("traces.json", store.TRACES_F),
                       ("experience.json", store.EXP_F),
                       ("ratchet.json", store.RATCHET_F)):
        if not path.exists():
            # 运行时文件尚未生成时不跳过、而是计入并标注：否则检查项数会随运行状态静默变化，
            # 覆盖率名义 1.0 却在缩水（沙箱与线上会数出不同的 total）。
            checks.append(check("parse:" + name, True, "运行时文件尚未生成，跳过解析（计入项数，保持覆盖恒定）"))
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            checks.append(check("parse:" + name, True))
        except Exception as exc:
            checks.append(check("parse:" + name, False, str(exc)))

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    result = {"ok": passed == total, "passed": passed, "total": total,
              "score": round(passed / total, 4) if total else 0.0, "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
