"""三源注册管线：原样放入 skills/<id>/ → 读 SKILL.md 派生 entry → 刷路由表 → 打待部署标记。"""

import logging
import os

from deploy import integrity
from evolution import distiller, permissions

log = logging.getLogger("LeyaoSeedSkill.pipeline")

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
    # 闭合完整性链：注册即把 as-is 内容哈希写入 manifest，部署时比对防漂移。
    integrity.pin(manifest, root, [skill_id])
    log.info("register: %s from %s (version_pin=%s)", skill_id, source, stored["version_pin"])
    return stored


def unregister(registry, manifest, skill_id):
    removed = registry.remove(skill_id)
    if removed:
        manifest.get("skills", {}).pop(skill_id, None)
    return removed


def propose_modify(proposals, skill_id, changes):
    """改子 skill 内容需用户授权：返回提案，等用户批准。"""
    try:
        permissions.guard("modify_skill_content", proposals, {"skill_id": skill_id, "changes": changes})
    except permissions.AuthorizationRequired as exc:
        return {
            "allowed": False,
            "action": "modify_skill_content",
            "skill_id": skill_id,
            "rule": permissions.REQUIRES_AUTH,
            "proposal_id": exc.proposal_id,
        }
    return {"allowed": True, "action": "modify_skill_content", "skill_id": skill_id}


def propose_add(proposals, skill_id, source, rel_path=None, overrides=None):
    """增子 skill 需用户授权：返回提案，等用户批准。

    payload 必须带齐执行所需的全部参数——批准发生在另一个时刻（甚至另一个进程），
    届时无法再从调用栈里取回 source / rel_path / overrides。
    """
    if source not in SOURCES:
        raise ValueError("source must be one of %s" % list(SOURCES))
    try:
        permissions.guard("add_skill", proposals, {
            "skill_id": skill_id, "source": source,
            "rel_path": rel_path, "overrides": overrides,
        })
    except permissions.AuthorizationRequired as exc:
        return {
            "allowed": False,
            "action": "add_skill",
            "skill_id": skill_id,
            "rule": permissions.REQUIRES_AUTH,
            "proposal_id": exc.proposal_id,
        }
    return {"allowed": True, "action": "add_skill", "skill_id": skill_id}


def propose_remove(proposals, skill_id):
    """删子 skill 需用户授权：返回提案，等用户批准。

    提出时不校验 skill 是否存在——提案与批准之间状态可能变化，且"删一个不存在的东西"
    在批准时由 approve_proposal 明确报错，比提前在这里拒绝更好排查。
    """
    try:
        permissions.guard("remove_skill", proposals, {"skill_id": skill_id})
    except permissions.AuthorizationRequired as exc:
        return {
            "allowed": False,
            "action": "remove_skill",
            "skill_id": skill_id,
            "rule": permissions.REQUIRES_AUTH,
            "proposal_id": exc.proposal_id,
        }
    return {"allowed": True, "action": "remove_skill", "skill_id": skill_id}
