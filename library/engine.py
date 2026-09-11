#!/usr/bin/env python3
"""资产管理层引擎：routes.json（单一事实源）→ ROUTES.md（人读级联地图）。

对外契约（CLI 与管理台共用本引擎，禁止第二实现）：
- 唯一写路径 = `commit(mutate)`：加锁 → 读 → 改 → 原子落盘 routes.json → 渲染 ROUTES.md → 契约校验。
- 节点增删规则集中在 `node_check` / `node_add` / `node_remove`，CLI 与管理台一律复用，禁止各自实现。
- 遍历集中在 `iter_nodes`；查父列表集中在 `find_parent_list`（`find` 复用前者）。
- 管理台语义「位置即归属」的唯一实现 = `nearest_card`（基座 `norm_mount` / `mount_under`）：
  位置落在哪个文件夹，节点就归到其**最近一层卡片**下（无卡片 → 主页顶层）。CLI 为维护者低级工具，
  `--parent` / `--mount` 仍可分别指定，不受本规则约束。
- 类型（type）为自由文本，默认登记「方法论 / Skill包」，实际出现过的类型自动汇入登记表。
- 节点只有唯一挂载字段 `mount`。
- 资产根 `ASSETS` = library/assets/；挂载前缀 `ASSETS_MOUNT` 由其推导，禁止另行硬编码。
- 契约校验 `validate`（唯一实现，CLI / 管理台 / 自检 / 库存体检共用）：挂载路径必须存在（防死链）、
  挂载目录必须有入口文档 SKILL.md / README.md（否则处理器进得去却用不了）、路由 id 必须唯一。
- 孤儿资产（未被任何节点挂载引用的目录）只提示不拦截：它可能是"已放入、待挂载"的合法中间态。

用法：
  python library/engine.py                                   # 重绘 ROUTES.md + 校验
  python library/engine.py add --id <id> --type <类型> --title "<标题>" [--parent <父id>] [--mount <挂载>] [--description "<何时用>"]
  python library/engine.py remove --id <节点id>               # 连同其子树一并摘除
  python library/engine.py move --id <节点id> [--parent <父id>]   # 移动到新文件夹（省略 --parent 即移到根）
  python library/engine.py update --id <节点id> [--title T] [--mount <挂载>] [--description "<何时用>"]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

LIB = Path(__file__).resolve().parent
ROUTES_JSON = LIB / "routes.json"
ROUTES_MD = LIB / "ROUTES.md"
ASSETS = LIB / "assets"                  # 资产根（"主页"）
ASSETS_MOUNT = ASSETS.relative_to(LIB.parent).as_posix() + "/"   # 挂载前缀由路径推导，禁止另行硬编码
DEFAULT_TYPES = ["方法论", "Skill包"]     # 类型登记表默认值（自由文本，可扩展）

_LOCK_FILE = ROUTES_JSON.with_suffix(".lock")
_LOCK_MUTEX = threading.Lock()           # 进程内线程互斥（文件锁负责跨进程）


# ---------- 写锁（跨进程互斥；唯一持有者是 commit） ----------

@contextlib.contextmanager
def _locked(timeout: float = 15.0):
    with _LOCK_MUTEX:                      # 进程内线程互斥
        handle = None
        deadline = time.time() + timeout
        while True:
            try:
                handle = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:                       # 陈旧锁（>60s）自动回收
                    if time.time() - os.path.getmtime(_LOCK_FILE) > 60:
                        os.remove(_LOCK_FILE)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError("等待 routes.json 写锁超时")
                time.sleep(0.05)
        try:
            yield
        finally:
            try:
                os.close(handle)
            finally:
                try:
                    os.remove(_LOCK_FILE)
                except OSError:
                    pass


# ---------- 读 / 写（唯一写入口 commit） ----------

def load() -> dict:
    return json.loads(ROUTES_JSON.read_text(encoding="utf-8"))


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def commit(mutate):
    """唯一写路径：加锁 → 读 → 改 → 落盘 routes.json → 渲染 ROUTES.md → 契约校验。

    mutate(data) 返回 (ok, payload)：
      - ok=False → 放弃写入，payload 为错误说明；
      - ok=True  → 落盘 + 渲染，返回 (True, 契约问题清单)（空清单即健康）。
    """
    with _locked():
        data = load()
        ok, payload = mutate(data)
        if not ok:
            return False, payload
        data["updated"] = datetime.date.today().isoformat()
        _write_atomic(ROUTES_JSON, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        _write_atomic(ROUTES_MD, render(data))
        return True, validate(data, LIB.parent)


# ---------- 遍历 / 查询（唯一遍历实现） ----------

def iter_nodes(nodes, depth: int = 0):
    """先序遍历，产出 (node, depth)。全引擎唯一遍历实现。"""
    for n in nodes or []:
        yield n, depth
        yield from iter_nodes(n.get("children") or [], depth + 1)


def find_parent_list(data: dict, node_id: str):
    """返回包含该节点的兄弟列表（供定位 / 移动 / 删除）。"""
    def walk(nodes):
        for n in nodes:
            if n.get("id") == node_id:
                return nodes
            hit = walk(n.get("children") or [])
            if hit is not None:
                return hit
        return None

    return walk(data.get("nodes") or [])


def find(data: dict, node_id: str):
    """按 id 取节点（复用 find_parent_list，不另开一套遍历）。"""
    lst = find_parent_list(data, node_id)
    if lst is None:
        return None
    return next((n for n in lst if n.get("id") == node_id), None)


# ---------- 挂载路径 / 位置→归属（管理台语义的唯一实现） ----------

def norm_mount(m: str) -> str:
    """挂载路径归一：统一斜杠、去首尾斜杠（空 → ""）。供前缀比较/改写共用。"""
    return (m or "").replace("\\", "/").strip("/")


def mount_under(child: str, base: str) -> bool:
    """child 是否位于 base 之内（严格下级；base 为空恒 False）。"""
    c, b = norm_mount(child), norm_mount(base)
    return bool(b) and bool(c) and c != b and c.startswith(b + "/")


def nearest_card(data: dict, mount: str, exclude_id: str = "") -> str:
    """按位置求归属：返回 mount 的「最近一层卡片」id（无则 "" = 主页顶层）。

    规则：卡片由人创建、目录与卡片 id 一一对应；子目录（无编号）不是卡片——只认已登记
    卡片的挂载路径，取其中**最深**的祖先；位置（含卡片目录下的子目录）落在谁的目录内，
    节点就归谁（管理台「位置即归属」的唯一实现）。
    """
    m = norm_mount(mount)
    if not m:
        return ""
    best, best_len = "", -1
    for n, _ in iter_nodes(data.get("nodes")):
        if n.get("id") == exclude_id:
            continue
        nm = norm_mount(n.get("mount"))
        if not nm or len(nm) <= best_len:
            continue
        if m == nm or mount_under(m, nm):
            best, best_len = n["id"], len(nm)
    return best


def known_types(data: dict) -> list[str]:
    """类型登记表 = 默认类型 + 数据中实际出现的类型（去重，默认在前）。"""
    out = list(DEFAULT_TYPES)
    for n, _ in iter_nodes(data.get("nodes")):
        t = n.get("type")
        if t and t not in out:
            out.append(t)
    return out


def validate(data: dict, root: Path) -> list[str]:
    """路由契约校验（唯一实现，CLI / 管理台 / 自检 / 库存体检共用）。

    三类问题：
    - 死链：挂载路径必须真实存在；
    - 缺入口文档：挂载目录必须有 SKILL.md 或 README.md（处理器命中后要读它，没有则进得去用不了）；
    - id 重复：路由 id 必须唯一（否则无法唯一定位）。
    """
    issues, ids = [], []
    for n, _ in iter_nodes(data.get("nodes")):
        nid = n.get("id")
        ids.append(nid)
        m = n.get("mount")
        if not m:
            continue
        p = root / m
        if not p.exists():
            issues.append(f"{nid}: mount 路径不存在 → {m}")
        elif p.is_dir() and not ((p / "SKILL.md").exists() or (p / "README.md").exists()):
            issues.append(f"{nid}: 挂载目录缺入口文档（SKILL.md / README.md）→ {m}")
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(f"{dup}: 路由 id 重复")
    return issues


def find_orphans(data: dict) -> list[str]:
    """孤儿资产：资产根下未被任何节点挂载引用的目录。"""
    used = set()
    for n, _ in iter_nodes(data.get("nodes")):
        v = (n.get("mount") or "").replace("\\", "/")
        if v.startswith(ASSETS_MOUNT):
            seg = v[len(ASSETS_MOUNT):].strip("/").split("/")[0]
            if seg:
                used.add(seg)
    if not ASSETS.is_dir():
        return []
    return [ASSETS_MOUNT + p.name + "/" for p in sorted(ASSETS.iterdir())
            if p.is_dir() and p.name not in used]


# ---------- 渲染 ----------

def render(data: dict) -> str:
    types = " / ".join(f"`{t}`" for t in DEFAULT_TYPES)
    lines = [
        "# 资产管理层 · 总路由地图",
        "",
        f"> 快照：{data.get('updated')} ｜ 事实源：`routes.json`（v{data.get('version')}）｜"
        "本文件由 `engine.py` 生成，勿手工编辑；增删改走同步命令或管理台。",
        "",
        f"图例：类型为自由文本（默认 {types}）；`→ 挂载` 即该节点在仓库内的资产目录（相对项目根，如 `{ASSETS_MOUNT}…`）。",
        "",
        "> 路由方式：按各节点**描述**匹配任务场景 → 命中即进其 `→ 挂载` 目录，读 `SKILL.md`／`README.md` 按其指引调用；"
        "无命中则按任务处理层自带判据亲自动手（不依赖任何资产）。",
        "> 同读用户区记忆 `.leyao-data/data/memory.md`（与 skill 同级）：命中「失效模式」先规避，命中「有效做法」直接复用。",
        "> 回写契约：交付后追加轨迹时 `--routed` 写**命中的节点 id**（各节点行首反引号内）；"
        "无命中（自带判据亲做）写 `none`——该字段是规则归属与命中率统计的唯一依据。",
        "",
    ]

    for n, depth in iter_nodes(data.get("nodes")):
        indent = "  " * depth
        mount = f" → `{n['mount']}`" if n.get("mount") else ""
        children = n.get("children") or []
        suffix = f"（{len(children)} 个子节点）" if children else ""
        lines.append(f"{indent}- `{n.get('id')}` **{n.get('title')}** `{n.get('type')}`{mount}{suffix}")
        if n.get("description"):
            lines.append(f"{indent}  - _{n['description']}_")
    lines += [
        "",
        "## 维护",
        "",
        "```text",
        "python library/engine.py                      # 重绘本地图 + 契约校验（挂载/入口文档/id）",
        'python library/engine.py add --id <新id> --type <类型> --title "<标题>" [--parent <父id>] [--mount 挂载] [--description "<何时用>"]',
        "python library/engine.py remove --id <节点id>",
        "python library/engine.py move --id <节点id> [--parent <父id>]   # 移动节点（省略即移到根）",
        'python library/engine.py update --id <节点id> [--title 新标题] [--mount 挂载] [--description "<何时用>"]',
        "python library/admin/console.py                     # 可视化管理台（推荐给日常维护）",
        "```",
        "",
    ]
    return "\n".join(lines)


# ---------- 节点增删（唯一实现；CLI 与管理台共用） ----------

def node_check(data: dict, parent_id: str, node: dict):
    """只校验不改树（供需先做副作用搬运的调用方复用）。返回 (ok, msg)。"""
    if not (node.get("type") or "").strip():
        return False, "type 不能为空（自由文本，默认 方法论 / Skill包）"
    if find(data, node["id"]):
        return False, f"id 已存在：{node['id']}"
    if parent_id and find(data, parent_id) is None:
        return False, f"父节点不存在：{parent_id}"
    return True, ""


def node_add(data: dict, parent_id: str, node: dict):
    """挂载节点（新增放第一位）。返回 (ok, msg)。"""
    ok, msg = node_check(data, parent_id, node)
    if not ok:
        return False, msg
    if parent_id:
        find(data, parent_id).setdefault("children", []).insert(0, node)
    else:
        data.setdefault("nodes", []).insert(0, node)
    return True, ""


def node_move(data: dict, node_id: str, parent_id: str):
    """移动节点到新父文件夹（跨层级）。返回 (ok, msg)。

    约束：目标父必须存在；禁止移入自身或自身子树（否则子树成环、整枝失访）。
    落位规则与新增一致：插入新父 children 的第一位（省略 parent 即移到根）。
    """
    if find(data, node_id) is None:
        return False, f"节点不存在：{node_id}"
    if parent_id:
        if find(data, parent_id) is None:
            return False, f"父节点不存在：{parent_id}"
        if parent_id == node_id:
            return False, "不能移动到自身"
        node = find(data, node_id)
        for sub, _ in iter_nodes(node.get("children") or []):
            if sub.get("id") == parent_id:
                return False, "不能移动到自己的子树内"
    old_list = find_parent_list(data, node_id)
    if old_list is None:
        return False, f"节点不存在：{node_id}"
    node = find(data, node_id)
    new_list = find(data, parent_id).setdefault("children", []) if parent_id else data.setdefault("nodes", [])
    old_list[:] = [n for n in old_list if n.get("id") != node_id]
    new_list.insert(0, node)
    return True, ""


def node_remove(data: dict, node_id: str):
    """摘除节点（含子树）。返回 (ok, msg, removed_node)。"""
    node = find(data, node_id)
    if node is None:
        return False, f"节点不存在：{node_id}", None
    lst = find_parent_list(data, node_id)
    lst[:] = [n for n in lst if n.get("id") != node_id]
    return True, "", node


# ---------- 命令（全部经 commit，唯一写路径） ----------

def _report(issues: list[str]) -> int:
    """统一输出：契约校验结果 + 孤儿提示。"""
    print("[routes] ROUTES.md 已重绘")
    if issues:
        print("[routes] 契约问题：")
        for i in issues:
            print("  -", i)
        return 1
    print("[routes] 契约校验通过（挂载存在 · 入口文档齐备 · id 唯一）")
    orphans = find_orphans(load())
    if orphans:
        print("[routes] 孤儿资产（未挂路由，可能待挂载）：")
        for o in orphans:
            print("  -", o)
    return 0


def cmd_render() -> int:
    ok, payload = commit(lambda data: (True, ""))
    return _report(payload)


def cmd_add(args) -> int:
    node = {"id": args.id, "type": args.type, "title": args.title}
    if args.mount:
        node["mount"] = args.mount
    if args.description:
        node["description"] = args.description
    ok, payload = commit(lambda data: node_add(data, args.parent, node))
    if not ok:
        print(f"[routes] {payload}")
        return 1
    print(f"[routes] 已添加 {args.id} → {args.parent or '（根）'}")
    return _report(payload)


def cmd_move(args) -> int:
    ok, payload = commit(lambda data: node_move(data, args.id, args.parent))
    if not ok:
        print(f"[routes] {payload}")
        return 1
    print(f"[routes] 已移动 {args.id} → {args.parent or '（根）'}")
    return _report(payload)


def cmd_remove(args) -> int:
    def mutate(data):
        ok, msg, _ = node_remove(data, args.id)
        return ok, msg

    ok, payload = commit(mutate)
    if not ok:
        print(f"[routes] {payload}")
        return 1
    print(f"[routes] 已移除（含子树）：{args.id}")
    return _report(payload)


def cmd_update(args) -> int:
    def mutate(data):
        node = find(data, args.id)
        if node is None:
            return False, f"节点不存在：{args.id}"
        if args.title:
            node["title"] = args.title
        if args.mount:
            node["mount"] = args.mount
        if args.description:
            node["description"] = args.description
        return True, ""

    ok, payload = commit(mutate)
    if not ok:
        print(f"[routes] {payload}")
        return 1
    print(f"[routes] 已更新：{args.id}")
    return _report(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="资产管理层引擎（routes.json ↔ ROUTES.md）")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("render")
    p_add = sub.add_parser("add")
    p_add.add_argument("--parent", default="")     # 省略即挂到根
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--type", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--mount")
    p_add.add_argument("--description")
    p_rm = sub.add_parser("remove")
    p_rm.add_argument("--id", required=True)
    p_mv = sub.add_parser("move")
    p_mv.add_argument("--id", required=True)
    p_mv.add_argument("--parent", default="")     # 省略即移到根
    p_up = sub.add_parser("update")
    p_up.add_argument("--id", required=True)
    p_up.add_argument("--title")
    p_up.add_argument("--mount")
    p_up.add_argument("--description")
    args = parser.parse_args()

    if args.cmd == "add":
        return cmd_add(args)
    if args.cmd == "remove":
        return cmd_remove(args)
    if args.cmd == "move":
        return cmd_move(args)
    if args.cmd == "update":
        return cmd_update(args)
    return cmd_render()


if __name__ == "__main__":
    raise SystemExit(main())
