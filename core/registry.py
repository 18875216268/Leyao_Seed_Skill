"""路由表：唯一事实源。加载即校验，变更即落盘。"""

import json
import os

from core.atomic import write_json
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
        # 原子写：路由表是唯一事实源，写一半崩溃留下半个 JSON 会让整个套件不可用。
        write_json(self.path, self.entries)

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
