"""完整性校验：子 skill 目录 content-hash 入 manifest，比对以证明 as-is 未被破坏；兼三方一致性门禁。"""

import hashlib
import os

from core.frontmatter import parse_frontmatter

HASH_ALGORITHM = "sha256"

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules"}


def iter_files(root, rel_path):
    base = os.path.join(root, rel_path)
    if not os.path.isdir(base):
        return []
    collected = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            collected.append(os.path.relpath(os.path.join(dirpath, name), base))
    return sorted(collected)


def content_hash(root, rel_path):
    base = os.path.join(root, rel_path)
    digest = hashlib.new(HASH_ALGORITHM)
    for rel in iter_files(root, rel_path):
        digest.update(rel.encode("utf-8"))
        with open(os.path.join(base, rel), "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
    return digest.hexdigest()


def current_version(root, rel_path):
    """读子 skill 当前声明的 version。缺 SKILL.md 或未声明都返回空串。

    与 core/lint.py 一样从 core.frontmatter 取解析器（纯函数，无副作用），不引第三方 YAML 依赖。
    """
    path = os.path.join(root, rel_path, "SKILL.md")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            fm = parse_frontmatter(f.read())
    except OSError:
        return ""
    return str(fm.get("version") or "").strip()


def pin(manifest, root, skill_ids=None):
    skills = manifest.setdefault("skills", {})
    for skill_id in skill_ids or list(skills.keys()):
        # 正斜杠路径，跨平台一致（Windows 下 os.path.join 会写反斜杠）。
        rel = "skills/" + skill_id
        meta = skills.setdefault(skill_id, {})
        meta["content_hash"] = content_hash(root, rel)
        meta["path"] = rel
        # version_pin 必须与 hash 同步刷新。approve_proposal 落地变更后会复 pin，
        # 若此处不同步，manifest 里留的就是旧版本号，漂移报告反而成了误导。
        version = current_version(root, rel)
        if version:
            meta["version_pin"] = version
    return manifest


def verify(manifest, root):
    report = {"ok": True, "checked": 0, "missing": [], "drift": [], "version_drift": []}
    for skill_id, meta in (manifest.get("skills") or {}).items():
        rel = meta.get("path") or os.path.join("skills", skill_id)
        if not os.path.isdir(os.path.join(root, rel)):
            report["missing"].append(skill_id)
            report["ok"] = False
            continue
        report["checked"] += 1
        pinned = meta.get("content_hash")
        if not pinned:
            continue
        actual = content_hash(root, rel)
        if pinned != actual:
            report["drift"].append({"skill": skill_id, "expected": pinned, "actual": actual})
            report["ok"] = False
            # 版本漂移只作诊断，不改变结论：version 写在 SKILL.md 里，已被 content_hash
            # 完整覆盖，单独再判一次属于重复检测。它的价值在于让"改了什么"可读——
            # 「pms 从 1.0.0 变成 1.2.0」远比一串哈希有信息量。
            pinned_v = str(meta.get("version_pin") or "")
            actual_v = current_version(root, rel)
            if pinned_v and actual_v and pinned_v != actual_v:
                report["version_drift"].append(
                    {"skill": skill_id, "expected": pinned_v, "actual": actual_v})
    return report


def compatibility(manifest, entries, root):
    problems = []
    entry_ids = set()
    for entry in entries:
        entry_ids.add(entry["id"])
        rel = entry.get("path") or os.path.join("skills", entry["id"])
        if not os.path.exists(os.path.join(root, rel, "SKILL.md")):
            problems.append({"skill": entry["id"], "reason": "SKILL.md missing under %s" % rel})
        if entry.get("mode") == "native" and not os.path.exists(os.path.join(root, rel, "handler.py")):
            problems.append({"skill": entry["id"], "reason": "native mode requires handler.py under %s" % rel})
    for skill_id in manifest.get("skills") or {}:
        if skill_id not in entry_ids:
            problems.append({"skill": skill_id, "reason": "present in manifest but absent from registry"})
    return {"ok": not problems, "problems": problems}
