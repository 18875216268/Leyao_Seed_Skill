"""套件门面（用户端）：一次装配 路由 / 进化 / 更新获取 / 版本。发布属作者端职责，不在门面能力内。"""

import logging
import os
import time

from core.manifest import load_manifest, save_manifest
from core.registry import Registry
from core.router import route as route_query
from deploy import integrity
from deploy.pull import remote_version, sync_before_use
from deploy.remote import from_manifest
from evolution import distiller
from evolution.gate import Gate, run_eval
from evolution.growth import GrowthEngine
from evolution.pipeline import propose_add, propose_modify, propose_remove, register, unregister
from evolution.permissions import ProposalStore
from evolution.store import KnowledgeStore
from evolution.user_modeler import UserModeler

log = logging.getLogger("LeyaoSeedSkill")


class Suite:
    def __init__(self, root=None, allow_native=True):
        self.root = root or os.path.dirname(os.path.abspath(__file__))
        self.allow_native = allow_native
        self.registry = Registry(os.path.join(self.root, "registry", "skills.json"))
        self.manifest = load_manifest(self.root)
        self.store = KnowledgeStore(os.path.join(self.root, "state", "knowledge.json"))
        self.proposals = ProposalStore(os.path.join(self.root, "state", "proposals.json"))
        self.gate = Gate(self.root)
        self.growth = GrowthEngine(self.root, self.registry, self.store, self.gate, self.proposals)
        self.users = UserModeler(self.store)

    def sync(self, force=False):
        result = sync_before_use(
            self.root, self.registry, self.manifest, remote=from_manifest(self.root, self.manifest), force=force
        )
        # 拉取后磁盘 manifest 已变：必须重载内存副本，否则后续 add_skill/save 会用过期副本覆盖刚拉取的配置。
        self.manifest = load_manifest(self.root)
        # 上游可能提交了 registry / manifest / 文件系统三者不一致的状态（例如加了 entry
        # 却忘了 pin）。拉取后对账一次，只留痕不阻断——阻断会让用户连已拉到的更新都用不上。
        report = integrity.compatibility(self.manifest, self.registry.all(), self.root)
        if not report["ok"]:
            log.warning("sync: registry/manifest/filesystem 不一致: %s", report["problems"])
            try:
                from core.audit import record
                record("sync_compatibility", root=self.root, problems=report["problems"])
            except Exception:
                pass
        result["compatibility"] = report
        log.info("sync: %s", result.get("reason", "done"))
        return result

    def schedule_background_sync(self):
        """启动异步后台：查远端版本并条件拉取（未配置/无更新则 no-op，有更新则拉取），不阻塞首用。
        安装位置由运行时实际仓库状态启发式判定（非硬编码目录名）：非受管 git 套件仓库由 sync_before_use 内部跳过。"""
        import threading
        threading.Thread(target=self._async_selfcheck_and_sync, daemon=True).start()

    def _async_selfcheck_and_sync(self):
        try:
            self.sync()
        except Exception:
            log.exception("background self-check sync failed")

    def version(self):
        return remote_version(self.root, self.manifest, remote=from_manifest(self.root, self.manifest))

    def route(self, query, strategy="direct", fallback=None, trace_id=None):
        # trace 贯穿：调用方可传入 trace_id 把多次调用串成一条链路；
        # 不传则新建，并随结果透出，便于调用方接续后续调用。
        from core.audit import new_trace
        started = time.time()
        tid = trace_id or new_trace()
        result = route_query(
            self.registry.enabled(),
            query,
            strategy=strategy,
            experience=self.store.experience(),
            usage=self.users.usage(),
            root=self.root,
            fallback=fallback,
            allow_native=self.allow_native,
            trace_id=tid,
        )
        try:
            from core.audit import record
            picked = None
            if result.get("routed") == "direct":
                picked = (result.get("result") or {}).get("skill_id")
            record("route", root=self.root, trace_id=tid, query=query, strategy=strategy,
                   routed=result.get("routed"), skill=picked,
                   duration_ms=(time.time() - started) * 1000)
        except Exception:
            pass
        result["trace_id"] = tid
        return result

    def _register_now(self, skill_id, source, rel_path=None, overrides=None):
        """绕过授权门的登记。仅两处可用：discover() 与批准后的执行。

        discover() 扫的是用户**自己放进** skills/ 的目录，放入即授权，再确认一次是重复且
        打断自动化；批准后的执行则授权已完成，不该再问第二遍。
        """
        entry = register(self.registry, self.manifest, skill_id, source, self.root, rel_path, overrides)
        self.save()
        return entry

    def _unregister_now(self, skill_id):
        """绕过授权门的移除。同 `_register_now`：只给批准后的执行用。"""
        removed = unregister(self.registry, self.manifest, skill_id)
        if removed:
            self.save()
        return removed

    def add_skill(self, skill_id, source, rel_path=None, overrides=None):
        """登记子 skill —— 需用户授权。

        返回提案 dict（`allowed=False` + `proposal_id`）；真正写入发生在
        `approve_proposal()`。这样 AI 无法静默往用户套件里塞东西。
        """
        return propose_add(self.proposals, skill_id, source, rel_path, overrides)

    def remove_skill(self, skill_id):
        """移除子 skill —— 需用户授权。

        返回提案 dict；真正删除发生在 `approve_proposal()`。删除会让某些 query 的
        承接方凭空消失，属于对外部可见行为的变更，与改内容同级。
        """
        return propose_remove(self.proposals, skill_id)

    def modify_skill(self, skill_id, changes):
        return propose_modify(self.proposals, skill_id, changes)

    def discover(self, source="user_drop"):
        """扫描 skills/ 下未注册子 skill 并登记（幂等、逐 skill 错误隔离）。

        已注册或无 SKILL.md 的目录跳过；derive_entry 缺 triggers 等异常只影响单个 skill，
        不阻断其余发现。返回 [(skill_id, action, detail), ...]，action ∈ registered/skipped/error。
        """
        discovered = []
        skills_root = os.path.join(self.root, "skills")
        if not os.path.isdir(skills_root):
            return discovered
        for name in sorted(os.listdir(skills_root)):
            skill_dir = os.path.join(skills_root, name)
            if not os.path.isdir(skill_dir):
                continue
            if self.registry.get(name):
                discovered.append((name, "skipped", "already registered"))
                continue
            if not os.path.exists(os.path.join(skill_dir, "SKILL.md")):
                discovered.append((name, "skipped", "no SKILL.md"))
                continue
            try:
                # 不经授权门：用户把目录放进 skills/ 本身就是授权，再确认一次是重复。
                self._register_now(name, source)
                try:
                    from core.lint import lint_skill
                    issues = lint_skill(skill_dir, root=self.root)
                    if issues:
                        log.warning("discover: lint %s: %s", name, [i.get("message") for i in issues])
                        try:
                            from core.audit import record
                            record("discover_lint", root=self.root, skill=name, issues=issues)
                        except Exception:
                            pass
                except Exception:
                    pass
                discovered.append((name, "registered", source))
            except Exception as exc:
                log.warning("discover: skip %s: %s", name, exc)
                discovered.append((name, "error", str(exc)))
        return discovered

    def approve_proposal(self, proposal_id):
        """批准已授权的提案并落到路由表（闭合 提案 → 批准 → 执行 循环）。

        有执行语义的 action：
        - `modify_skill_content`：依据当前 SKILL.md 重派生 entry（保留 manual_overrides），
          并把提案中的结构化字段变更写回路由表，再复 pin 完整性 + 落盘。
        - `add_skill`：用提案里带齐的参数执行登记（source / rel_path / overrides 都从 payload 取，
          因为批准可能发生在另一个进程，调用栈已不在）。
        - `remove_skill`：从路由表与 manifest 摘除。skill 不存在时报错而非静默成功——
          批准一个"删空气"的提案通常意味着 id 写错了，静默通过会掩盖问题。

        其余 action 仅置为 approved。
        """
        proposal = self.proposals.approve(proposal_id)
        action = proposal["action"]
        payload = proposal.get("payload") or {}
        if action == "modify_skill_content":
            skill_id = payload.get("skill_id")
            if not skill_id:
                raise ValueError("proposal %s missing skill_id" % proposal_id)
            entry = self.registry.get(skill_id)
            if entry is None:
                raise KeyError("skill not registered: %s" % skill_id)
            updated = distiller.derive_entry(skill_id, self.root, base=entry)
            for key, value in (payload.get("changes") or {}).items():
                if key in entry and key not in ("id", "path"):
                    updated[key] = value
            self.registry.upsert(updated)
            integrity.pin(self.manifest, self.root, [skill_id])
            self.save()
            log.info("approve_proposal: executed %s for %s", proposal_id, skill_id)
        elif action == "add_skill":
            skill_id = payload.get("skill_id")
            if not skill_id:
                raise ValueError("proposal %s missing skill_id" % proposal_id)
            self._register_now(skill_id, payload.get("source"),
                               payload.get("rel_path"), payload.get("overrides"))
            log.info("approve_proposal: executed %s (add %s)", proposal_id, skill_id)
        elif action == "remove_skill":
            skill_id = payload.get("skill_id")
            if not skill_id:
                raise ValueError("proposal %s missing skill_id" % proposal_id)
            if self.registry.get(skill_id) is None:
                raise KeyError("skill not registered: %s" % skill_id)
            self._unregister_now(skill_id)
            log.info("approve_proposal: executed %s (remove %s)", proposal_id, skill_id)
        return proposal

    def reject_proposal(self, proposal_id):
        """关闭提案。

        状态机必须有拒绝这个终态：只有 approved 的话，一条没人认领的提案会永远
        悬在 pending，pending 列表随时间长成噪音，也就没人再看它了。
        """
        proposal = self.proposals.reject(proposal_id)
        log.info("reject_proposal: closed %s", proposal_id)
        return proposal

    def pending_proposals(self):
        return self.proposals.pending()

    def learn(self, traces):
        rules = self.growth.reflect(traces)
        self.users.observe(traces)
        return rules

    def evolve(self):
        return self.growth.apply(self.growth.evolve())

    def evaluate(self, name, test_prompts, runner, payload=None):
        score = run_eval(test_prompts, runner)
        verdict = self.growth.evaluate(name, score, payload or {})
        verdict["score"] = score
        return verdict

    def save(self):
        save_manifest(self.root, self.manifest)
