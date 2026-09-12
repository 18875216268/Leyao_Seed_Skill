#!/usr/bin/env python3
"""leyao-knowledge 唯一入口（协议 1.0）。

八个结果型命令（对齐 MCP 设计原则：结果导向 / 参数枚举 / 可操作错误 / 工具数少）：
  status     概览（注册表 + 缓存/记忆统计）
  doctor     自检（注册表 / 公共池连通 / 云智库登录态 / 预算）
  ask        问知识（公共池优先 → 云智库兜底；早停）
  check      口径校验（只认 authority；池侧只查注入库）
  search     关键词搜索（--kind procedure = 程序环独立检索）
  feedback   反馈闭环（采纳/否决 → 晋升/存疑；采纳且 best 来自池时上报价值信号）
  reflect    反思（把轨迹变成带证据的改进建议）
  contribute 沉淀上传（显式：提交本地确认记忆 / 注入权威库；--dry-run 只预检）

退出码：0 成功 ｜ 2 用法/参数 ｜ 3 未命中（含无权威口径）｜ 4 依赖/凭证缺失（含云智库需人工登录）｜ 5 网络失败（全部源不可达）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True          # 运行期零写包（不在本包内生成 __pycache__）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cache  # noqa: E402
import contribute  # noqa: E402
import feedback  # noqa: E402
import memory  # noqa: E402
import reflect  # noqa: E402
import registry as reg  # noqa: E402
import resolve  # noqa: E402
import source_loader  # noqa: E402
from common import emit, ensure_home, log  # noqa: E402

VERSION = "1.0.0"
NEED_TYPES = ("term", "caliber", "policy", "course", "search")


def _pool_asset(data):
    return next((a for a in data["ordered_assets"] if a.get("kind") == "pool"), None)


def _leyou_asset(data):
    return next((a for a in data["ordered_assets"] if a.get("kind") == "cli"), None)


def _exit_code(result: dict) -> int:
    """0 成功 ｜ 4 依赖/凭证缺失（需人工登录或补依赖）｜ 5 全部源网络失败 ｜ 3 未命中。"""
    if result.get("ok"):
        return 0
    layers = result.get("path") or []
    attempted = [l for l in layers if not l.get("skipped")]
    if any(l.get("reason") in ("LOGIN_REQUIRED", "DEPENDENCY_MISSING") for l in attempted):
        return 4
    if attempted and all(l.get("error") or l.get("reason") for l in attempted):
        return 5
    return 3


def cmd_status(_args) -> int:
    ensure_home()
    data = reg.load()
    emit({"ok": True, "plugin": "leyao-knowledge", "protocol": "1.0", "version": VERSION,
          "registry": {"version": data.get("registry_version"),
                       "priority": [a["id"] for a in data["ordered_assets"]],
                       "assets": [{"id": a["id"], "name": a["name"], "kind": a["kind"],
                                   "covers_need": a.get("covers_need")} for a in data["ordered_assets"]],
                       "problems": reg.validate(data)},
          "cache": cache.stats(), "memory": memory.stats(), "feedback": feedback.stats()})
    return 0


def cmd_doctor(args) -> int:
    ensure_home()
    data = reg.load()
    out = {"ok": True, "plugin": "leyao-knowledge", "protocol": "1.0", "checks": {}}
    out["checks"]["registry"] = {"ok": not reg.validate(data), "problems": reg.validate(data)}
    pa = _pool_asset(data)
    if pa:
        t = source_loader.pool.probe(pa["endpoint"], float(pa.get("timeout_s") or 6))
        out["checks"]["pool"] = t
        out["ok"] = out["ok"] and bool(t.get("ok"))
    la = _leyou_asset(data)
    if la:
        st = source_loader.leyou_status(la)
        out["checks"]["leyou"] = st
        # 云智库未登录不算失败（兜底源，明确提示即可）
        if not st.get("ok"):
            out["checks"]["leyou"]["hint"] = st.get("next") or "未登录；需要时手动扫码"
    out["checks"]["budget_seconds"] = reg.budget_seconds(data)
    out["checks"]["semantic_threshold"] = reg.semantic_threshold(data)
    if getattr(args, "warm", False) and pa:
        r = source_loader.pool_search("缺货率", "term", pa, 1, None)
        out["checks"]["warm"] = {"ok": bool(r.get("items")), "ms": r.get("ms")}
    emit(out)
    return 0 if out["ok"] else 5


def cmd_ask(args) -> int:
    ensure_home()
    result = resolve.ask(args.problem, need_type=args.need_type, expand=args.expand,
                         no_cache=args.no_cache, limit=args.limit, full=args.full, tier=args.tier)
    emit(result)
    if result.get("ok"):
        best = result.get("best") or {}
        log("[kb] 命中 %s（trust=%s, %s ms）" % (best.get("source"), best.get("trust"), result.get("elapsed_ms")))
    else:
        log("[kb] 未命中：%s ｜ 建议：%s" % (result.get("reason"), " / ".join(result.get("suggestions") or [])))
    return _exit_code(result)


def cmd_check(args) -> int:
    ensure_home()
    result = resolve.check(args.problem)
    emit(result)
    log("[kb] 口径校验：%s" % ("找到权威口径" if result.get("authority_found") else "未找到权威口径"))
    return 0 if result.get("authority_found") else 3


def cmd_search(args) -> int:
    ensure_home()
    result = resolve.ask(args.q, need_type="search", limit=args.limit, expand=False, kind=args.kind)
    emit(result)
    return _exit_code(result)


def cmd_feedback(args) -> int:
    ensure_home()
    result = feedback.submit(args.query_id, args.verdict, args.note or "")
    emit(result)
    return 0 if result.get("ok") else 2


def cmd_contribute(args) -> int:
    """沉淀上传（显式动作）：提交本地确认记忆 / 注入权威库；--dry-run 只预检。"""
    ensure_home()
    if args.inject:
        if not (args.title and args.content):
            emit({"ok": False, "reason": "MISSING_ARGS", "detail": "注入需 --title 与 --content"})
            return 2
        result = contribute.inject(args.title, args.content, category=args.category or "experience",
                                   kind=args.kind or "fact", dry_run=args.dry_run)
    elif args.all_candidates:
        result = contribute.submit_candidates(dry_run=args.dry_run)
    elif args.memory_id:
        result = contribute.submit_memory(args.memory_id, dry_run=args.dry_run)
    else:
        emit({"ok": False, "reason": "MISSING_ARGS",
              "detail": "三选一：--memory-id <id> / --all-candidates / --inject --title … --content …"})
        return 2
    emit(result)
    if result.get("dry_run"):
        log("[kb] 预检通过（未发起网络写请求）")
    if result.get("reason") == "NO_WRITE_TOKEN":
        return 4                                   # 凭证缺失（对齐方案退出码口径）
    return 0 if result.get("ok") else 5


def cmd_reflect(args) -> int:
    ensure_home()
    result = reflect.reflect(window=args.window)
    emit(result)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="leyao-knowledge：乐药业务知识查询（公共池优先 → 云智库兜底）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="概览：注册表 + 缓存/记忆/反馈统计")
    p_doc = sub.add_parser("doctor", help="自检：注册表 / 公共池连通 / 云智库登录态 / 预算")
    p_doc.add_argument("--warm", action="store_true", help="附带一次真实查询（预热并验证端到端）")

    p_ask = sub.add_parser("ask", help="问知识（早停：公共池命中即返回；查不到才走云智库）")
    p_ask.add_argument("--problem", required=True, help="问题原文（如：成本优势率怎么算）")
    p_ask.add_argument("--need-type", choices=NEED_TYPES, help="诉求类型；不传则自动推断")
    p_ask.add_argument("--expand", action="store_true", help="不早停：两库都取（多可能性）")
    p_ask.add_argument("--no-cache", action="store_true", help="跳过缓存（口径刚变更时用）")
    p_ask.add_argument("--limit", type=int, default=20, help="返回条数（默认 20；对齐 top-20 结论）")
    p_ask.add_argument("--full", action="store_true", help="返回全文（默认摘要 ≤300 字）")
    p_ask.add_argument("--tier", choices=["inject", "session"], help="仅看公共池某 tier")

    p_chk = sub.add_parser("check", help="口径校验（只认 authority；未命中即未通过）")
    p_chk.add_argument("--problem", required=True)

    p_s = sub.add_parser("search", help="关键词搜索")
    p_s.add_argument("--q", required=True)
    p_s.add_argument("--limit", type=int, default=20)
    p_s.add_argument("--kind", choices=["fact", "procedure"],
                     help="程序环：kind=procedure 独立检索（对齐池侧语义）")

    p_fb = sub.add_parser("feedback", help="反馈闭环：采纳/否决（adopt 累积可晋升本地记忆）")
    p_fb.add_argument("--query-id", required=True)
    p_fb.add_argument("--verdict", required=True, choices=["adopt", "reject"])
    p_fb.add_argument("--note")

    p_rf = sub.add_parser("reflect", help="反思：把使用轨迹变成带证据的改进建议")
    p_rf.add_argument("--window", type=int, default=100)

    p_ct = sub.add_parser("contribute", help="沉淀上传（显式动作；默认真实提交，--dry-run 仅预检）")
    grp = p_ct.add_mutually_exclusive_group(required=True)
    grp.add_argument("--memory-id", help="提交单条本地记忆（需已确认：semantic / pool-candidate）")
    grp.add_argument("--all-candidates", action="store_true", help="批量提交全部 pool-candidate 记忆")
    grp.add_argument("--inject", action="store_true", help="写注入库（authority；仅用户显式要求注入）")
    p_ct.add_argument("--title", help="--inject 用：标题")
    p_ct.add_argument("--content", help="--inject 用：内容")
    p_ct.add_argument("--category", choices=["term", "caliber", "method", "experience", "template"],
                      help="--inject 用：类别（默认 experience）")
    p_ct.add_argument("--kind", choices=["fact", "procedure"], help="--inject 用：kind（默认 fact）")
    p_ct.add_argument("--dry-run", action="store_true", help="只预检并打印 payload，不发起网络写请求")

    args = ap.parse_args()
    return {"status": cmd_status, "doctor": cmd_doctor, "ask": cmd_ask, "check": cmd_check,
            "search": cmd_search, "feedback": cmd_feedback, "reflect": cmd_reflect,
            "contribute": cmd_contribute}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
