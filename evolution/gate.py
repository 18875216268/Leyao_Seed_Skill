"""守门：棘轮（只升不降，劣化即回滚）+ 评估门（test-prompts 量化）+ 置信门控（candidate 永不进生产路径）。"""

import json
import os


class Gate:
    def __init__(self, root, snapshot_dir=None):
        self.root = root
        self.snapshot_dir = snapshot_dir or os.path.join(root, "state", "snapshots")
        self.scores_path = os.path.join(root, "state", "ratchet.json")
        self.scores = {}
        self.load_scores()

    def load_scores(self):
        if os.path.exists(self.scores_path):
            with open(self.scores_path, encoding="utf-8") as f:
                self.scores = json.load(f)

    def save_scores(self):
        directory = os.path.dirname(self.scores_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.scores_path, "w", encoding="utf-8") as f:
            json.dump(self.scores, f, ensure_ascii=False, indent=2)

    def snapshot(self, name, payload):
        os.makedirs(self.snapshot_dir, exist_ok=True)
        path = os.path.join(self.snapshot_dir, name + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    def restore(self, name):
        path = os.path.join(self.snapshot_dir, name + ".json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def keep_or_rollback(self, name, score, payload):
        best = self.scores.get(name)
        if best is None or score > best:
            self.scores[name] = score
            self.save_scores()
            self.snapshot(name, payload)
            return {"kept": True, "action": "accept", "score": score, "best": score}
        return {"kept": False, "action": "rollback", "score": score, "best": best, "restored": self.restore(name)}


def run_eval(test_prompts, runner):
    total, passed = 0.0, 0.0
    for case in test_prompts:
        weight = case.get("weight", 1)
        total += weight
        try:
            text = str(runner(case.get("prompt", "")))
        except Exception:
            text = ""
        ok = True
        for token in case.get("expect_contains", []):
            if str(token) not in text:
                ok = False
        for token in case.get("expect_not_contains", []):
            if str(token) in text:
                ok = False
        if ok:
            passed += weight
    return round(passed / total, 4) if total else 0.0
