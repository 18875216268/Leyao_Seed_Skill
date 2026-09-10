#!/usr/bin/env python3
"""经验库（store）：规则状态机 + 墓碑 + 轨迹 + 记忆读写 + 提案 + 审计。

自我进化层唯一数据落点，全部原子写。规则状态机：
  candidate → active → core / demoted → retired(墓碑)。
记忆文件 library/.memory.md 四段与状态一一对应（见 EVOLUTION.md）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "evolution" / "state"   # 本层运行时数据（规则/墓碑/棘轮/轨迹/提案/审计）
TRACES_F = STATE / "traces.json"
EXP_F = STATE / "experience.json"
RATCHET_F = STATE / "ratchet.json"
AUDIT_F = STATE / "audit.log"
PROPOSALS_D = STATE / "proposals"
MEMORY_F = ROOT / "library" / ".memory.md"

MAX_TRACES = 200
MEMORY_SECTIONS = ("失效模式", "有效做法", "待验证", "墓碑")
SECTION_FOR = {"route": "待验证", "avoid": "失效模式"}


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    """原子写：写临时文件 → flush + fsync → 替换。与资产层引擎同一强度，防掉电截断。"""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def rule_id(kind: str, pattern, target: str) -> str:
    """确定性指纹：同 kind+pattern+target 永远同 id（墓碑拦截依赖此性质）。"""
    raw = "%s|%s|%s" % (kind, "|".join(sorted(pattern)), target)
    return "%s_%s" % (kind, hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12])


# ---------- 轨迹（L1，滚动窗口） ----------

def traces() -> dict:
    return load_json(TRACES_F, {"total": 0, "items": []})


def add_trace(task, routed_to, outcome, failure_reason="", user_override="", hit_rules=None) -> dict:
    STATE.mkdir(exist_ok=True)
    data = traces()
    data["total"] += 1
    data["items"].append({
        "ts": now(), "task": task, "routed_to": routed_to, "outcome": outcome,
        "failure_reason": failure_reason, "user_override": user_override,
        "hit_rules": hit_rules or [],
    })
    data["items"] = data["items"][-MAX_TRACES:]
    atomic_write(TRACES_F, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


# ---------- 规则（状态机 + 墓碑） ----------

def experience() -> dict:
    return load_json(EXP_F, {"rules": [], "tombstones": []})


def save_experience(data: dict) -> None:
    STATE.mkdir(exist_ok=True)
    atomic_write(EXP_F, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def tombstoned(rid: str, data: dict | None = None) -> bool:
    data = data if data is not None else experience()
    return any(t["fingerprint"] == rid for t in data["tombstones"])


def upsert_rule(rule: dict) -> dict:
    data = experience()
    for i, r in enumerate(data["rules"]):
        if r["id"] == rule["id"]:
            for key in ("hits", "misses", "observed", "observed_since_demote"):
                rule[key] = r.get(key, 0)
            data["rules"][i] = rule
            break
    else:
        data["rules"].append(rule)
    save_experience(data)
    return rule


def get_rule(rid: str):
    for r in experience()["rules"]:
        if r["id"] == rid:
            return r
    return None


def rules_by_state(*states) -> list:
    return [r for r in experience()["rules"] if r["state"] in states]


def update_rule(rid: str, **fields):
    data = experience()
    for r in data["rules"]:
        if r["id"] == rid:
            r.update(fields)
            save_experience(data)
            return r
    return None


def retire_rule(rid: str, reason: str):
    """淘汰：移出规则表 → 写墓碑（指纹+否定理由）→ 记忆段同步。"""
    data = experience()
    rule = next((r for r in data["rules"] if r["id"] == rid), None)
    if rule is None:
        return None
    data["rules"] = [r for r in data["rules"] if r["id"] != rid]
    data["tombstones"].append({
        "fingerprint": rid, "kind": rule["kind"], "pattern": rule["pattern"],
        "target": rule["target"], "reason": reason, "retired_at": now(),
    })
    save_experience(data)
    memory_scrub(rid)
    memory_put("墓碑", {**rule, "summary": "已淘汰：" + reason})
    return rule


# ---------- 记忆（L0，唯一自动写区） ----------

def memory_read() -> str:
    return MEMORY_F.read_text(encoding="utf-8") if MEMORY_F.exists() else ""


def _section_bounds(lines: list, section: str):
    if section not in MEMORY_SECTIONS or f"## {section}" not in lines:
        return None
    start = lines.index(f"## {section}")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def memory_put(section: str, rule: dict) -> bool:
    """在指定段写入规则条目（幂等：同 id 只保留一条）。"""
    line = "- [%s] %s" % (rule["id"], rule.get("summary") or ("/".join(rule["pattern"]) + " → " + rule["target"]))
    lines = memory_read().splitlines()
    bounds = _section_bounds(lines, section)
    if bounds is None:
        return False
    start, end = bounds
    body = [ln for ln in lines[start + 1:end] if ln.strip() and not ln.strip().startswith("（无）")]
    body = [ln for ln in body if f"[{rule['id']}]" not in ln]
    body.append(line)
    lines[start + 1:end] = body
    atomic_write(MEMORY_F, "\n".join(lines).rstrip() + "\n")
    return True


def memory_scrub(rid: str) -> bool:
    """从所有段移除该 id 的条目（降级/淘汰/迁移时用）；段被清空则回落「（无）」占位。"""
    lines = memory_read().splitlines()
    kept, removed = [], False
    for ln in lines:
        if f"[{rid}]" in ln and ln.strip().startswith("- "):
            removed = True
            continue
        kept.append(ln)
    if not removed:
        return False
    for section in MEMORY_SECTIONS:                      # 空段回落占位，保持格式一致
        bounds = _section_bounds(kept, section)
        if bounds is None:
            continue
        start, end = bounds
        if not [ln for ln in kept[start + 1:end] if ln.strip()]:
            kept[start + 1:end] = ["", "（无）"]
    atomic_write(MEMORY_F, "\n".join(kept).rstrip() + "\n")
    return True


# ---------- 提案（高风险档唯一通道） ----------

def save_proposal(p: dict) -> None:
    PROPOSALS_D.mkdir(parents=True, exist_ok=True)
    atomic_write(PROPOSALS_D / (p["id"] + ".json"), json.dumps(p, ensure_ascii=False, indent=2) + "\n")


def load_proposal(pid: str):
    f = PROPOSALS_D / (pid + ".json")
    return load_json(f, None)


def list_proposals() -> list:
    if not PROPOSALS_D.exists():
        return []
    items = []
    for f in sorted(PROPOSALS_D.glob("p_*.json")):
        p = load_json(f, None)
        if p:
            items.append({"id": p["id"], "kind": p["kind"], "created_at": p["created_at"], "payload": p["payload"]})
    return items


def remove_proposal(pid: str) -> bool:
    f = PROPOSALS_D / (pid + ".json")
    if f.exists():
        f.unlink()
        return True
    return False


# ---------- 审计（永不清理） ----------

def audit(event: str, **fields) -> None:
    STATE.mkdir(exist_ok=True)
    entry = json.dumps({"ts": now(), "event": event, **fields}, ensure_ascii=False)
    with AUDIT_F.open("a", encoding="utf-8") as fh:
        fh.write(entry + "\n")
