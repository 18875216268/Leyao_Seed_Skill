"""成长引擎（读写·驱动者）：反思 → 进化 → 评估。只消费知识资产与反馈，不做抽取；变异严格优于基线才保留。"""

from evolution import distiller, permissions
from evolution.gate import Gate


class GrowthEngine:
    def __init__(self, root, registry, store, gate=None, proposals=None):
        self.root = root
        self.registry = registry
        self.store = store
        self.gate = gate or Gate(root)
        self.proposals = proposals

    def reflect(self, traces, min_support=2):
        rules = distiller.distill_traces(traces, min_support=min_support)
        self.store.add_experience(rules)
        self.store.set_library_map(distiller.distill_library(self.registry.enabled()))
        return rules

    def evolve(self):
        library = self.store.library_map() or {}
        mutations = []
        for conflict in library.get("conflicts", []):
            mutations.append(
                {
                    "id": "priority:%s" % "+".join(sorted(conflict["skills"])),
                    "action": "update_registry_entry",
                    "kind": "priority_adjust",
                    "skills": sorted(conflict["skills"]),
                    "reason": conflict["reason"],
                    "evidence": {
                        "source": "library_map.conflicts",
                        "shared_terms": conflict.get("terms") or conflict.get("shared") or [],
                    },
                }
            )
        for rule in self.store.experience(kinds=("avoid",)):
            mutations.append(
                {
                    "id": "negtrig:%s" % rule["id"],
                    "action": "update_registry_entry",
                    "kind": "negative_trigger",
                    "skill": rule["target"],
                    "tokens": rule["pattern"],
                    "reason": "repeated failure under this pattern",
                    # 证据锚定：这次变更不是凭感觉，而是由 N 次观测、成功率 X 支撑的。
                    # 没有这两个数字，事后无法判断变更是否合理（ASG-SI 可复现性要求）。
                    "evidence": {
                        "source": "knowledge.experience",
                        "rule_id": rule.get("id"),
                        "support": rule.get("support"),
                        "success_rate": rule.get("success_rate"),
                        "state": rule.get("state"),
                    },
                }
            )
        return mutations

    def apply(self, mutations):
        from core.audit import new_trace
        # 同一批变更共用 trace_id，事后可整批回放。
        trace_id = new_trace()
        applied, pending = [], []
        for mutation in mutations:
            if not permissions.autonomous(mutation["action"]):
                item = self.proposals.propose(mutation["action"], mutation) if self.proposals else None
                pending.append({"mutation": mutation, "proposal_id": item["id"] if item else None})
                self._audit(trace_id, mutation, applied=False,
                            proposal_id=item["id"] if item else None)
                continue
            before, after = self.apply_one(mutation)
            self._audit(trace_id, mutation, applied=True, before=before, after=after)
            applied.append(mutation)
        if applied:
            self.registry.save()
        return {"applied": applied, "pending_authorization": pending, "trace_id": trace_id}

    def apply_one(self, mutation):
        if mutation["kind"] == "priority_adjust":
            return self._priority_adjust(mutation)
        if mutation["kind"] == "negative_trigger":
            return self._add_negative_triggers(mutation)
        return {}, {}

    def _audit(self, trace_id, mutation, applied, before=None, after=None, proposal_id=None):
        """自进化变更留痕——此前 apply 是静默改路由表，事后无法解释。

        ASG-SI 指出 self-improving agent 的核心治理难题是
        "behavioral drift is difficult to audit or reproduce"。因此每次自动变更
        都记下三件事：依据什么证据（evidence）、改了什么（before → after）、
        由谁授权（authority）。缺任何一件，系统漂移都无法归因。
        """
        try:
            from core.audit import record
            skills = mutation.get("skills")
            if not skills and mutation.get("skill"):
                skills = [mutation["skill"]]
            record(
                "growth.apply" if applied else "growth.propose",
                root=self.root,
                trace_id=trace_id,
                mutation_id=mutation.get("id"),
                kind=mutation.get("kind"),
                action=mutation.get("action"),
                skills=skills or [],
                reason=mutation.get("reason"),
                evidence=mutation.get("evidence") or {},
                before=before or {},
                after=after or {},
                proposal_id=proposal_id,
                authority="autonomous" if applied else "pending_human",
            )
        except Exception:
            pass

    def evaluate(self, name, score, payload):
        return self.gate.keep_or_rollback(name, score, payload)

    def _mark_manual(self, entry, key):
        entry.setdefault("manual_overrides", [])
        if key not in entry["manual_overrides"]:
            entry["manual_overrides"].append(key)

    def _priority_adjust(self, mutation):
        before, after = {}, {}
        for offset, skill_id in enumerate(mutation["skills"]):
            entry = self.registry.get(skill_id)
            if entry is None:
                continue
            before[skill_id] = entry.get("priority", 0)
            entry["priority"] = entry.get("priority", 0) - offset
            after[skill_id] = entry["priority"]
            self._mark_manual(entry, "priority")
        return before, after

    def _add_negative_triggers(self, mutation):
        entry = self.registry.get(mutation["skill"])
        if entry is None:
            return {}, {}
        before = list(entry.get("negative_triggers", []))
        merged = set(before) | set(mutation["tokens"])
        entry["negative_triggers"] = sorted(merged)
        after = list(entry["negative_triggers"])
        self._mark_manual(entry, "negative_triggers")
        return {mutation["skill"]: before}, {mutation["skill"]: after}
