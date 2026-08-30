"""manifest 读写：版本层与总则的唯一落盘入口，供进化层与部署层共用。"""

import json
import os

from core.atomic import write_json

FILENAME = "manifest.json"


def path_for(root):
    return os.path.join(root, FILENAME)


def load_manifest(root):
    path = path_for(root)
    if not os.path.exists(path):
        return {"suite": "LeyaoSeedSkill", "version": "0.0.0", "skills": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(root, manifest):
    # 原子写：manifest 承载版本与每 skill 的 content-hash pin，损坏即失去防漂移能力。
    write_json(path_for(root), manifest)
