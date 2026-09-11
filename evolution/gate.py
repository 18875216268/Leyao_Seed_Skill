#!/usr/bin/env python3
"""自我进化层 · 证环（评分 / 棘轮 / 回滚）：一票否决 + 分数只升不降 + 失败即恢复。

回滚依据 = apply 前的内存快照（capture → restore_files），覆盖单次变更窗口；
自动评分（`evolution/tests/run_checks.py`）是 '证' 环唯一可自动化的一半，语义类判定由 AI 在
review 时承担、用户终审。Gödel Agent 实证：临时下降不可避免（92%），可回滚才是关键。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import store

ROOT = store.ROOT
RATCHET_F = store.RATCHET_F
CHECKS = ROOT / "evolution" / "tests" / "run_checks.py"


def run_checks() -> dict:
    proc = subprocess.run([sys.executable, str(CHECKS)], capture_output=True)
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except Exception:
        return {"ok": False, "score": 0.0, "error": proc.stdout.decode("utf-8", "replace")[-200:]}


def capture(files: list) -> dict:
    """读取当前内容（回滚依据，内存持有）：绝对路径键；文件不存在记 None（回滚时删除）。"""
    snap = {}
    for p in files:
        snap[str(p)] = p.read_text(encoding="utf-8") if p.exists() else None
    return snap


def restore_files(before: dict) -> int:
    """精确回滚：有内容写回；None = 当时不存在 → 删除新建的文件。"""
    for raw, content in before.items():
        p = Path(raw)
        if content is None:
            if p.exists():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
    return len(before)


def ratchet() -> dict:
    return store.load_json(RATCHET_F, {"best": {}})


def keep_score(name: str, score: float) -> dict:
    """棘轮：分数只升不降（追踪框架健康度历史最优）。"""
    data = ratchet()
    best = data["best"].get(name)
    if best is None or score >= best:
        data["best"][name] = score
        store.STATE.mkdir(exist_ok=True)
        store.atomic_write(RATCHET_F, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return {"kept": True, "score": score, "best": score}
    return {"kept": False, "score": score, "best": best}
