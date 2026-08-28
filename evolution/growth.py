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
                }
            )
        return mutations

    def apply(self, mutations):
        applied, pending = [], []
        for mutation in mutations:
            if not permissions.autonomous(mutation["action"]):
                item = self.proposals.propose(mutation["action"], mutation) if self.proposals else None
                pending.append({"mutation": mutation, "proposal_id": item["id"] if item else None})
                continue
            self.apply_one(mutation)
            applied.append(mutation)
        if applied:
            self.registry.save()
        return {"applied": applied, "pending_authorization": pending}

    def apply_one(self, mutation):
        if mutation["kind"] == "priority_adjust":
            self._priority_adjust(mutation)
        elif mutation["kind"] == "negative_trigger":
            self._add_negative_triggers(mutation)

    def evaluate(self, name, score, payload):
        return self.gate.keep_or_rollback(name, score, payload)

    def _mark_manual(self, entry, key):
        entry.setdefault("manual_overrides", [])
        if key not in entry["manual_overrides"]:
            entry["manual_overrides"].append(key)

    def _priority_adjust(self, mutation):
        for offset, skill_id in enumerate(mutation["skills"]):
            entry = self.registry.get(skill_id)
            if entry is None:
                continue
            entry["priority"] = entry.get("priority", 0) - offset
            self._mark_manual(entry, "priority")

    def _add_negative_triggers(self, mutation):
        entry = self.registry.get(mutation["skill"])
        if entry is None:
            return
        merged = set(entry.get("negative_triggers", [])) | set(mutation["tokens"])
        entry["negative_triggers"] = sorted(merged)
        self._mark_manual(entry, "negative_triggers")
