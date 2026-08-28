"""完整性校验：子 skill 目录 content-hash 入 manifest，部署时比对，证 as-is 未被破坏；兼兼容门禁。"""

import hashlib
import os

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


def pin(manifest, root, skill_ids=None):
    skills = manifest.setdefault("skills", {})
    for skill_id in skill_ids or list(skills.keys()):
        rel = os.path.join("skills", skill_id)
        meta = skills.setdefault(skill_id, {})
        meta["content_hash"] = content_hash(root, rel)
        meta["path"] = rel
    return manifest


def verify(manifest, root):
    report = {"ok": True, "checked": 0, "missing": [], "drift": []}
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
