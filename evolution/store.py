#!/usr/bin/env python3
"""经验库（store）：规则状态机 + 墓碑 + 轨迹 + 记忆读写 + 提案 + 审计。

唯一数据落点 = **用户区**（与 skill 同级 `.leyao-data/`，由 `paths.py` 解析；包内只读），全部原子写。
规则状态机：candidate → active → core / demoted → retired(墓碑)。
记忆文件 `data/memory.md` 四段与状态一一对应（见 EVOLUTION.md）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402  （导入即初始化用户区：配置/记忆播种，幂等）

ROOT = paths.SKILL_ROOT                # 包根（资产层/引擎引用它）
STATE = paths.STATE_D                  # 用户区运行态（轨迹/规则/棘轮/审计/提案）
TRACES_F = STATE / "traces.json"
EXP_F = STATE / "experience.json"
RATCHET_F = STATE / "ratchet.json"
AUDIT_F = paths.AUDIT_F
PROPOSALS_D = paths.PROPOSALS_D
MEMORY_F = paths.MEMORY_F

MAX_TRACES = 200
MAX_ACTIVE_RULES_DEFAULT = 200      # 库宽上限 C（依据 Ratchet：上限是非发散的必要条件）；默认足够高，避免误退
MEMORY_SECTIONS = ("失效模式", "有效做法", "待验证", "墓碑")
SECTION_FOR = {"route": "待验证", "avoid": "失效模式"}


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def atomic_write(path: Path, text: str) -> None:
    """原子写：写临时文件 → flush + fsync → 替换。

    与 `library/engine.py::_write_atomic` **同强度**（都保证 fsync + `os.replace`），差异仅在临时文件命名
    （本层固定 `<name>.tmp`，引擎用 mkstemp 唯一名）——不合并实现以避免本层反向依赖 library 的私有工具；
    任一处改动都必须保持「fsync + os.replace」这两条不变量。
    """
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


# ---------- 深合并 / 差分（meta 的"覆盖式读取 + 变更集写入"共用） ----------

def deep_merge(base, override):
    """深合并：override 优先；同为 dict 递归，其余直接覆盖。"""
    out = dict(base) if isinstance(base, dict) else {}
    for k, v in (override or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def deep_diff(base, merged):
    """merged 相对 base 的差异：只保留"与默认不同"的键（变更集写入的唯一实现）。"""
    out = {}
    for k, v in (merged or {}).items():
        if k not in base:
            out[k] = v
        elif isinstance(v, dict) and isinstance(base[k], dict):
            sub = deep_diff(base[k], v)
            if sub:
                out[k] = sub
        elif v != base[k]:
            out[k] = v
    return out


def rule_id(kind: str, pattern, target: str) -> str:
    """确定性指纹：同 kind+pattern+target 永远同 id（墓碑拦截依赖此性质）。"""
    raw = "%s|%s|%s" % (kind, "|".join(sorted(pattern)), target)
    return "%s_%s" % (kind, hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12])


# ---------- 轨迹（L1，滚动窗口） ----------

def traces() -> dict:
    return load_json(TRACES_F, {"total": 0, "items": []})


def add_trace(task, routed_to, outcome, failure_reason="", user_override="") -> dict:
    """追加轨迹（L1）。命中规则的记账在 experience.json（hits/misses/observed），此处不另存一份。"""
    STATE.mkdir(exist_ok=True)
    data = traces()
    data["total"] += 1
    data["items"].append({
        "ts": now(), "task": task, "routed_to": routed_to, "outcome": outcome,
        "failure_reason": failure_reason, "user_override": user_override,
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


def enforce_capacity(limit: int) -> list:
    """容量守卫（库宽上限 C）：活跃规则（candidate/active/core）超上限 → 按"贡献最低"退役。

    依据（Ratchet, arXiv 2605.22148 摘要）：**库宽上限 C 是非发散的必要条件**；不加维护会出现
    library drift（库不断增长，直到"注入规则比不注入更差"）。贡献近似 = hits - misses，
    再按 observed、created_at 排序（越差越先退）；**只退役不删除**（复用 retire_rule → 墓碑 + 记忆同步，可审计）。
    """
    active = [r for r in experience()["rules"] if r.get("state") in ("candidate", "active", "core")]
    over = len(active) - int(limit)
    if over <= 0:
        return []
    ranked = sorted(active, key=lambda r: (r.get("hits", 0) - r.get("misses", 0),
                                           r.get("observed", 0), r.get("created_at", "")))
    retired = []
    for r in ranked[:over]:
        reason = "容量上限 C=%d：贡献最低者退役（hits-misses=%d, observed=%d）" % (
            int(limit), r.get("hits", 0) - r.get("misses", 0), r.get("observed", 0))
        retire_rule(r["id"], reason)
        retired.append({"id": r["id"], "reason": reason})
    if retired:
        audit("capacity.retire", limit=int(limit), retired=retired)   # 留痕（不静默）
    return retired


# ---------- 记忆（L0，唯一自动写区） ----------

def memory_read() -> str:
    """读用户区记忆；文件缺失时回落模板（自愈：写入会按模板重建）。"""
    if MEMORY_F.exists():
        return MEMORY_F.read_text(encoding="utf-8")
    return paths.TPL_MEMORY.read_text(encoding="utf-8") if paths.TPL_MEMORY.exists() else ""


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
    """从所有段移除该 id 的条目（降级 / 淘汰 / 状态迁移时用）；段被清空则回落「（无）」占位。"""
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
