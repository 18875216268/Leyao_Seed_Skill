"""pull 的 deadline 治理回归测试。

锁死三件事（任何一件退化都会让"自动拉取更新"变成无上限阻塞）：

  1. 单次调用有 timeout —— 一次 TCP 挂起不能吃掉全部预算。
  2. 整体有 deadline  —— 剩余预算低于 MIN_EFFECTIVE_TIMEOUT 时不再发起新尝试。
  3. 云函数连续空候选会熔断 —— 不对着一个坏掉的加速器空转满 21 次。

依据：Google SRE retry budget（重试须有 deadline 传播）、AWS Exponential Backoff
and Jitter（full jitter 而非固定 ±抖动，后者仍形成同步重试波前）。

用虚拟时钟推进时间：不真睡，测试既快又确定性可复现。
"""

import os
import random
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from deploy import connectivity  # noqa: E402
from deploy import remote as remote_mod  # noqa: E402
from deploy.remote import (  # noqa: E402
    CIRCUIT_THRESHOLD,
    DEFAULT_BACKOFF_CAP,
    MIN_EFFECTIVE_TIMEOUT,
    GitRemote,
    Result,
    backoff_delay,
    from_manifest,
)

NET_ERR = "fatal: unable to access 'https://github.com/x/y/': Could not resolve host: github.com"


class FakeClock:
    """替换 remote 模块的 time：sleep 只推进虚拟时间，不真睡。"""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Harness:
    """接管 pull 的三个外部依赖：本地 git、云函数取候选、钉 IP 的 git。

    git_cost > 0 时，每次钉 IP 拉取会推进虚拟时钟——真实墙钟时间本就该计入预算，
    否则"预算"只统计了退避睡眠，实际阻塞时长仍会失控。
    """

    def __init__(self, remote, clock, hosts=None, git_rc=1, git_cost=0.0):
        self.remote = remote
        self.clock = clock
        self.hosts = hosts if hosts is not None else {"github.com": ["1.2.3.4"]}
        self.git_rc = git_rc
        self.git_cost = git_cost
        self.fetch_calls = 0
        self.git_calls = []          # 每次钉 IP 拉取收到的 timeout
        self.fallback_ok = False
        self.fallback_err = ""
        self._run_count = 0

        self._orig = {
            "time": remote_mod.time,
            "fetch": connectivity.fetch_hosts,
            "git": connectivity.run_git_with_hosts,
            "run": remote.run,
        }

        remote_mod.time = clock
        connectivity.fetch_hosts = self._fetch
        connectivity.run_git_with_hosts = self._git
        remote.run = self._run

    def _fetch(self, url, source):
        self.fetch_calls += 1
        return dict(self.hosts)

    def _git(self, root, args, hosts, timeout=120):
        self.git_calls.append(timeout)
        self.clock.now += self.git_cost
        return subprocess.CompletedProcess(["git"] + list(args), self.git_rc, "", "boom")

    def _run(self, args, timeout=None):
        self._run_count += 1
        if self._run_count == 1:
            return Result(False, "", NET_ERR)
        return Result(self.fallback_ok, "", self.fallback_err)

    def restore(self):
        remote_mod.time = self._orig["time"]
        connectivity.fetch_hosts = self._orig["fetch"]
        connectivity.run_git_with_hosts = self._orig["git"]
        self.remote.run = self._orig["run"]


def make_remote(**kwargs):
    root = tempfile.mkdtemp(prefix="srs-deadline-")
    kwargs.setdefault("accelerator_url", "https://accel.example")
    return GitRemote(root, **kwargs), root


def run_pull(git_cost=0.0, **kwargs):
    clock = FakeClock()
    remote, _ = make_remote(**kwargs)
    h = Harness(remote, clock, git_cost=git_cost)
    try:
        random.seed(7)  # backoff 带随机性，固定种子保证断言可复现
        result = remote.pull()
    finally:
        h.restore()
    return result, h, clock


# --------------------------------------------------------------------------- 退避

def test_backoff_delay_bounded_by_cap():
    for attempt in range(1, 40):
        delay = backoff_delay(attempt)
        assert 0.0 <= delay <= DEFAULT_BACKOFF_CAP, "attempt=%s delay=%s" % (attempt, delay)
    # 早期退避必须很小（failover 场景不该拖慢正常路径）
    assert backoff_delay(1) <= 0.5
    # 上限封顶：再多次也不会无限增长
    assert backoff_delay(50) <= DEFAULT_BACKOFF_CAP


