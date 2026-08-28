"""manifest 读写：版本层与总则的唯一落盘入口，供进化层与部署层共用。"""

import json
import os

FILENAME = "manifest.json"


def path_for(root):
    return os.path.join(root, FILENAME)


def load_manifest(root):
    with open(path_for(root), encoding="utf-8") as f:
        return json.load(f)


def save_manifest(root, manifest):
    with open(path_for(root), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
