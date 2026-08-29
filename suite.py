"""套件门面（用户端）：一次装配 路由 / 进化 / 更新获取 / 版本。发布属作者端职责，不在门面能力内。"""

import os

from core.manifest import load_manifest, save_manifest
from core.registry import Registry
from core.router import route as route_query
from deploy.pull import remote_version, sync_before_use
from deploy.remote import from_manifest
from evolution.gate import Gate, run_eval
from evolution.growth import GrowthEngine
from evolution.pipeline import propose_modify, register, unregister
from evolution.permissions import ProposalStore
from evolution.store import KnowledgeStore
from evolution.user_modeler import UserModeler


class Suite:
    def __init__(self, root=None):
        self.root = root or os.path.dirname(os.path.abspath(__file__))
        self.registry = Registry(os.path.join(self.root, "registry", "skills.json"))
        self.manifest = load_manifest(self.root)
        self.store = KnowledgeStore(os.path.join(self.root, "state", "knowledge.json"))
        self.proposals = ProposalStore(os.path.join(self.root, "state", "proposals.json"))
        self.gate = Gate(self.root)
        self.growth = GrowthEngine(self.root, self.registry, self.store, self.gate, self.proposals)
        self.users = UserModeler(self.store)

    def sync(self, force=False):
        return sync_before_use(
            self.root, self.registry, self.manifest, remote=from_manifest(self.root, self.manifest), force=force
        )

    def schedule_background_sync(self):
        """启动异步后台：自检安装位置后做版本查+条件拉取（未变 no-op）。不阻塞首用。"""
        import threading
        threading.Thread(target=self._async_selfcheck_and_sync, daemon=True).start()

    def _async_selfcheck_and_sync(self):
        try:
            if not self._is_installed_skill_location():
                return
            self.sync()
        except Exception:
            pass

    def _is_installed_skill_location(self):
        """skill 包恒置于某 skills/ 目录下；非此形态（如开发副本）不触发自动更新，避免覆盖在研文件。"""
        return os.path.basename(os.path.dirname(os.path.abspath(self.root))) == "skills"

    def version(self):
        return remote_version(self.root, self.manifest, remote=from_manifest(self.root, self.manifest))

    def route(self, query, strategy="direct", fallback=None):
        return route_query(
            self.registry.enabled(),
            query,
            strategy=strategy,
            experience=self.store.experience(),
            root=self.root,
            fallback=fallback,
        )

    def add_skill(self, skill_id, source, rel_path=None, overrides=None):
        entry = register(self.registry, self.manifest, skill_id, source, self.root, rel_path, overrides)
        self.save()
        return entry

    def remove_skill(self, skill_id):
        removed = unregister(self.registry, self.manifest, skill_id)
        if removed:
            self.save()
        return removed

    def modify_skill(self, skill_id, changes):
        return propose_modify(self.proposals, skill_id, changes)

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
