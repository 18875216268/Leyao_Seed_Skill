#!/usr/bin/env python3
"""卡子系统（唯一实现）：运营知识库 → **极薄常驻速查卡** 的 fetch / check / render / status / read。

契约与条目纪律见 `references/card.md`。红线：
- **零框架依赖**（只用 stdlib + 本 skill 的 common / registry / sources.pool）——保证本资产**可独立使用**；
- **数据源两类**：共享池（知识条目 · `pool#id`）+ `@` 基础卡（路由地基条目 · `node#id`，由卡宿主代蒸，输入=路由面节点）；
  **云智库永不蒸馏** ✗（大库、定位"只查"，仅作按需查询兜底）；
- 产物**只落用户数据区**（`common.HOME`），运行期**零写包**（`dont_write_bytecode` + 只写 HOME）；
- 网络/格式失败一律 **fail-soft**（不抛、不阻断）。

生成流程：`fetch`（池索引优先 + 正文 + 精确 diff，只读）→ **AI 按规范蒸馏**（生产者插槽：优先用路由中匹配的蒸馏类资产；
业务条目 + `@` 基础卡条目**一并蒸**——基础卡输入 = 路由面节点名称+描述，卡宿主代蒸）
→ 写 `card.json` → `check`（唯一验收，可 --verify 抽样回池 / --nodes 离线核验基础卡指针）→ `render`（AI 每任务读的渲染物）。
退出码：0 成功 ｜ 1 卡不合格（契约问题）｜ 3 依赖/未命中 ｜ 5 网络失败（全部类别都失败）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys

sys.dont_write_bytecode = True                 # 运行期零写包：**必须在任何本地 import 之前**（否则 common/pool 会落 __pycache__ ✗）

from datetime import datetime                  # noqa: E402
from pathlib import Path                       # noqa: E402

import common                                  # noqa: E402
import registry as reg                         # noqa: E402
from sources import pool                       # noqa: E402

CARD_JSON = common.HOME / "card.json"
CARD_MD = common.HOME / "card.md"
CARD_META = common.HOME / "card.meta.json"
CANDIDATES = common.HOME / "card.candidates.json"

MAX_ONELINER = 25                              # 单条一句话上限（字）
MAX_CHARS = 1500                               # 卡总量上限（字；按显示文本计）
BASE_HEADER = "【基础卡】（路由地基 · @ 开头；只用于识别与定位）"   # 有基础卡条目时的固定节头（render / 预算共用）
HEADER_CHARS = 40 + len(BASE_HEADER)           # 固定开销 = 卡头图例 40 + 节头实长（由常量推导，防再算错）
CARD_MAX_ITEMS = 80                            # 条数上限
CATEGORIES = ("term", "caliber", "method", "experience")   # 池侧枚举（分类分页多拉的维度）
TIER_BY_CATEGORY = {"caliber": "inject"}       # 池侧语义：口径必须查注入库（与 resolve 一致）
DEFAULT_TTL_DAYS = 1                           # 快照诊断：默认 1 天视为过期（仅报告，不触发刷新）
_SECRET = re.compile(r"AIza[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY")
_POINTER = re.compile(r"^(?:pool|node)#[\w.-]+$")   # 知识条目 pool#id ｜ 基础卡条目 node#id（\w 含中文：node id 无字符集限制）


def _pool_endpoint() -> str:
    """池端点；注册表缺失 / 损坏 → 空串（调用方走 NO_POOL_ENDPOINT 降级——fail-soft，绝不抛）。"""
    try:
        data = reg.load()
    except Exception:                              # noqa: BLE001
        return ""
    a = next((x for x in (data.get("ordered_assets") or []) if x.get("kind") == "pool"), None) or {}
    return str(a.get("endpoint") or "")


def _node_ids_with_mount(routes_path) -> set[str]:
    """读 routes.json 取「有 mount 的节点 id」集合（供 `check --nodes` 离线核验基础卡指针；不可读 → 空集）。"""
    data = _load_dict(routes_path)
    out = set()

    def walk(ns):
        for n in ns or []:
            if str(n.get("mount") or "").strip():
                out.add(str(n.get("id")))
            walk(n.get("children") or [])

    walk(data.get("nodes"))
    return out


def _write_text(path, text: str) -> bool:
    """写用户区文件（fail-soft：失败返回 False，由调用方如实输出）。"""
    try:
        common.ensure_home()
        path.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def _load_dict(path) -> dict:
    """读 JSON 并保证 dict（非对象 / 坏文件 → {}；红线：格式失败一律 fail-soft、不抛 ✗）。"""
    x = common.load_json(Path(path), {})
    return x if isinstance(x, dict) else {}


def cmd_fetch(args) -> int:
    """拉**共享池**候选 → `<数据区>/card.candidates.json`；**不改卡**。

    数据源硬规则：**云智库不参与卡**（体量大、定位"只查"）✗——本函数只读 registry 里 pool 端点。
    流程：① `fields=index` 轻量索引（池已升级：全池 id/version + `total` + `pool_updated_at`，**零写**）
    → 精确 diff（含长尾 new / 废弃 gone）；② 分类分页拉**正文**（蒸馏必需，≤50/类）＋ 对"新增/变更但
    不在正文集里"的 id 用 `ids=` 定向补拉（≤50）；池未升级 → 自动回落旧路径（diff 基于上一份候选）。
    """
    ep = _pool_endpoint()
    if not ep:
        common.emit({"ok": False, "error": "NO_POOL_ENDPOINT", "hint": "registry.json 未声明 pool 端点"})
        return 3
    old = _load_dict(CANDIDATES)
    old_map = {str(x.get("id")): x for x in (old.get("items") or [])}

    def _fp(x):
        return "%s/%s" % (x.get("version"), x.get("similarity_hash") or "")

    probe = pool.index_list(ep, limit=1)                        # 轻探测：旧池毫秒级返回（无 pool_updated_at）→ 直接回落
    idx = pool.index_list(ep) if probe.get("ok") else probe     # ① 轻量索引（零写；未升级 → ok=False）
    mode = "index" if idx.get("ok") else "category"
    pool_at, total = (idx.get("pool_updated_at"), idx.get("total")) if idx.get("ok") else (None, None)
    base = [dict(x) for x in (idx.get("items") or [])] if idx.get("ok") else None

    cats = [c.strip() for c in (args.category or ",".join(CATEGORIES)).split(",") if c.strip()]
    items, errors, truncated = [], [], {}
    for cat in cats:                                            # ② 正文（蒸馏必需）
        r = pool.hot_list(ep, limit=args.limit, category=cat, tier=TIER_BY_CATEGORY.get(cat))
        if r.get("ok"):
            items += [dict(it, _category=cat) for it in r["items"]]
            if len(r["items"]) >= min(int(args.limit), 50):
                truncated[cat] = True
        else:
            errors.append("%s: %s" % (cat, r.get("error")))
    seen, uniq = set(), []
    for it in items:
        i = str(it.get("id") or "")
        if i and i not in seen:
            seen.add(i)
            uniq.append(it)
    if base is None:                                            # 池未升级：diff 基于正文集（旧行为）
        base = uniq
    diff = {"new": [], "changed": [], "gone": []}
    cur = {}
    for it in base:
        i = str(it.get("id"))
        cur[i] = it
        if i not in old_map:
            diff["new"].append(i)
        elif _fp(it) != _fp(old_map[i]):
            diff["changed"].append(i)
    diff["gone"] = [i for i in old_map if i not in cur]
    extra = [i for i in (diff["new"] + diff["changed"]) if i not in seen][:50]
    if extra:                                                   # ③ 长尾/变更条目定向补正文（≤50）
        r2 = pool.fetch_by_ids(ep, extra)
        if r2.get("ok"):
            uniq += [dict(it, _category="extra") for it in r2["items"]]
        else:
            errors.append("ids: %s" % r2.get("error"))
    counts = {c: sum(1 for x in uniq if x.get("_category") == c) for c in cats + ["extra"]}
    payload = {"fetched_at": common.now_iso(), "endpoint": ep, "mode": mode,
               "pool_updated_at": pool_at, "total": total, "index_count": len(base),
               "counts": {c: n for c, n in counts.items() if n},
               "truncated": sorted(truncated), "errors": errors, "diff": diff, "items": uniq}
    if not _write_text(CANDIDATES, json.dumps(payload, ensure_ascii=False, indent=1)):
        common.emit({"ok": False, "error": "WRITE_FAIL", "path": str(CANDIDATES), "hint": "用户区不可写（检查 LEYAO_KB_HOME）"})
        return 3
    common.emit({"ok": bool(uniq), "path": str(CANDIDATES), "items": len(uniq), "mode": mode,
                 "total": total, "pool_updated_at": pool_at,
                 "diff": {k: len(v) for k, v in diff.items()}, "truncated": sorted(truncated), "errors": errors})
    if uniq:
        return 0
    return 5 if errors and len(errors) == len(cats) else 3      # 全部类别网络失败 = 5；否则按"未命中"= 3


def cmd_check(args) -> int:
    """唯一验收（无论谁蒸馏）：格式 / ≤25字 / 条数 ≤80 / 总量（显示文本+固定开销）≤1500 / 两类指针 / 无凭据；通过则更新 meta。

    `--verify N`（可选）：**抽样回池**验 `pool#` 指针存在（池不可达 → 仅 warn ✓ fail-soft）。
    `--nodes <routes.json>`（可选）：**离线核验 `node#` 基础卡指针**（节点必须存在且有 mount；文件不可读 → 仅 warn ✓）。
    """
    if not CARD_JSON.is_file():
        common.emit({"ok": False, "error": "NO_CARD", "path": str(CARD_JSON),
                     "hint": "先 fetch → 蒸馏 → 写 card.json（规范见 references/card.md）"})
        return 3
    data = _load_dict(CARD_JSON)
    items = data.get("items") or []
    problems = []
    if not items:
        problems.append("items 为空")
    if len(items) > CARD_MAX_ITEMS:
        problems.append("条数 %d > %d" % (len(items), CARD_MAX_ITEMS))
    seen = set()
    for i, it in enumerate(items, 1):
        name = str(it.get("name") or "").strip()
        one = str(it.get("oneliner") or "").strip()
        ptr = str(it.get("pointer") or "").strip()
        if not name:
            problems.append("#%d 缺 name" % i)
            continue
        if not one:
            problems.append("#%d 缺 oneliner：%s" % (i, name))
        elif len(one) > MAX_ONELINER:
            problems.append("#%d 一句话 %d 字 > %d：%s" % (i, len(one), MAX_ONELINER, name))
        if not _POINTER.match(ptr):
            problems.append("#%d pointer 非法（应 pool#<id> 或 node#<id>）：%s" % (i, name))
        if re.search(r"[\r\n\t]", name + str(it.get("alias") or "") + one + ptr):
            problems.append("#%d 字段含换行/制表符（会破坏渲染 ✗）：%s" % (i, name))
        key = common.norm_text(name)
        if key in seen:
            problems.append("#%d 重名：%s" % (i, name))
        seen.add(key)
    for k in ("pool_version", "generated_at", "ttl_days"):
        if (data.get("meta") or {}).get(k) in (None, ""):
            problems.append("meta 缺 %s（规范要求必须含：pool_version / generated_at / ttl_days）" % k)
    if _SECRET.search(json.dumps(items, ensure_ascii=False)):
        problems.append("检出密钥字面量（卡内禁凭据）")
    cand = _load_dict(CANDIDATES)
    cand_ids = {str(x.get("id")) for x in (cand.get("items") or [])}
    if cand_ids:                                # 离线引用完整性：候选在场时**全量**核验指针（死指针=不合格）
        dead = [str(it.get("pointer") or "") for it in items
                if str(it.get("pointer") or "").startswith("pool#") and str(it["pointer"])[5:] not in cand_ids]
        if dead:
            problems.append("死指针（不在候选内——转录错/池已变，请核对后 refresh）：%s" % ", ".join(dead[:6]))
    nodes_path = str(getattr(args, "nodes", "") or "")
    nodes_note = ""
    if (not nodes_path) and any(str(it.get("pointer") or "").startswith("node#") for it in items):
        nodes_note = "node# 基础卡指针未核验（未提供 --nodes <routes.json>）"
    if nodes_path:                              # 基础卡指针离线核验（可选；fail-soft：读不到仅标注）
        nids = _node_ids_with_mount(nodes_path)
        if not nids:
            nodes_note = "--nodes 跳过：%s 不可读或无带 mount 的节点" % nodes_path
        else:
            dead_nodes = [str(it.get("pointer") or "") for it in items
                          if str(it.get("pointer") or "").startswith("node#")
                          and str(it["pointer"])[5:] not in nids]
            if dead_nodes:
                problems.append("基础卡死指针（节点不存在或缺 mount）：%s" % ", ".join(dead_nodes[:6]))
    # 预算计"显示文本"（name+alias+oneliner）+ 固定卡头；`pool#id` 指针是机器句柄，不计入（规范同此）
    chars = HEADER_CHARS + sum(len(str(it.get("name") or "")) + len(str(it.get("alias") or ""))
                               + len(str(it.get("oneliner") or ""))
                               for it in items)
    if chars > MAX_CHARS:
        problems.append("总量约 %d 字 > %d（提示：低频条目下沉到按需层）" % (chars, MAX_CHARS))
    ok = not problems
    if ok:
        meta = {**(data.get("meta") or {}), "items": len(items),
                "checked": common.now_iso(), "chars": chars}
        if not _write_text(CARD_META, json.dumps(meta, ensure_ascii=False, indent=1)):
            common.emit({"ok": False, "error": "WRITE_FAIL", "path": str(CARD_META)})
            return 3
    warn, verified = ([nodes_note] if nodes_note else []), {"sample": 0, "checked": 0}
    n = int(getattr(args, "verify", 0) or 0)
    if ok and n > 0:
        ids = [str(it.get("pointer") or "")[5:] for it in items
               if str(it.get("pointer") or "").startswith("pool#")]
        k = min(n, len(ids))
        sample = (ids[:1] if k <= 1 else
                  list(dict.fromkeys(ids[i * (len(ids) - 1) // (k - 1)] for i in range(k))))  # 均匀含尾（稳定、可复现）
        verified["sample"] = len(sample)
        ep = _pool_endpoint()
        if not ep:
            warn.append("--verify 跳过：registry 未声明 pool 端点")
        else:
            r = pool.ids_lookup(ep, sample)
            if not r.get("ok"):
                warn.append("--verify 跳过：%s（池未升级/网络失败——如实标注，不判失败）" % (r.get("error") or "?"))
            else:
                got = {str(x.get("id")) for x in r.get("items") or []}
                missing = [i for i in sample if i not in got]
                verified["checked"] = len(got)
                if missing:
                    warn.append("--verify：%d/%d 指针在池中未命中（可能已废弃，建议下次 refresh 处理）：%s"
                                % (len(missing), len(sample), ", ".join(missing[:6])))
    common.emit({"ok": ok, "problems": problems, "items": len(items), "chars": chars,
                 "verify": {**verified, "warnings": warn}})
    return 0 if ok else 1


def cmd_render(_args) -> int:
    """`card.json` → `card.md`（**稳定排序 = 入卡序**，不按热度重排 → 前缀缓存友好）。"""
    data = _load_dict(CARD_JSON)
    if not data:
        common.emit({"ok": False, "error": "NO_CARD"})
        return 3
    meta = data.get("meta") or {}
    lines = ["# 业务速查卡（常驻 · 只读）", "",
             "> 用途：**识别与定位**；知识定义以运营知识库（池 authority）为准，指针 `pool#<id>` → 需要细节时回池检索；"
             "`node#<id>` = 路由地基卡片 → 见 ROUTES.md 对应节点。",
             "> 生成：`scripts/card.py` ｜ 规范：`references/card.md` ｜ 池快照：%s ｜ 生成时间：%s"
             % (meta.get("pool_version") or "?", meta.get("generated_at") or "?"), ""]
    items = data.get("items") or []
    known = [it for it in items if not str(it.get("pointer") or "").startswith("node#")]
    base = [it for it in items if str(it.get("pointer") or "").startswith("node#")]
    for it in known:
        alias = "（%s）" % it["alias"] if it.get("alias") else ""
        lines.append("- %s%s → %s → `%s`" % (it.get("name"), alias, it.get("oneliner"), it.get("pointer")))
    if base:                                    # 基础卡节（卡尾固定位；无条目则不出现 → 现有卡前缀零变化）
        lines += ([""] if known else []) + [BASE_HEADER]   # 无知识条目时节头紧跟卡头（不重复空行）
        for it in base:
            alias = "（%s）" % it["alias"] if it.get("alias") else ""
            lines.append("- %s%s → %s → `%s`" % (it.get("name"), alias, it.get("oneliner"), it.get("pointer")))
    if not _write_text(CARD_MD, "\n".join(lines) + "\n"):
        common.emit({"ok": False, "error": "WRITE_FAIL", "path": str(CARD_MD)})
        return 3
    common.emit({"ok": True, "path": str(CARD_MD), "items": len(data.get("items") or [])})
    return 0


def cmd_status(args) -> int:
    """快照诊断（仅报告，不触发刷新——唯一蒸馏时点 = 每日 14:00，见规范）：
    missing（首次）/ fresh / stale（TTL 到期）/ unchecked（card.json 比校验新）。

    口径（诚实）：**按 TTL + 校验态判断**（离线）；"池版本变化"由 `fetch` 的 `pool_updated_at`/`diff`
    显式暴露（池已升级），`meta.pool_version` 记录快照（见 `references/card.md` §4）。
    """
    meta = _load_dict(CARD_META)
    if not meta:
        common.emit({"ok": True, "state": "missing", "hint": "首次：fetch → 蒸馏 → check → render，并创建每日 14:00 定时任务（规范见 references/card.md）"})
        return 0
    try:
        ttl = int(meta.get("ttl_days") or args.ttl_days or DEFAULT_TTL_DAYS)
    except (TypeError, ValueError):
        ttl = int(args.ttl_days or DEFAULT_TTL_DAYS)
    age = None
    try:
        gen = datetime.fromisoformat(str(meta.get("generated_at")))
        age = (datetime.now(gen.tzinfo) - gen).total_seconds() / 86400.0
    except (TypeError, ValueError):
        pass
    state = "stale" if (age is None or age >= ttl) else "fresh"
    if not CARD_JSON.is_file():
        state = "missing"                       # 卡本体缺失（meta 尚新也不能算 fresh ✗）
    elif not CARD_MD.is_file():
        state = "unchecked"                     # 渲染物缺失：跑 render
    if state == "fresh" and CARD_JSON.is_file():
        try:                                    # 卡文件比"最近一次校验"更新 → 未校验态（改了没跑 check）
            if CARD_JSON.stat().st_mtime > CARD_META.stat().st_mtime:
                state = "unchecked"
        except OSError:
            pass
    common.emit({"ok": True, "state": state, "items": meta.get("items"),
                 "pool_version": meta.get("pool_version"), "generated_at": meta.get("generated_at"),
                 "checked": meta.get("checked"), "chars": meta.get("chars"),
                 "age_days": round(age, 2) if age is not None else None, "ttl_days": ttl})
    return 0


def cmd_read(args) -> int:
    """按指针读**全文**（卡上规则类条目命中后"必须回池读全文"的正式入口）。

    只读：不写卡、不写状态；命中即返回条目全文（取正文与池侧"使用"语义一致，会记 hit）。
    """
    ep = _pool_endpoint()
    if not ep:
        common.emit({"ok": False, "error": "NO_POOL_ENDPOINT", "hint": "registry.json 未声明 pool 端点"})
        return 3
    pid = str(args.id).split("#", 1)[-1].strip()
    r = pool.fetch_by_ids(ep, [pid])
    if not (r.get("ok") and r.get("items")):
        common.emit({"ok": False, "error": r.get("error") or "NOT_FOUND", "id": pid})
        return 3
    it = r["items"][0]
    common.emit({"ok": True, "id": pid, "title": it.get("title"), "category": it.get("category"),
                 "version": it.get("version"), "updated_at": it.get("updated_at"),
                 "content": it.get("content")})
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="业务速查卡：fetch（索引优先+精确diff）/ check（验收±回池抽检）/ render / status / read（按指针读全文）")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="拉共享池候选（index 优先、自动回落；只读池；云智库不参与）")
    f.add_argument("--category", default="", help="池 category 过滤，逗号分隔（默认 term,caliber,method,experience）")
    f.add_argument("--limit", type=int, default=50, help="单类正文条数（保守硬上限 50）")
    c = sub.add_parser("check", help="校验卡（唯一验收）")
    c.add_argument("--verify", type=int, default=0, help="抽样回池验 pointer 存在（默认 0=跳过；失败仅 warn）")
    c.add_argument("--nodes", default="", help="routes.json 路径：离线核验 node# 基础卡指针（缺省跳过，fail-soft）")
    sub.add_parser("render", help="card.json → card.md")
    s = sub.add_parser("status", help="快照诊断（missing/fresh/stale/unchecked；不触发刷新）")
    s.add_argument("--ttl-days", type=int, default=0, help="覆盖 TTL（默认取 meta.ttl_days 或 %d 天）" % DEFAULT_TTL_DAYS)
    r = sub.add_parser("read", help="按指针读全文（规则类条目命中后必须回池读全文——该动作的正式入口；只读、不写状态）")
    r.add_argument("--id", required=True, help="池条目 id（可写 pool#<id>，前缀自动剥离）")
    args = p.parse_args(argv)
    common.ensure_home()                                  # 用户区可写性先行（fail-soft 的前提）
    return {"fetch": cmd_fetch, "check": cmd_check, "render": cmd_render, "status": cmd_status,
            "read": cmd_read}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
