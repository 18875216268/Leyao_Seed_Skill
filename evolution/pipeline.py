"""三源注册管线：原样放入 skills/<id>/ → 读 SKILL.md 派生 entry → 刷路由表 → 打待部署标记。"""

import os

from evolution import distiller, permissions

SOURCES = ("user_create", "user_drop", "remote_pull")


def register(registry, manifest, skill_id, source, root, rel_path=None, overrides=None):
    if source not in SOURCES:
        raise ValueError("source must be one of %s" % list(SOURCES))
    entry = distiller.derive_entry(skill_id, root, rel_path, base=registry.get(skill_id))
    if overrides:
        entry.update(overrides)
    stored = registry.upsert(entry)
    manifest.setdefault("skills", {})[skill_id] = {
        "version_pin": stored["version_pin"],
        "mode": stored["mode"],
        "source": source,
    }
    return stored


def unregister(registry, manifest, skill_id):
    removed = registry.remove(skill_id)
    if removed:
        manifest.get("skills", {}).pop(skill_id, None)
    return removed


def propose_modify(proposals, skill_id, changes):
    try:
        permissions.guard("modify_skill_content", proposals, {"skill_id": skill_id, "changes": changes})
    except permissions.AuthorizationRequired as exc:
        return {
            "allowed": False,
            "action": "modify_skill_content",
            "skill_id": skill_id,
            "proposal_id": exc.proposal_id,
        }
    return {"allowed": True, "action": "modify_skill_content", "skill_id": skill_id}