def test_backoff_delay_is_full_jitter_not_fixed():
    """full jitter 在 [0, ceiling] 上均匀采样，不是"固定延迟 ± 抖动"。

    固定 ±抖动会保留同步的重试波前；full jitter 才真正打散。
    """
    random.seed(11)
    samples = [backoff_delay(8, base=0.5, cap=8.0) for _ in range(400)]
    low = [s for s in samples if s < 1.0]
    high = [s for s in samples if s > 7.0]
    assert low, "退避未覆盖低区间，退化成了固定延迟"
    assert high, "退避未覆盖高区间，退化成了固定延迟"
    mean = sum(samples) / len(samples)
    assert 3.0 < mean < 5.0, "均值应接近 ceiling/2=4，实际 %s" % mean


# ------------------------------------------------------------------- 不该走加速器

def test_non_network_error_never_touches_accelerator():
    clock = FakeClock()
    remote, _ = make_remote()
    h = Harness(remote, clock)
    h._run_count = 0
    remote.run = lambda args, timeout=None: Result(False, "", "fatal: not a git repository")
    try:
        result = remote.pull()
    finally:
        h.restore()
    assert not result.ok
    assert h.fetch_calls == 0, "非网络错误不得触发加速器"
    assert h.git_calls == [], "非网络错误不得发起钉 IP 拉取"


def test_no_accelerator_url_means_no_acceleration():
    clock = FakeClock()
    remote, _ = make_remote(accelerator_url=None)
    h = Harness(remote, clock)
    try:
        result = remote.pull()
    finally:
        h.restore()
    assert not result.ok
    assert h.fetch_calls == 0
    assert h._run_count == 1, "无加速器时不应有多余的回退拉取"


# ----------------------------------------------------------------------- 熔断

def test_circuit_breaks_on_repeated_empty_candidates():
    """云函数连续返回空候选 = 同一目标重试，不是 failover。必须熔断，不许空转 21 次。"""
    clock = FakeClock()
    remote, _ = make_remote()
    h = Harness(remote, clock, hosts={})
    try:
        random.seed(3)
        result = remote.pull()
    finally:
        h.restore()
    assert not result.ok
    assert h.fetch_calls == CIRCUIT_THRESHOLD, (
        "空候选应熔断于 %s 次，实际 %s" % (CIRCUIT_THRESHOLD, h.fetch_calls))
    assert h.git_calls == [], "没有候选就不该发起拉取"


def test_circuit_resets_when_candidate_returns():
    """熔断是"连续"计数：中间拿到过候选就重新计数，不算坏掉。"""
    clock = FakeClock()
    remote, _ = make_remote(overall_deadline=10000.0)
    seq = [{}, {}, {"github.com": ["1.2.3.4"]}, {}, {}, {}]
    h = Harness(remote, clock, hosts=seq[0])
    calls = {"n": 0}

    def flaky(url, source):
        calls["n"] += 1
        h.hosts = seq[min(calls["n"], len(seq) - 1)]
        return dict(h.hosts)

    connectivity.fetch_hosts = flaky
    try:
        random.seed(5)
        remote.pull(accelerator_retries=6)
    finally:
        h.restore()
    assert calls["n"] > CIRCUIT_THRESHOLD, "候选回来后应继续，不该在第 %s 次就熔断" % CIRCUIT_THRESHOLD


# ------------------------------------------------------------------- deadline

def test_deadline_caps_total_acceleration():
    """整体预算耗尽后必须停止，且永不越过 deadline。"""
    deadline = 30.0
    result, h, clock = run_pull(overall_deadline=deadline, pull_timeout=120.0,
                                accelerator_retries=20)
    assert not result.ok
    assert h.fetch_calls < 21, "预算应提前终止加速，实际尝试 %s 次" % h.fetch_calls
    assert clock.now <= deadline, "虚拟耗时 %.1fs 超过 deadline %.1fs" % (clock.now, deadline)
    assert h.git_calls, "预算充足时至少应尝试一次"


def test_single_attempt_never_exceeds_remaining_budget():
    """单次 timeout 必须同时受 pull_timeout 与剩余预算双重约束。"""
    deadline = 60.0
    pull_timeout = 120.0
    result, h, clock = run_pull(overall_deadline=deadline, pull_timeout=pull_timeout)
    assert h.git_calls
    for t in h.git_calls:
        assert t <= pull_timeout, "单次超时 %s 超过 pull_timeout %s" % (t, pull_timeout)
        assert t <= deadline, "单次超时 %s 超过整体预算 %s" % (t, deadline)


