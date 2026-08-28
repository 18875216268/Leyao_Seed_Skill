"""路由表：唯一事实源。加载即校验，变更即落盘。"""

import json
import os

from core.contract import normalize, validate


class Registry:
    def __init__(self, path):
        self.path = path
        self.entries = []
        self.load()

    def load(self):
        if not os.path.exists(self.path):
            self.entries = []
            return
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        entries = [normalize(e) for e in raw]
        for e in entries:
            validate(e)
        self.entries = entries

    def save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, ensure_ascii=False, indent=2)

    def upsert(self, entry):
        e = normalize(entry)
        validate(e)
        self.entries = [x for x in self.entries if x["id"] != e["id"]]
        self.entries.append(e)
        self.save()
        return e

    def remove(self, skill_id):
        remaining = [x for x in self.entries if x["id"] != skill_id]
        changed = len(remaining) != len(self.entries)
        if changed:
            self.entries = remaining
            self.save()
        return changed

    def get(self, skill_id):
        for e in self.entries:
            if e["id"] == skill_id:
                return e
        return None

    def all(self):
        return self.entries

    def enabled(self):
        return [e for e in self.entries if e.get("enabled", True)]
