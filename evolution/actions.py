#!/usr/bin/env python3
"""自我进化层 · 择 / 行 / 证结算 + 阈值层：

reflect 蒸馏候选 → evolve 出变异（自动档 memory 直写 / 高风险档提案）
→ apply 执行已批准提案（快照→写入→评分→失败即回滚）→ review 观察期结算。
两档权限：唯一自动档是 library/.memory.md（L0）；其余一律提案。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
import sys

import distiller
import gate
import store

META_F = store.ROOT / "evolution" / "meta.json"


def load_meta() -> dict:
    return store.load_json(META_F, {})


def save_meta(meta: dict) -> None:
    meta["updated"] = datetime.date.today().isoformat()
    store.atomic_write(META_F, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")


def thresholds() -> dict:
    return load_meta().get("thresholds", {})


# ---------- 自动开启（用户裁决：数据够则自动开，翻转留审计） ----------

def auto_enable_check() -> list:
    meta = load_meta()
    ae = meta.get("auto_enable", {})
    total = store.traces()["total"]
    active = len(store.rules_by_state("active", "core"))
    changed = []
    if not ae.get("exploration") and total >= ae.get("min_traces", 40):
        ae["exploration"] = True
        changed.append("exploration")
    if (not ae.get("meta_mutation") and total >= ae.get("min_traces", 40)
            and active >= ae.get("min_active_rules", 2)):
        ae["meta_mutation"] = True
        changed.append("meta_mutation")
    if changed:
        meta["auto_enable"] = ae
        save_meta(meta)
        store.audit("auto_enable", enabled=changed, traces=total, active_rules=active)
    return changed


# ---------- 变→择：蒸馏 + 出变异 ----------

def reflect() -> list:
    """变：经验候选每次重建（候选是派生态，防 stale 堆积）。

    只蒸馏统计经验（用户纠正/失败模式；墓碑在 distiller 拦截）。
    库体检与探索信号是确定性/窗口条件 → 由 status 实时计算，不入库（修复即自动消解）。
    已激活规则（active/core/demoted）不受重建影响，仅候选提升 support。
    """
    th = thresholds()
    tr = store.traces()["items"]
    distilled = distiller.distill_traces(tr, th.get("min_support", 2))

    distilled_ids = {r["id"] for r in distilled}
    data = store.experience()
    data["rules"] = [r for r in data["rules"]
                     if r["state"] != "candidate" or r["id"] in distilled_ids]
    store.save_experience(data)

    new = []
    for r in distilled:
        existing = store.get_rule(r["id"])
        if existing:
            if (existing["state"] == "candidate" and r["kind"] in ("route", "avoid")
                    and r["support"] > existing.get("support", 0)):
                store.update_rule(r["id"], support=r["support"], success_rate=r["success_rate"])
            continue
        store.upsert_rule(r)
        new.append(r)
    return new


def evolve() -> dict:
    """择：经验候选（route/avoid，需 support≥门槛）→ 自动档 memory 直写并激活。

    动作类变异（路由 / 资产内容 / 阈值 / 降级）不走此路径——由 AI 依 status 诊断信号
    或观察期结算用 `grow.py propose` 构造提案，经用户批准后 apply。
    """
    th = thresholds()
    applied = []
    for r in store.rules_by_state("candidate"):
        if r["kind"] not in ("route", "avoid") or r.get("support", 0) < th.get("min_support", 2):
            continue
        section = store.SECTION_FOR[r["kind"]]
        ok = store.memory_put(section, r)
        store.update_rule(r["id"], state="active", activated_at=store.now())
        store.audit("evolve.auto_memory", rule=r["id"], section=section,
                    support=r["support"], success_rate=r["success_rate"], applied=ok)
        applied.append({"rule": r["id"], "action": f"memory_put:{section}", "summary": r.get("summary")})
    return {"applied": applied}


# ---------- 择 / 行：提案（唯一高风险通道）—— 造 / 否决 / 执行 ----------

PROPOSAL_CONTRACT = {
    "route_update": ("cmd", "args"),
    "asset_write": ("file", "text"),
    "meta_update": ("key", "value"),
    "core_demote": ("rule",),
}


def contract_error(kind: str, payload) -> str:
    """提案 payload 契约校验（唯一实现：propose 与 apply 共用）。返回 "" 即合规。"""
    if kind not in PROPOSAL_CONTRACT:
        return "未知提案类型：%s（可用：%s）" % (kind, " / ".join(PROPOSAL_CONTRACT))
    missing = [k for k in PROPOSAL_CONTRACT[kind] if k not in (payload or {})]
    if missing:
        return "%s 的 payload 缺字段：%s" % (kind, missing)
    if kind == "route_update" and not isinstance(payload["args"], list):
        return "route_update 的 args 必须是数组（引擎参数列表）"
    return ""


def propose(kind: str, payload: dict) -> str:
    """造提案；契约不合规即报错（不造无法执行的悬空提案）。"""
    err = contract_error(kind, payload)
    if err:
        raise ValueError(err)
    digest = hashlib.sha1(json.dumps({"kind": kind, "payload": payload},
                                     ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    pid = "p_%s_%s" % (datetime.datetime.now().strftime("%m%d%H%M%S"), digest)
    store.save_proposal({"id": pid, "kind": kind, "payload": payload,
                         "created_at": store.now(), "status": "pending"})
    store.audit("propose", proposal=pid, kind=kind, payload=payload)
    return pid


def reject(pid: str, reason: str = "") -> dict:
    """否决提案：pending 的另一个终态（只批准不否决，悬空提案会堆成噪音）。"""
    p = store.load_proposal(pid)
    if not p:
        return {"ok": False, "error": "提案不存在：%s" % pid}
    note = (reason or "").strip() or "用户否决"
    store.audit("propose.rejected", proposal=pid, kind=p.get("kind"), reason=note)
    store.remove_proposal(pid)
    return {"ok": True, "rejected": pid, "kind": p.get("kind"), "reason": note}


def _finish_apply(pid: str, kind: str, before: dict, note: str) -> dict:
    checks = gate.run_checks()
    if not checks.get("ok"):
        gate.restore_files(before)
        store.audit("apply.rollback", proposal=pid, kind=kind, checks=checks, note=note)
        store.remove_proposal(pid)
        return {"ok": False, "rolled_back": True, "reason": "apply 后评分未通过，已回滚", "checks": checks}
    score = gate.keep_score("framework", checks.get("score", 0.0))
    store.remove_proposal(pid)
    store.audit("apply", proposal=pid, kind=kind, approved_by="user",
                files=list(before), score=score, note=note)
    return {"ok": True, "applied": kind, "checks": checks, "ratchet": score}


def apply(pid: str, approved_by: str = "user") -> dict:
    p = store.load_proposal(pid)
    if not p:
        return {"ok": False, "error": "提案不存在：%s" % pid}
    kind, payload = p["kind"], p["payload"]

    err = contract_error(kind, payload)          # 契约守卫：残次提案不得进入执行
    if err:
        store.audit("apply.rejected", proposal=pid, kind=kind, reason=err)
        store.remove_proposal(pid)
        return {"ok": False, "discarded": True, "error": "%s；该提案已作废并留审计" % err}

    if kind == "route_update":
        paths = [store.ROOT / "library" / "routes.json", store.ROOT / "library" / "ROUTES.md"]
        before = gate.capture(paths)
        args = [sys.executable, str(store.ROOT / "library" / "engine.py"), payload["cmd"]] + payload["args"]
        proc = subprocess.run(args, capture_output=True)
        if proc.returncode != 0:
            gate.restore_files(before)
            store.audit("apply.rollback", proposal=pid, kind=kind,
                        reason=proc.stdout.decode("utf-8", "replace")[-200:])
            store.remove_proposal(pid)
            return {"ok": False, "rolled_back": True, "reason": "引擎校验未通过，已回滚"}
        return _finish_apply(pid, kind, before, payload.get("note", ""))

    if kind == "meta_update":
        meta = load_meta()
        if not meta.get("auto_enable", {}).get("meta_mutation"):
            store.audit("apply.blocked", proposal=pid, kind=kind, reason="元变异未开启（数据不足）")
            return {"ok": False, "blocked": True,
                    "error": "元变异未开启（数据不足），拒绝执行；提案保留，待条件满足后再批准"}
        before = gate.capture([META_F])
        node = meta
        parts = payload["key"].split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        old = node.get(parts[-1])
        node[parts[-1]] = payload["value"]
        store.atomic_write(META_F, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
        result = _finish_apply(pid, kind, before, "%s: %s → %s" % (payload["key"], old, payload["value"]))
        return result

    if kind == "asset_write":
        # 内容变更只允许落在资产根内（框架代码与文档不在此列，防扩权）
        target = store.ROOT / payload["file"]
        resolved = target.resolve()
        if not str(resolved).startswith(str((store.ROOT / "library" / "assets").resolve())):
            store.audit("apply.rejected", proposal=pid, kind=kind, file=payload.get("file"),
                        reason="越权：只允许写资产根 library/assets/ 内")
            store.remove_proposal(pid)
            return {"ok": False, "discarded": True,
                    "error": "越权：只允许写资产根（library/assets/）内的文档；该提案已作废并留审计"}
        before = gate.capture([target])
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        store.atomic_write(target, current.rstrip() + "\n\n" + payload["text"].rstrip() + "\n")
        return _finish_apply(pid, kind, before, payload.get("note", ""))

    if kind == "core_demote":
        r = store.get_rule(payload.get("rule", ""))
        if not r or r["state"] != "core":
            store.audit("apply.rejected", proposal=pid, kind=kind, rule=payload.get("rule"),
                        reason="规则不存在或已非 core")
            store.remove_proposal(pid)
            return {"ok": False, "discarded": True,
                    "error": "规则不存在或已非 core；该提案已作废并留审计"}
        store.update_rule(r["id"], state="demoted", demoted_at=store.now(), observed_since_demote=0)
        store.memory_scrub(r["id"])
        store.audit("apply.core_demote", rule=r["id"], approved_by=approved_by)
        store.remove_proposal(pid)
        return {"ok": True, "applied": "core_demote", "rule": r["id"]}

    # 兜底：契约已保证 kind 属于上面四类；此分支只在契约与分支不同步时可达（防静默返回 None）
    store.audit("apply.rejected", proposal=pid, kind=kind, reason="契约与执行分支不同步")
    store.remove_proposal(pid)
    return {"ok": False, "discarded": True, "error": "未实现的提案类型：%s（契约与分支不同步）" % kind}


# ---------- 证后结算（藏：促进 / 降级 / 淘汰） ----------

def review() -> dict:
    th = thresholds()
    settlement = {"promoted": [], "demoted": [], "retired": [], "checked": 0}
    for r in store.rules_by_state("active", "core", "demoted"):
        r = store.get_rule(r["id"])
        if not r:
            continue
        settlement["checked"] += 1
        if r["state"] == "active":
            if r["hits"] >= th.get("observation", 5) and r["misses"] == 0:
                store.update_rule(r["id"], state="core")
                store.memory_scrub(r["id"])
                store.memory_put("有效做法", r)
                store.audit("review.promote", rule=r["id"], hits=r["hits"], misses=r["misses"])
                settlement["promoted"].append(r["id"])
            elif r["misses"] >= th.get("demote", 2):
                store.update_rule(r["id"], state="demoted", demoted_at=store.now(), observed_since_demote=0)
                store.memory_scrub(r["id"])
                store.audit("review.demote", rule=r["id"], misses=r["misses"])
                settlement["demoted"].append(r["id"])
        elif r["state"] == "core":
            if r["misses"] >= th.get("core_demote", 3):
                pid = propose("core_demote", {"rule": r["id"],
                                              "summary": "core 规则反例达 %d，建议降级（需你确认）" % r["misses"]})
                settlement["demoted"].append("%s（提案 %s）" % (r["id"], pid))
        elif r["state"] == "demoted":
            if r.get("observed_since_demote", 0) >= th.get("retire", 10) and r["hits"] == 0:
                store.retire_rule(r["id"], "降级后观察 %d 次任务无恢复" % r.get("observed_since_demote", 0))
                store.audit("review.retire", rule=r["id"])
                settlement["retired"].append(r["id"])
    return settlement
