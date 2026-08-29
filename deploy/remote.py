"""远端适配（只读消费端）：Git 为分发真相源。只查询版本与拉取更新，绝不推送、绝不初始化本地仓库。"""

import os
import subprocess
from collections import namedtuple

from deploy import connectivity

Result = namedtuple("Result", ["ok", "out", "err"])


class RemoteStatus:
    CONFIGURED = "configured"
    NOT_CONFIGURED = "not-configured"
    NOT_A_REPO = "not-a-repo"
    GIT_MISSING = "git-missing"


class GitRemote:
    """只读消费者。发布（建仓 / commit / push）属作者端职责，不在本类能力范围内。"""

    def __init__(self, root, remote="origin", branch="main", remote_url=None, token_env=None,
                 accelerator_url=None, accelerator_source="ziyou"):
        self.root = root
        self.remote = remote
        self.branch = branch
        self.remote_url = remote_url
        self.token_env = token_env
        self.accelerator_url = accelerator_url
        self.accelerator_source = accelerator_source

    def run(self, args):
        command = ["git"]
        token_value = os.environ.get(self.token_env) if self.token_env else None
        if token_value:
            command += ["-c", "http.extraHeader=Authorization: Bearer " + token_value]
        try:
            proc = subprocess.run(command + args, cwd=self.root, capture_output=True, text=True)
        except FileNotFoundError:
            return Result(False, "", "git not installed")
        return Result(proc.returncode == 0, proc.stdout, proc.stderr)

    def state(self):
        probe = self.run(["rev-parse", "--is-inside-work-tree"])
        if not probe.ok:
            return RemoteStatus.GIT_MISSING if "git not installed" in probe.err else RemoteStatus.NOT_A_REPO
        if not self.run(["remote", "get-url", self.remote]).out.strip():
            return RemoteStatus.NOT_CONFIGURED
        return RemoteStatus.CONFIGURED

    def local_head(self):
        result = self.run(["rev-parse", "HEAD"])
        return result.out.strip() if result.ok else ""

    def remote_head(self):
        result = self.run(["ls-remote", self.remote, "refs/heads/" + self.branch])
        if not result.ok:
            return ""
        for line in result.out.splitlines():
            if line.strip():
                return line.split()[0]
        return ""

    def has_updates(self):
        local, head = self.local_head(), self.remote_head()
        if not local or not head:
            return None
        return local != head

    def pull(self):
        result = self.run(["pull", "--ff-only", self.remote, self.branch])
        if result.ok:
            return result
        if self._is_network_error(result.err) and self.accelerator_url:
            hosts = connectivity.fetch_hosts(self.accelerator_url, self.accelerator_source)
            if hosts:
                pr = connectivity.run_git_with_hosts(
                    self.root, ["pull", "--ff-only", self.remote, self.branch], hosts)
                if pr.returncode == 0:
                    return Result(True, pr.stdout, pr.stderr)
        return result

    @staticmethod
    def _is_network_error(err):
        err = (err or "").lower()
        keys = ("could not resolve", "connection", "timed out", "timeout",
                "failed to connect", "502", "503", "reset", "unreachable", "refused")
        return any(k in err for k in keys)


def from_manifest(root, manifest):
    config = manifest.get("deploy") or {}
    return GitRemote(
        root,
        remote=config.get("remote", "origin"),
        branch=config.get("branch", "main"),
        remote_url=config.get("remote_url") or None,
        token_env=config.get("token_env") or None,
        accelerator_url=config.get("accelerator_url") or connectivity.DEFAULT_ACCELERATOR,
        accelerator_source=config.get("accelerator_source", "ziyou"),
    )
