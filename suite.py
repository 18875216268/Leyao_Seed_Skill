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
        """启动异步后台：查远端版本并条件拉取（未变 no-op），不阻塞首用。
        安装位置由运行时实际仓库状态启发式判定（非硬编码目录名）：非受管 git 套件仓库由 sync_before_use 内部 no-op。"""
        import threading
        threading.Thread(target=self._async_selfcheck_and_sync, daemon=True).start()

    def _async_selfcheck_and_sync(self):
        try:
            self.sync()
        except Exception:
            pass

    def version(self):
        return remote_version(self.root, self.manifest, remote=from_manifest(self.root, self.manifest))

    def install_status(self):
        """启发式给 AI 提供自启用判定线索；是否自启用、装到哪由 AI 依当前环境裁决。
        脚本不复制文件、不写系统目录（目录命名因软件而异，不能假设都叫 skills）。"""
        root = self.root.rstrip(os.sep)
        parent_dir = os.path.dirname(root)
        parent_name = os.path.basename(parent_dir)
        name = os.path.basename(root)
        user_skills = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills")
        proj_skills = os.path.join(os.getcwd(), ".workbuddy", "skills")
        # 弱线索：父目录是否命中常见 skills 命名约定；不同软件未必叫 skills，不可作权威依据
        looks_like_skills_dir = parent_name == "skills"
        candidates = []
        if os.path.abspath(parent_dir) != os.path.abspath(user_skills):
            candidates.append(user_skills)
        if os.path.abspath(proj_skills) != os.path.abspath(user_skills) and \
                os.path.abspath(parent_dir) != os.path.abspath(proj_skills):
            candidates.append(proj_skills)
        return {
            "current_root": self.root,
            "parent_dir": parent_dir,
            "looks_like_skills_dir": looks_like_skills_dir,
            "name": name,
            "candidates": candidates,
            "guidance": (
                "脚本不复制文件、不写系统目录。是否自启用、装到哪个目录由 AI 依当前环境裁决："
                "(1) 核实候选目录确为当前 agent 的 skill 发现目录——不同软件命名未必叫 skills"
                "（可能为 plugins/commands/.agents 等），父目录命中 skills 仅为弱线索，非权威；(2) "
                "若候选均不匹配，请查当前 agent 文档或询问用户，由 AI 把本套件置于正确的发现目录；"
                "(3) 复制动作由 AI 执行（如 cp -r 或文件工具），目标已存在时先确认是否保留用户改动实例。"
            ),
        }

    def install_plan(self, target=None):
        """仅生成复制计划（源/目标/是否已存在/建议动作），不执行复制、不写系统目录。
        AI 据返回的计划自行把 self.root 复制到 destination 下。"""
        name = os.path.basename(self.root.rstrip(os.sep))
        skills_dir = os.path.abspath(target) if target else os.path.join(
            os.path.expanduser("~"), ".workbuddy", "skills")
        destination = os.path.join(skills_dir, name)
        exists = os.path.exists(destination)
        return {
            "source": self.root,
            "destination": destination,
            "exists": exists,
            "recommended_action": "skip" if exists else "copy",
            "guidance": (
                "AI 执行：将 source 复制到 destination（如 cp -r 或文件工具）。"
                "复制前核实 destination 的父目录确为当前 agent 的 skill 发现目录；"
                "目标已存在时不要静默覆盖——先确认是否保留用户的改动，必要时先备份。"
            ),
        }

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
