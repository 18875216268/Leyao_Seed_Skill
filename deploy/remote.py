"""远端适配（只读消费端）：Git 为分发真相源。只查询版本与拉取更新，绝不推送、绝不初始化本地仓库。"""

import logging
import os
import random
import subprocess
import time
from collections import namedtuple

from deploy import connectivity

log = logging.getLogger("LeyaoSeedSkill.remote")

Result = namedtuple("Result", ["ok", "out", "err"])

# pull 的重试语义必须分清两件事，混为一谈就会既慢又无效：
#
#   failover 换 IP —— 每次换一个新目标，本身不需要退避（换条路就试）。
#   retry 同一目标 —— 同一目标反复打，必须退避，否则是在锤一个正在故障的服务。
#
# 这里的循环是前者（每次向云函数重新取候选 IP），所以 accelerator_retries 可以保持较大；
# 真正缺的是**整体时间预算**：没有 deadline 时，21 次 × 单次 120s 超时 ≈ 42 分钟无上限阻塞。
#
# 依据：Google SRE「retry budget」（重试不得超过正常负载的固定比例，且须有 deadline 传播）、
# AWS「Exponential Backoff and Jitter」的 full jitter（固定 ±抖动仍留下同步波前）。
DEFAULT_OVERALL_DEADLINE = 180.0
DEFAULT_PULL_TIMEOUT = 120.0
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_CAP = 8.0

# 剩余预算低于此值时不再发起新尝试——git 握手都完不成，只会白等。
MIN_EFFECTIVE_TIMEOUT = 10.0

# 云函数连续返回空候选达到此数即熔断，避免对着一个坏掉的加速器空转。
CIRCUIT_THRESHOLD = 3


def backoff_delay(attempt, base=DEFAULT_BACKOFF_BASE, cap=DEFAULT_BACKOFF_CAP):
    """full jitter：延迟在 [0, min(cap, base × 2^attempt)] 上均匀采样。

    AWS 明确推荐 full jitter 而非"固定延迟 ± 抖动"——后者仍会形成同步的重试波前。
    """
    ceiling = min(cap, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0, ceiling)


class RemoteStatus:
    CONFIGURED = "configured"
    NOT_CONFIGURED = "not-configured"
    NOT_A_REPO = "not-a-repo"
    GIT_MISSING = "git-missing"


class GitRemote:
    """只读消费者。发布（建仓 / commit / push）属作者端职责，不在本类能力范围内。"""

    def __init__(self, root, remote="origin", branch="main", remote_url=None, token_env=None,
                 accelerator_url=None, accelerator_source="ziyou", accelerator_retries=20,
                 overall_deadline=DEFAULT_OVERALL_DEADLINE, pull_timeout=DEFAULT_PULL_TIMEOUT):
        self.root = root
        self.remote = remote
        self.branch = branch
        self.remote_url = remote_url
        self.token_env = token_env
        self.accelerator_url = accelerator_url
        self.accelerator_source = accelerator_source
        self.accelerator_retries = accelerator_retries
        self.overall_deadline = overall_deadline
        self.pull_timeout = pull_timeout

    def run(self, args, timeout=None):
        command = ["git"]
        token_value = os.environ.get(self.token_env) if self.token_env else None
        if token_value:
            command += ["-c", "http.extraHeader=Authorization: Bearer " + token_value]
        try:
            proc = subprocess.run(command + args, cwd=self.root, capture_output=True,
                                  text=True, timeout=timeout)
        except FileNotFoundError:
            return Result(False, "", "git not installed")
        except subprocess.TimeoutExpired:
            # 与 connectivity 保持一致：超时转失败，绝不抛出。err 含 "timeout"，
            # 会被 _is_network_error 识别，从而正常进入加速器 failover 路径。
            return Result(False, "", "timed out after %ss" % timeout)
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

    def pull(self, accelerator_retries=None):
        """拉取更新。整体受 overall_deadline 约束，单次受 pull_timeout 约束。

        两条硬约束（缺一即退化成无上限阻塞）：
          1. 单次有 timeout —— 否则一次 TCP 挂起就能吃掉全部预算。
          2. 整体有 deadline —— 剩余预算低于 MIN_EFFECTIVE_TIMEOUT 时不再发起新尝试，
             且剩余预算会透传给单次调用，单次永远不会超过总预算。
        """
        retries = self.accelerator_retries if accelerator_retries is None else accelerator_retries
        deadline = time.monotonic() + float(self.overall_deadline or 0)
        args = ["pull", "--ff-only", self.remote, self.branch]

        result = self.run(args, timeout=self.pull_timeout)
        if result.ok:
            return result
        if not (self._is_network_error(result.err) and self.accelerator_url):
            return result

        log.warning("pull: network error, trying accelerator (<=%s tries, %.0fs budget)",
                    retries + 1, self.overall_deadline)
        empty_streak = 0
        attempt = 0
        while attempt < retries + 1:
            remaining = deadline - time.monotonic()
            if remaining < MIN_EFFECTIVE_TIMEOUT:
                log.warning("pull: budget exhausted (%.1fs left), stop accelerating", remaining)
                break
            attempt += 1

            hosts = connectivity.fetch_hosts(self.accelerator_url, self.accelerator_source)
            cands = connectivity.top_candidates(hosts, n=3) if hosts else {}
            if not cands:
                # 云函数本身拿不到 IP：这是"同一目标重试"，不是 failover，必须退避并熔断。
                empty_streak += 1
                if empty_streak >= CIRCUIT_THRESHOLD:
                    log.warning("pull: accelerator returned no candidate %s times in a row, "
                                "opening circuit", empty_streak)
                    break
            else:
                empty_streak = 0
                remaining = deadline - time.monotonic()
                if remaining < MIN_EFFECTIVE_TIMEOUT:
                    break
                pr = connectivity.run_git_with_hosts(
                    self.root, args, cands, timeout=min(self.pull_timeout, remaining))
                if pr.returncode == 0:
                    return Result(True, pr.stdout, pr.stderr)

            delay = backoff_delay(attempt)
            remaining = deadline - time.monotonic() - delay
            if remaining < MIN_EFFECTIVE_TIMEOUT:
                log.warning("pull: next try would exceed budget, stop accelerating")
                break
            time.sleep(delay)

        remaining = deadline - time.monotonic()
        if remaining >= MIN_EFFECTIVE_TIMEOUT:
            # 加速器路径全败：回退系统代理/正常 DNS（git 默认出口，不清空代理）
            log.warning("pull: accelerator path exhausted, falling back to system proxy/DNS")
            fb = self.run(args, timeout=min(self.pull_timeout, remaining))
            if fb.ok:
                return fb
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
        accelerator_url=config.get("accelerator_url") or None,
        accelerator_source=config.get("accelerator_source", "ziyou"),
        accelerator_retries=config.get("accelerator_retries", 20),
        overall_deadline=config.get("overall_deadline", DEFAULT_OVERALL_DEADLINE),
        pull_timeout=config.get("pull_timeout", DEFAULT_PULL_TIMEOUT),
    )
