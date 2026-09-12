#!/usr/bin/env python3
"""自我进化层 CLI（唯一接触面）：变→择→行→证→藏。

用法：
  python evolution/grow.py trace --task "<任务>" --routed "<走了哪条路>" --outcome success|partial|fail [--reason R] [--override O]
  python evolution/grow.py reflect                  # 轨迹 → 候选规则（蒸馏）
  python evolution/grow.py evolve                   # 候选 → 变异（自动档 memory 直写 / 提案待批）
  python evolution/grow.py apply --id p_xxx         # 执行已批准提案（先快照，失败即回滚）
  python evolution/grow.py propose --kind K --payload '<JSON>'   # 择：构造动作类变异提案（payload 契约见 EVOLUTION.md）
  python evolution/grow.py reject --id p_x [--reason R]          # 择：否决提案（关闭 pending，留审计）
  python evolution/grow.py review                   # 观察期结算（促进/降级/淘汰）
  python evolution/grow.py status                   # 全景：轨迹/规则/提案/棘轮/自动开启进度

所有输出均为 JSON（AI 易读）。trace 时自动匹配 active/core 规则记账（hit_rules），
并在数据充足时自动开启主动探索与元变异（翻转写审计）。
数据落点：用户区（与 skill 同级 `.leyao-data/`，见 evolution/EVOLUTION.md）；包内只读。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

sys.dont_write_bytecode = True          # 运行期零写包（不在包内生成 __pycache__）
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import distiller  # noqa: E402
import gate  # noqa: E402
import actions  # noqa: E402
import store  # noqa: E402
import paths  # noqa: E402


def out(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def match_rules(task: str) -> list:
    tokens = set(distiller.tokenize(task))
    return [r["id"] for r in store.rules_by_state("active", "core")
            if set(r["pattern"]) and set(r["pattern"]) <= tokens]


def cmd_trace(args) -> int:
    if not (args.routed or "").strip():
        out({"ok": False, "error": "routed 不能为空：写命中的节点 id（见 library/ROUTES.md 各节点行首），"
                                   "无命中（自带判据亲做）写 none——该字段是规则归属与命中率统计的唯一依据"})
        return 1
    matched = match_rules(args.task)
    data = store.add_trace(args.task, args.routed, args.outcome,
                           args.reason or "", args.override or "", matched)
    ok = args.outcome == "success"
    for rid in matched:
        r = store.get_rule(rid)
        if r:
            store.update_rule(rid,
                              hits=r["hits"] + (1 if ok else 0),
                              misses=r["misses"] + (0 if ok else 1),
                              observed=r["observed"] + 1)
    for r in store.rules_by_state("demoted"):
        store.update_rule(r["id"], observed_since_demote=r.get("observed_since_demote", 0) + 1)
    enabled = actions.auto_enable_check()
    auto = None
    if not getattr(args, "no_auto", False):      # 自动闭环（默认开）：trace → reflect → evolve（memory 自动档直写）
        new_rules = actions.reflect()
        evo = actions.evolve()
        auto = {"new_candidates": len(new_rules),
                "applied": evo.get("applied", []),
                "retired": evo.get("retired", [])}
    warn = None
    if args.outcome in ("fail", "partial") and not (args.reason or "").strip():
        warn = "outcome=%s 但未写 --reason：本层无法从这次学到原因（判据见 processor/flow/5-deliver.md「outcome 必须如实」）" % args.outcome
    out({"ok": True, "total_traces": data["total"], "matched_rules": matched,
         "auto_enabled": enabled, "auto_evolve": auto, "warn": warn})
    return 0


def cmd_reflect(_args) -> int:
    new = actions.reflect()
    out({"ok": True, "new_candidates": [
        {"id": r["id"], "kind": r["kind"], "pattern": r["pattern"], "target": r["target"],
         "support": r["support"], "success_rate": r["success_rate"], "summary": r["summary"]}
        for r in new]})
    return 0


def cmd_evolve(_args) -> int:
    result = actions.evolve()
    out({"ok": True, **result})
    return 0


def cmd_apply(args) -> int:
    result = actions.apply(args.id)
    out(result)
    return 0 if result.get("ok") else 1


def cmd_review(_args) -> int:
    settlement = actions.review()
    checks = gate.run_checks()
    score = gate.keep_score("framework", checks.get("score", 0.0))
    out({"ok": True, **settlement, "checks": {"ok": checks.get("ok"), "score": checks.get("score")},
         "ratchet": score})
    return 0


def assets_data_summary() -> list:
    """各资产私有数据区足迹（**只统计、不解析内容**，零格式耦合）：id / 文件数 / 字节 / 最后写入。

    用途：让 AI 与人一眼看到"各资产在用户区留了什么、有多大、多久没动"——
    清理决策（缓存可删 / 证据类迁移）与后续跨层利用的依据；资产内容语义仍归资产自己。
    """
    d = paths.DATA_D / "assets"
    out = []
    for sub in sorted(p for p in d.glob("*") if p.is_dir()) if d.exists() else []:
        files = [f for f in sub.rglob("*") if f.is_file()]
        latest = max((f.stat().st_mtime for f in files), default=0)
        out.append({"id": sub.name, "files": len(files),
                    "bytes": sum(f.stat().st_size for f in files),
                    "last_write": datetime.datetime.fromtimestamp(latest).isoformat(timespec="seconds") if latest else ""})
    return out


def cmd_status(_args) -> int:
    tr = store.traces()
    exp = store.experience()
    meta = actions.load_meta()
    ae = meta.get("auto_enable", {})
    active_count = len(store.rules_by_state("active", "core"))
    health = distiller.check_library()
    explore_hint = (distiller.explore_signal(tr["items"], meta.get("thresholds", {}).get("min_support", 2))
                    if ae.get("exploration") else None)
    out({
        "ok": True,
        "traces": {"total": tr["total"], "window": len(tr["items"])},
        "rules": {state: [{"id": r["id"], "kind": r["kind"], "support": r["support"],
                           "hits": r["hits"], "misses": r["misses"], "summary": r.get("summary", "")}
                          for r in store.rules_by_state(state)]
                  for state in ("candidate", "active", "core", "demoted")},
        "tombstones": len(exp["tombstones"]),
        "capacity": {"used": len(store.rules_by_state("candidate", "active", "core")),
                     "limit": actions.thresholds().get("max_active_rules", store.MAX_ACTIVE_RULES_DEFAULT)},
        "proposals": store.list_proposals(),
        "ratchet": gate.ratchet()["best"],
        "auto_enable": {**ae, "progress": "%d/%d traces, %d/%d active+core" % (
            tr["total"], ae.get("min_traces", 40), active_count, ae.get("min_active_rules", 2))},
        "library_health": {"ok": not health, "findings": health},
        "exploration": {"enabled": bool(ae.get("exploration")), "signal": explore_hint},
        "assets_data": assets_data_summary(),
        "memory_file": str(store.MEMORY_F),
        "user_area": str(paths.HOME),
        "role": "maintainer" if paths.maintainer() else "user",
        "checks": {"ok": gate.run_checks().get("ok")},
    })
    return 0


def cmd_propose(args) -> int:
    """择：构造动作类变异提案（高风险档唯一入口），待用户批准或否决收口。"""
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        out({"ok": False, "error": "payload 不是合法 JSON：%s" % exc})
        return 1
    try:
        pid = actions.propose(args.kind, payload)
    except ValueError as exc:
        out({"ok": False, "error": str(exc)})
        return 1
    out({"ok": True, "proposal": pid, "kind": args.kind, "payload": payload,
         "next": "经用户批准后执行：python evolution/grow.py apply --id " + pid})
    return 0


def cmd_reject(args) -> int:
    """择：否决提案（pending → rejected，关闭队列并留审计）。"""
    result = actions.reject(args.id, args.reason or "")
    out(result)
    return 0 if result.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="五环自举 · 自我进化层 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_trace = sub.add_parser("trace", help="追加一条任务轨迹（L1）")
    p_trace.add_argument("--task", required=True)
    p_trace.add_argument("--routed", required=True, help="本次走了哪条路/资产")
    p_trace.add_argument("--outcome", required=True, choices=["success", "partial", "fail"])
    p_trace.add_argument("--reason", help="失败/部分成功的原因（反射性分析输入）")
    p_trace.add_argument("--override", help="用户纠正（最强信号）")
    p_trace.add_argument("--no-auto", action="store_true",
                         help="关闭自动闭环（默认：trace 后自动跑 reflect+evolve，让经验立即生效）")

    sub.add_parser("reflect", help="变：轨迹蒸馏 → 候选规则")
    sub.add_parser("evolve", help="择：候选 → 变异（自动档落地 / 提案）")
    p_prop = sub.add_parser("propose", help="择：构造动作类变异提案（待用户批准）")
    p_prop.add_argument("--kind", required=True,
                        choices=["route_update", "asset_write", "meta_update", "core_demote", "framework_update"],
                        help="route_update=路由 / asset_write=资产内容 / meta_update=阈值 / core_demote=规则降级 / framework_update=整包更新")
    p_prop.add_argument("--payload", required=True, help="JSON 负载，如 {\"cmd\":\"add\",\"args\":[\"--id\",\"x\"]}")
    p_rej = sub.add_parser("reject", help="择：否决提案（关闭 pending，留审计）")
    p_rej.add_argument("--id", required=True)
    p_rej.add_argument("--reason", help="否决理由（写入审计）")
    p_apply = sub.add_parser("apply", help="行：执行已批准提案")
    p_apply.add_argument("--id", required=True)
    sub.add_parser("review", help="证+藏：观察期结算")
    sub.add_parser("status", help="全景状态")

    args = parser.parse_args()
    return {"trace": cmd_trace, "reflect": cmd_reflect, "evolve": cmd_evolve,
            "propose": cmd_propose, "reject": cmd_reject, "apply": cmd_apply,
            "review": cmd_review, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
