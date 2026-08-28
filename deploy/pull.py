"""用户端唯一职责：获取远端版本，并在启动或用户要求时拉取更新、热更新路由表。作者端发布不在本层。"""

from deploy import integrity
from deploy.remote import RemoteStatus, from_manifest


def remote_version(root, manifest=None, remote=None):
    remote = remote or from_manifest(root, manifest or {})
    state = remote.state()
    if state != RemoteStatus.CONFIGURED:
        return {"state": state, "local": remote.local_head() or None, "upstream": None, "has_updates": None}
    return {
        "state": state,
        "local": remote.local_head() or None,
        "upstream": remote.remote_head() or None,
        "has_updates": remote.has_updates(),
        "remote_url": remote.remote_url,
        "branch": remote.branch,
    }


def sync_before_use(root, registry, manifest=None, remote=None, force=False):
    remote = remote or from_manifest(root, manifest or {})
    state = remote.state()
    if state != RemoteStatus.CONFIGURED:
        return {"pulled": False, "reloaded": False, "reason": "remote " + state}

    updates = remote.has_updates()
    if updates is not True and not force:
        reason = "up to date" if updates is False else "remote version unknown"
        return {"pulled": False, "reloaded": False, "reason": reason}

    result = remote.pull()
    if not result.ok:
        return {"pulled": False, "reloaded": False, "reason": result.err.strip() or "pull failed"}

    registry.load()
    report = integrity.verify(manifest or {}, root)
    return {
        "pulled": True,
        "reloaded": True,
        "reason": "upstream updates applied" if report["ok"] else "upstream updates applied; integrity drift detected",
        "integrity": report,
    }
