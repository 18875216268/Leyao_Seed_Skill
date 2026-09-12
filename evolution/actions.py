#!/usr/bin/env python3
"""自我进化层 · 择 / 行 / 证结算 + 阈值层：

reflect 蒸馏候选 → evolve 出变异（自动档 memory 直写 / 高风险档提案）
→ apply 执行已批准提案（快照→写入→评分→失败即回滚）→ review 观察期结算。
两档权限：唯一自动档是用户区记忆（`.leyao-data/data/memory.md`，L0）；其余一律提案（内容类 route_update / asset_write 仅维护者实例）；
整包更新（framework_update，版本维护层）不受维护者限制——经用户批准即可，落地器内自带快照 / 证环 / 整体回滚。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import distiller
import gate
import paths
import store

sys.path.insert(0, str(store.ROOT / "library"))
import engine  # noqa: E402  （复用引擎的路径常量，防两套硬编码漂移；只读）

META_F = paths.META_F


def load_meta() -> dict:
    """覆盖式读取：模板默认 ⊕ 用户变更集（用户优先）。"""
    return store.deep_merge(store.load_json(paths.TPL_META, {}), store.load_json(META_F, {}))


def save_meta(meta: dict) -> None:
    """变更集写入：只落与模板不同的键——维护者改进默认值可惠及未改过的实例。"""
    meta = dict(meta)
    meta["updated"] = datetime.date.today().isoformat()
    store.atomic_write(META_F, json.dumps(store.deep_diff(store.load_json(paths.TPL_META, {}), meta),
                                          ensure_ascii=False, indent=2) + "\n")


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
    store.enforce_capacity(th.get("max_active_rules", store.MAX_ACTIVE_RULES_DEFAULT))   # 容量守卫（reflect 侧）
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
    retired = store.enforce_capacity(th.get("max_active_rules", store.MAX_ACTIVE_RULES_DEFAULT))   # 库宽上限 C（Ratchet）
    return {"applied": applied, "retired": retired}


# ---------- 择 / 行：提案（唯一高风险通道）—— 造 / 否决 / 执行 ----------

PROPOSAL_CONTRACT = {
    "route_update": ("cmd", "args"),
    "asset_write": ("file", "text"),
    "meta_update": ("key", "value"),
    "core_demote": ("rule",),
    "framework_update": ("staging", "version"),
}


def contract_error(kind: str, payload) -> str:
    """提案准入校验：契约 + 权限（唯一实现：propose 与 apply 共用）。返回 "" 即合规。

    权限：内容类变异（asset_write / route_update，改内置资产与路由）仅维护者实例可用
    （用户区 config.json 的 maintainer=true）；使用者改资产/路由走管理台显式维护。
    framework_update（整包更新，版本维护层）不受维护者限制——经用户批准即可；这里校验
    staging 是存在、同名、版本一致且层级齐备的框架包（防误拉 / 防自覆盖）。
    """
    if kind not in PROPOSAL_CONTRACT:
        return "未知提案类型：%s（可用：%s）" % (kind, " / ".join(PROPOSAL_CONTRACT))
    if kind in ("asset_write", "route_update") and not paths.maintainer():
        return ("非维护者实例：%s 属内容层变异（改内置资产/路由），仅维护者实例可用；"
                "使用者侧请用管理台显式维护（python library/admin/console.py；详见 evolution/EVOLUTION.md）" % kind)
    missing = [k for k in PROPOSAL_CONTRACT[kind] if k not in (payload or {})]
    if missing:
        return "%s 的 payload 缺字段：%s" % (kind, missing)
    if kind == "route_update" and not isinstance(payload["args"], list):
        return "route_update 的 args 必须是数组（引擎参数列表）"
    if kind == "framework_update":
        st = Path(str(payload.get("staging") or ""))
        if not st.is_dir():
            return "framework_update 的 staging 必须是存在的目录（新版包）"
        try:
            new_manifest = json.loads((st / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "staging 缺合法 manifest.json（不是完整的框架包）"
        cur_root, new_root = store.ROOT.resolve(), st.resolve()
        if new_root == cur_root or cur_root in new_root.parents:
            return "staging 不能是当前包自身或包内目录（防自覆盖）"
        if str(new_manifest.get("name", "")) != str(store.load_json(store.ROOT / "manifest.json", {}).get("name", "")):
            return "staging 的 manifest.name 与当前包不一致（不是同一框架的更新包）"
        if str(new_manifest.get("version", "")) != str(payload.get("version", "")):
            return "staging 的 manifest.version 与 payload.version 不一致"
        lack = [str(c.get("path", "")).rstrip("/") for c in new_manifest.get("layers", [])
                if not (st / str(c.get("path", "")).rstrip("/")).exists()]
        if lack:
            return "staging 缺层级路径：%s" % lack
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
        lmap_d = engine.LOCAL_MAPS                                # 分片节点的局部图（分形路由）
        pre_maps = {f.name for f in lmap_d.glob("*.md")} if lmap_d.exists() else set()
        files = [engine.ROUTES_JSON, engine.ROUTES_MD]
        files += sorted(lmap_d.glob("*.md")) if lmap_d.exists() else []
        before = gate.capture(files)
        args = [sys.executable, str(Path(engine.__file__)), payload["cmd"]] + payload["args"]
        proc = subprocess.run(args, capture_output=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})   # 子进程零写包
        if proc.returncode != 0:
            gate.restore_files(before)
            if lmap_d.exists():                                   # 本次新建的局部图一并回滚（防残留）
                for f in lmap_d.glob("*.md"):
                    if f.name not in pre_maps:
                        f.unlink()
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
        save_meta(meta)
        result = _finish_apply(pid, kind, before, "%s: %s → %s" % (payload["key"], old, payload["value"]))
        return result

    if kind == "asset_write":
        # 内容变更只允许落在资产根内（框架代码与文档不在此列，防扩权；目录边界校验防 assetsX/ 这类前缀绕过）
        target = store.ROOT / payload["file"]
        resolved = target.resolve()
        assets_root = engine.ASSETS.resolve()
        if assets_root not in resolved.parents:
            store.audit("apply.rejected", proposal=pid, kind=kind, file=payload.get("file"),
                        reason="越权：只允许写资产根 library/assets/ 内")
            store.remove_proposal(pid)
            return {"ok": False, "discarded": True,
                    "error": "越权：只允许写资产根（library/assets/）内的文档；该提案已作废并留审计"}
        before = gate.capture([target])
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        find = payload.get("find") or ""
        if find:
            if find not in current:                             # 就地修正：原文必须命中，否则作废（不猜、不猜错）
                store.audit("apply.rejected", proposal=pid, kind=kind, file=payload.get("file"),
                            reason="find 原文未命中，就地修正无法安全执行")
                store.remove_proposal(pid)
                return {"ok": False, "discarded": True,
                        "error": "就地修正失败：find 原文在该文件中未找到；提案已作废并留审计"}
            new_text = current.replace(find, payload["text"], 1)   # 只替换首个命中
        else:
            new_text = current.rstrip() + "\n\n" + payload["text"].rstrip() + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        store.atomic_write(target, new_text)
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

    if kind == "framework_update":
        return _apply_framework_update(pid, payload)

    # 兜底：契约已保证 kind 属于上面五类；此分支只在契约与分支不同步时可达（防静默返回 None）
    store.audit("apply.rejected", proposal=pid, kind=kind, reason="契约与执行分支不同步")
    store.remove_proposal(pid)
    return {"ok": False, "discarded": True, "error": "未实现的提案类型：%s（契约与分支不同步）" % kind}


# ---------- 版本维护层：整包更新（唯一落地器；快照 / 证环 / 整体回滚 / 版本记录） ----------

UPDATE_SKIP_DIRS = {"__pycache__"}
UPDATE_SKIP_SUFFIX = (".pyc", ".tmp", ".swp")


def _pkg_files(root: Path) -> set:
    """包内文件相对路径（更新作用域的唯一清单）：跳过缓存 / 临时物 / 隐藏物（.git、.leyao-data 等）。"""
    out = set()
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        parts = p.relative_to(root).parts
        if (any(part in UPDATE_SKIP_DIRS or part.startswith(".") for part in parts)
                or p.name.endswith(UPDATE_SKIP_SUFFIX)):
            continue
        out.add(p.relative_to(root).as_posix())
    return out


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def versions_record() -> dict:
    """版本记录（用户区）：local / history / baseline；缺失即空骨架。"""
    return store.load_json(paths.VERSIONS_F, {"local": {}, "history": [], "baseline": {}})


def _prune_empty(dirs) -> None:
    """删空目录（只删空的；非空自然保留）。"""
    for d in sorted(dirs, key=lambda s: s.count("/"), reverse=True):
        if not d:
            continue
        try:
            (store.ROOT / d).rmdir()
        except OSError:
            pass


def _restore_snapshot(snap: Path, kept: list, added: list) -> None:
    """整体回滚：本次新增的删除（顺带清空目录）、快照里的写回（二进制安全）。"""
    for rel in added:
        p = store.ROOT / rel
        if p.exists():
            p.unlink()
    _prune_empty({rel.rsplit("/", 1)[0] for rel in added if "/" in rel})
    for rel in kept:
        dst = store.ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap / rel, dst)


def _apply_framework_update(pid: str, payload: dict) -> dict:
    """整包更新：以 staging 全量对齐包内（覆盖 / 新增 / 删除）；失败整体回滚，成功写版本记录。

    非静默：本地偏离（相对 baseline 哈希）与本地新增文件先落用户区备份，清单进审计与返回。
    """
    staging = Path(str(payload["staging"])).resolve()
    new_files = _pkg_files(staging)
    cur_files = _pkg_files(store.ROOT)
    to_replace = sorted(new_files & cur_files)
    to_write = sorted(new_files - cur_files)
    to_delete = sorted(cur_files - new_files)

    baseline = versions_record().get("baseline") or {}
    deviations = [rel for rel in to_replace
                  if rel in baseline and _sha1(store.ROOT / rel) != baseline[rel]]
    additions = [rel for rel in to_delete if rel not in baseline]      # 本地新增（新版没有 → 将删除）

    snap = Path(tempfile.mkdtemp(prefix="leyao_update_snap_"))
    kept = to_replace + to_delete
    for rel in kept:
        dst = snap / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(store.ROOT / rel, dst)

    backup_rel = ""
    if deviations or additions:                    # 非静默：偏离 / 本地新增 先备份到用户区
        bdir = paths.STATE_D / "update_backups" / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        for rel in deviations + additions:
            dst = bdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(store.ROOT / rel, dst)
        backup_rel = bdir.relative_to(paths.HOME).as_posix()

    try:
        for rel in to_replace + to_write:
            dst = store.ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging / rel, dst)
        for rel in to_delete:
            (store.ROOT / rel).unlink()
        _prune_empty({rel.rsplit("/", 1)[0] for rel in to_delete if "/" in rel})
    except OSError as exc:
        _restore_snapshot(snap, kept, to_write)
        shutil.rmtree(snap, ignore_errors=True)
        store.audit("apply.rollback", proposal=pid, kind="framework_update", reason="覆盖失败：%s" % exc)
        store.remove_proposal(pid)
        return {"ok": False, "rolled_back": True, "reason": "覆盖失败，已整体回滚：%s" % exc}

    checks = gate.run_checks()
    if not checks.get("ok"):
        _restore_snapshot(snap, kept, to_write)
        shutil.rmtree(snap, ignore_errors=True)
        store.audit("apply.rollback", proposal=pid, kind="framework_update",
                    checks=checks, reason="更新后证环未通过")
        store.remove_proposal(pid)
        return {"ok": False, "rolled_back": True, "reason": "更新后证环未通过，已整体回滚", "checks": checks}
    shutil.rmtree(snap, ignore_errors=True)

    record = versions_record()
    history = [h for h in (record.get("history") or []) if isinstance(h, dict)]
    history.insert(0, {"version": str(payload["version"]), "date": datetime.date.today().isoformat(),
                       "source": str(payload.get("source", "remote")),
                       "summary": str(payload.get("summary", "")) or "整包更新（framework_update）"})
    basis = {rel: _sha1(store.ROOT / rel) for rel in sorted(_pkg_files(store.ROOT))}
    record_written = True
    try:
        store.atomic_write(paths.VERSIONS_F, json.dumps(
            {"local": {"version": str(payload["version"]), "applied_at": store.now()},
             "history": history[:10], "baseline": basis}, ensure_ascii=False, indent=2) + "\n")
    except OSError:
        record_written = False
        store.audit("apply.warn", proposal=pid, kind="framework_update",
                    reason="版本记录写入失败（包内已更新）")

    score = gate.keep_score("framework", checks.get("score", 0.0))
    store.audit("apply", proposal=pid, kind="framework_update", approved_by="user",
                version=str(payload["version"]), replaced=len(to_replace), added=len(to_write),
                removed=len(to_delete), deviations=deviations, additions=additions,
                backup=backup_rel, score=score)
    store.remove_proposal(pid)
    return {"ok": True, "applied": "framework_update", "version": str(payload["version"]),
            "checks": checks,
            "replaced": len(to_replace), "added": len(to_write), "removed": len(to_delete),
            "deviations": deviations, "additions": additions, "backup": backup_rel,
            "record_written": record_written, "ratchet": score}


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
