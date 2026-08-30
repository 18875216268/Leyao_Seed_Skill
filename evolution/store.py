"""共享知识库：experience / user-model / library-map。磁盘持久、按需加载、语义去重、动态打分、剪枝、置信门控。"""

import json
import os

from core.atomic import write_json

PROMOTE_RULES = {
    "candidate": {"min_support": 3, "min_success_rate": 0.7, "to": "validated"},
    "validated": {"min_support": 6, "min_success_rate": 0.85, "to": "locked"},
}

PRUNE = {"min_support": 2, "min_success_rate": 0.5}

CONSUMABLE_STATES = ("validated", "locked")


class KnowledgeStore:
    def __init__(self, path):
        self.path = path
        self.data = {"experience": [], "user_model": {}, "library_map": {}}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        for key in ("experience", "user_model", "library_map"):
            self.data.setdefault(key, [] if key == "experience" else {})

    def save(self):
        # 共享知识库由多个消费者共用，写入必须全有或全无。
        write_json(self.path, self.data)

    def add_experience(self, rules):
        index = {r["id"]: r for r in self.data["experience"]}
        for rule in rules:
            existing = index.get(rule["id"])
            if existing is None:
                index[rule["id"]] = rule
                continue
            total = existing["support"] + rule["support"]
            weighted = existing["success_rate"] * existing["support"] + rule["success_rate"] * rule["support"]
            existing["support"] = total
            existing["success_rate"] = round(weighted / total, 4)
        self.data["experience"] = list(index.values())
        self.promote()
        self.prune()
        self.save()
        return self.data["experience"]

    def promote(self):
        promoted = []
        for rule in self.data["experience"]:
            step = PROMOTE_RULES.get(rule["state"])
            if not step:
                continue
            evidence = rule["support"] >= step["min_support"]
            quality = rule.get("kind") == "avoid" or rule["success_rate"] >= step["min_success_rate"]
            if evidence and quality:
                rule["state"] = step["to"]
                promoted.append(rule["id"])
        return promoted

    def prune(self):
        kept = []
        for rule in self.data["experience"]:
            if rule["support"] < PRUNE["min_support"]:
                continue
            stale = rule["state"] == "locked" and rule["success_rate"] < PROMOTE_RULES["validated"]["min_success_rate"]
            if stale and rule.get("kind") != "avoid":
                rule["state"] = "validated"
            kept.append(rule)
        self.data["experience"] = kept
        return kept

    def experience(self, states=CONSUMABLE_STATES, kinds=None):
        rules = [r for r in self.data["experience"] if r["state"] in states]
        if kinds:
            rules = [r for r in rules if r["kind"] in kinds]
        return rules

    def set_user_model(self, profile):
        self.data["user_model"] = profile
        self.save()
        return profile

    def user_model(self):
        return self.data["user_model"]

    def set_library_map(self, library_map):
        self.data["library_map"] = library_map
        self.save()
        return library_map

    def library_map(self):
        return self.data["library_map"]