def test_tiny_deadline_skips_acceleration_entirely():
    """预算连一次有效 git 握手都不够时，直接放弃，不白等。"""
    result, h, clock = run_pull(overall_deadline=1.0, pull_timeout=120.0)
    assert not result.ok
    assert h.fetch_calls == 0, "预算不足时不应发起加速，实际 %s 次" % h.fetch_calls
    assert h.git_calls == []


def test_fallback_used_when_budget_remains():
    clock = FakeClock()
    remote, _ = make_remote(overall_deadline=10000.0)
    h = Harness(remote, clock)
    h.fallback_ok = True
    h.fallback_err = ""
    try:
        random.seed(9)
        result = remote.pull(accelerator_retries=2)
    finally:
        h.restore()
    assert result.ok, "加速器全败且预算充足时应回退系统代理/DNS"
    assert h.fetch_calls == 3, "retries=2 → 共 3 次尝试"
    assert h._run_count == 2, "应发生一次回退拉取"


def test_fallback_skipped_when_budget_truly_exhausted():
    """预算被真实消耗干净后，不得再发起回退拉取。

    注意区分两种退出：
      - 循环因"等一次退避就会超预算"而退出 → 剩余仍够一次握手，回退是"不用白不用"；
      - 循环因"剩余已低于 MIN_EFFECTIVE_TIMEOUT"而退出 → 确无预算，回退必须跳过。
    本用例构造后者（每次钉 IP 拉取真的耗掉 7s）。
    """
    result, h, clock = run_pull(git_cost=7.0, overall_deadline=30.0, pull_timeout=120.0)
    assert not result.ok
    remaining = 30.0 - clock.now
    assert remaining < MIN_EFFECTIVE_TIMEOUT, (
        "本用例要构造的是'剩余低于一次有效握手'，实际剩余 %.1fs" % remaining)
    assert h._run_count == 1, "预算耗尽时不应再发起回退拉取，实际 %s 次" % h._run_count


def test_git_wall_time_counts_against_deadline():
    """预算必须计入 git 真实耗时，不能只统计退避睡眠。"""
    result, h, clock = run_pull(git_cost=7.0, overall_deadline=30.0, pull_timeout=120.0)
    assert not result.ok
    assert len(h.git_calls) <= 5, (
        "每次拉取耗 7s、预算 30s，最多约 4 次；实际 %s 次说明墙钟时间没计入" % len(h.git_calls))
    assert clock.now <= 30.0 + 7.0, "虚拟耗时越界：%.1fs" % clock.now


def test_success_on_accelerator_path_returns_ok():
    clock = FakeClock()
    remote, _ = make_remote(overall_deadline=10000.0)
    h = Harness(remote, clock, git_rc=0)
    try:
        random.seed(1)
        result = remote.pull()
    finally:
        h.restore()
    assert result.ok, "加速器路径成功必须返回成功"
    assert h.fetch_calls == 1, "首次成功即返回，不该继续尝试"


# ------------------------------------------------------------------ run 超时

def test_run_timeout_becomes_failure_not_exception():
    """subprocess 超时必须转成失败 Result，且 err 能被判定为网络错误（从而正常 failover）。"""
    remote, _ = make_remote()
    original = remote_mod.subprocess.run

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(kwargs.get("args", args[0] if args else "git"), 5)

    remote_mod.subprocess.run = slow
    try:
        result = remote.run(["pull"], timeout=5)
    finally:
        remote_mod.subprocess.run = original
    assert not result.ok
    assert "timed out" in result.err
    assert GitRemote._is_network_error(result.err), "超时必须能被识别为网络错误以进入 failover"


# -------------------------------------------------------------- manifest 透传

def test_from_manifest_passes_budget_settings():
    root = tempfile.mkdtemp(prefix="srs-deadline-")
    manifest = {"deploy": {"remote_url": "", "overall_deadline": 42.0, "pull_timeout": 33.0}}
    remote = from_manifest(root, manifest)
    assert remote.overall_deadline == 42.0
    assert remote.pull_timeout == 33.0
    blank = from_manifest(root, {"deploy": {"remote_url": ""}})
    assert blank.overall_deadline == remote_mod.DEFAULT_OVERALL_DEADLINE
    assert blank.pull_timeout == remote_mod.DEFAULT_PULL_TIMEOUT


def test_min_effective_timeout_is_conservative():
    """MIN_EFFECTIVE_TIMEOUT 必须远小于默认单次超时，否则"剩余预算不够一次握手"的判断失效。"""
    assert MIN_EFFECTIVE_TIMEOUT < remote_mod.DEFAULT_PULL_TIMEOUT
    assert MIN_EFFECTIVE_TIMEOUT > 0


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
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
