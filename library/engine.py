#!/usr/bin/env python3
"""资产管理层引擎：routes.json（单一事实源）→ ROUTES.md（人读级联总图）+ routes/<id>.md（分片节点局部图）。

对外契约（CLI 与管理台共用本引擎，禁止第二实现）：
- 唯一写路径 = `commit(mutate)`：加锁 → 读 → 改 → 原子落盘 routes.json → 渲染 ROUTES.md → 契约校验。
- 节点增删规则集中在 `node_check` / `node_add` / `node_remove`，CLI 与管理台一律复用，禁止各自实现。
- **基础卡片**（`title` 首个字符 `@`，兼容全角 `＠`；如 `@行业知识库`）是路由树地基，**不可删除**：
  守卫唯一实现在 `node_remove`（自身拒绝 + 子树含基础卡片的祖先拒绝整体删除）；
  管理台按钮同源禁用（`/api/tree` 注入 `base` 标注，前端不重复规则）。
- 遍历集中在 `iter_nodes`；查父列表集中在 `find_parent_list`（`find` 复用前者）。
- 管理台语义「位置即归属」的唯一实现 = `nearest_card`（基座 `norm_mount` / `mount_under`）：
  位置落在哪个文件夹，节点就归到其**最近一层卡片**下（无卡片 → 主页顶层）。CLI 为维护者低级工具，
  `--parent` / `--mount` 仍可分别指定，不受本规则约束。
- 类型（type）为自由文本，默认登记「方法论 / Skill包」，实际出现过的类型自动汇入登记表。
- 节点只有唯一挂载字段 `mount`。
- **默认资产**（可选，至多一个）：顶层 `defaults{default, hook}`——`default` = 节点 id（"每次任务必读"的资产；
  "读它"由任务层判据 0.5 执行；**引擎只负责注册与呈现，不解析资产内容** ✗——卡健康检查归资产 `card.py check`）；
  `hook` 当前**只支持 `read`**（每任务必读）；若要新增钩子语义（如改为"交付前必经"），**先补判据再加枚举**（不先预留 ✗）。
- 资产根 `ASSETS` = library/assets/；挂载前缀 `ASSETS_MOUNT` 由其推导，禁止另行硬编码。
- 契约校验 `validate`（唯一实现，CLI / 管理台 / 自检 / 库存体检共用）——**硬契约（会拦截/报警）**：
  挂载路径必须存在（防死链）、路由 id 必须唯一、分形局部图一致（超阈节点必须有局部图 · 局部图不得残留）、
  默认资产注册合法（至多一个 · 指向真实节点 · 必带 mount · hook 合法）。
- 描述（路由判据 H1）**写入不拦**：用户/管理员怎么写都行（自由文本合法）；
  六段结构化属**路由能力等级（推荐，非门槛）**——齐备 → 判据链全能力；自由 → 关键词降级匹配。
  降级**不静默**：`hints` 提示 + `ROUTES.md` 行内标注；模板见 `processor/shapes.md` 第 7 节。
- 软提示 `hints`（唯一实现，同样四端共用）——**不算问题、不拦截**：挂载目录未附入口文档（SKILL.md / README.md）；
  描述自由/未写、六段超软上限（`DESC_FIELD_MAX`，建议精简）。
  **资产 ≠ Skill**：一张卡片可以放任意内容（资料 / 数据 / 工具 / 文档…），纯资料型资产不需要入口文档；
  入口文档只在"要被 AI 按其指引调用"时才有价值（处理器命中后读它；没有则退回自带判据自做）。
- 孤儿资产（未被任何节点挂载引用的目录）只提示不拦截：它可能是"已放入、待挂载"的合法中间态。

用法：
  python library/engine.py                                   # 重绘 ROUTES.md + 校验
  python library/engine.py render                            # 同上（显式子命令写法，完全等价）
  python library/engine.py add --id <id> --type <类型> --title "<标题>" [--parent <父id>] [--mount <挂载>] [--description "<何时用>"]
  python library/engine.py remove --id <节点id>               # 连同其子树一并摘除（基础卡片拒绝）
  python library/engine.py move --id <节点id> [--parent <父id>]   # 移动到新文件夹（省略 --parent 即移到根）
  python library/engine.py update --id <节点id> [--title T] [--mount <挂载>] [--description "<何时用>"]
  python library/engine.py default --id <节点id>             # 设默认资产（hook=read：每次任务必读）
  python library/engine.py default --clear                   # 取消默认资产
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
sys.dont_write_bytecode = True          # 运行期零写包（不在包内生成 __pycache__）

LIB = Path(__file__).resolve().parent
ROUTES_JSON = LIB / "routes.json"
ROUTES_MD = LIB / "ROUTES.md"
ASSETS = LIB / "assets"                  # 资产根（"主页"）
ASSETS_MOUNT = ASSETS.relative_to(LIB.parent).as_posix() + "/"   # 挂载前缀由路径推导，禁止另行硬编码
DEFAULT_TYPES = ["方法论", "Skill包"]     # 类型登记表默认值（自由文本，可扩展）
LOCAL_MAPS = LIB / "routes"              # 局部路由图目录（分片节点的子树；总图指针指向它 → 任意级联）
INLINE_KIDS_MAX = 5                      # 分片阈值①：子节点数超过此值的子树收进局部图
INLINE_SUBTREE_MAX = 20                  # 分片阈值②：**子树节点总数**超过此值也收进局部图（防"每层都 ≤5 但很深"）
DEFAULT_HOOKS = ("read",)                # 默认资产钩子（当前仅 read——新增语义先补判据再加枚举，见模块契约）

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
    """读事实源 routes.json；缺失 / 损坏 → **可读错误**（CLI 与管理台兜住，不抛裸栈）。"""
    try:
        return json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError("事实源缺失：%s（用管理台或引擎 add 重建，或从备份恢复）" % ROUTES_JSON) from None
    except json.JSONDecodeError as e:
        raise RuntimeError("事实源损坏：%s 第 %d 行 JSON 解析失败（修好或恢复备份后重跑）"
                           % (ROUTES_JSON, e.lineno)) from None


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
        render_all(data)
        return True, validate(data, LIB.parent)


# ---------- 遍历 / 查询（唯一遍历实现） ----------

def iter_nodes(nodes, depth: int = 0):
    """先序遍历，产出 (node, depth)。全引擎唯一遍历实现。"""
    for n in nodes or []:
        yield n, depth
        yield from iter_nodes(n.get("children") or [], depth + 1)


def subtree_sizes(nodes) -> dict:
    """每棵子树的节点总数（含自身）。供分片判定用——**深而窄**的树只有靠它才不会漏判。"""
    out = {}

    def walk(ns) -> int:
        total = 0
        for n in ns or []:
            s = 1 + walk(n.get("children") or [])
            out[n.get("id")] = s
            total += s
        return total

    walk(nodes)
    return out


def needs_split(node: dict, sizes: dict) -> bool:
    """分片判定（**唯一实现**：总图 / 局部图 / 校验 / 报告共用）：
    ① 子节点数 > INLINE_KIDS_MAX（宽）或 ② 子树节点总数 > INLINE_SUBTREE_MAX（深而窄）。"""
    return (len(node.get("children") or []) > INLINE_KIDS_MAX
            or sizes.get(node.get("id"), 0) > INLINE_SUBTREE_MAX)


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
    """路由**硬契约**校验（唯一实现，CLI / 管理台 / 自检 / 库存体检共用）——会拦截/报警的四类：

    - 死链：挂载路径必须真实存在；
    - id 重复：路由 id 必须唯一（否则无法唯一定位）；
    - 分形死链：超内联上限的节点必须有局部图，局部图必须对应分片节点（防残留）；
    - 默认资产：`defaults` 至多一个 · `default` 必须指向真实节点 · 该节点必须有 mount（容器不可当默认）· hook 合法。

    入口文档缺失**不在此列**（资产 ≠ Skill，资料型资产无需入口文档）——见 `hints()`。
    """
    issues, ids = [], []
    for n, _ in iter_nodes(data.get("nodes")):
        nid = n.get("id")
        ids.append(nid)
        m = n.get("mount")
        if not m:
            continue
        if not (root / m).exists():
            issues.append(f"{nid}: mount 路径不存在 → {m}")
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        issues.append(f"{dup}: 路由 id 重复")
    # 默认资产注册契约（"读它"由任务层判据 0.5 执行；引擎只校验注册面，不解析资产内容 ✗）
    d = data.get("defaults") or {}
    if d:
        did = d.get("default")
        node = next((n for n, _ in iter_nodes(data.get("nodes")) if n.get("id") == did), None) if did else None
        hook = d.get("hook", "read")
        if not did:
            issues.append("defaults: 缺 default（用 `engine.py default --clear` 清除）")
        elif node is None:
            issues.append(f"defaults.default 指向不存在节点：{did}")
        elif not node.get("mount"):
            issues.append(f"{did}: 默认资产必须有 mount（容器节点不可当默认——没有实体可读）")
        if hook not in DEFAULT_HOOKS:
            issues.append(f"defaults.hook 非法：{hook}（可选 {'|'.join(DEFAULT_HOOKS)}）")
    # 分形路由一致性：分片节点必须有局部图；局部图不得对应无关节点（防死链 / 防残留）
    sizes = subtree_sizes(data.get("nodes"))
    for n, _ in iter_nodes(data.get("nodes")):
        if needs_split(n, sizes) and not (LOCAL_MAPS / f"{n.get('id')}.md").exists():
            issues.append(f"{n.get('id')}: 子树超分片阈值（子节点 > {INLINE_KIDS_MAX} 或子树节点 > {INLINE_SUBTREE_MAX}）"
                          f" → 缺少局部路由图 routes/{n.get('id')}.md")
    if LOCAL_MAPS.exists():
        big = {n.get("id") for n, _ in iter_nodes(data.get("nodes")) if needs_split(n, sizes)}
        for f in sorted(LOCAL_MAPS.glob("*.md")):
            if f.stem not in big:
                issues.append(f"routes/{f.name}: 多余局部图（对应节点不存在或未超内联上限）→ 跑 engine.py 重绘即清理")
    return issues


def hints(data: dict, root: Path) -> list[str]:
    """**软提示**（非问题、不拦截）：① 挂载目录未附入口文档（SKILL.md / README.md）；
    ② 描述为自由文本 / 未写（→ 路由降级匹配或不参与，建议补齐）；
    ③ 描述六段某段超过软上限（`DESC_FIELD_MAX`，建议精简）。

    只提示不判定：卡片可以放任意内容——需要"被 AI 按文档调用"的资产才建议补；
    纯资料 / 数据 / 工具型资产可忽略（处理器命中后无入口文档时退回自带判据自做）。
    """
    out = []
    for n, _ in iter_nodes(data.get("nodes")):
        for note in desc_length_notes(n.get("description") or ""):
            out.append(f"{n.get('id')}: {note}")
        st = desc_state(n.get("description"))
        if st == "free":
            out.append(f"{n.get('id')}: 描述为自由文本（未按六段模板）→ 路由按关键词降级匹配；"
                       "建议补齐（模板见 processor/shapes.md 第 7 节，也可让 AI 起草后粘贴）")
        elif st == "empty":
            out.append(f"{n.get('id')}: 无描述 → 无法参与路由匹配（补一句『何时用』即可被召回）")
        m = n.get("mount")
        if not m:
            continue
        p = root / m
        if p.is_dir() and not ((p / "SKILL.md").exists() or (p / "README.md").exists()):
            out.append(f"{n.get('id')}: 未附入口文档（SKILL.md / README.md）→ {m}"
                       "（提示，非问题：资料型资产可忽略；需按文档调用时建议补一个）")
    tops = data.get("nodes") or []
    if len(tops) > INLINE_KIDS_MAX:
        out.append(f"顶层节点 {len(tops)} 个（> {INLINE_KIDS_MAX}）→ 建议建容器节点分组："
                   "分组后自动分片出局部图，总图保持一屏可读")
    return out


def used_segments(data: dict) -> set:
    """资产根下「首层目录」中被节点挂载引用的集合（孤儿判定与管理台删除复核的**唯一口径**）。"""
    used = set()
    for n, _ in iter_nodes(data.get("nodes")):
        v = (n.get("mount") or "").replace("\\", "/")
        if v.startswith(ASSETS_MOUNT):
            seg = v[len(ASSETS_MOUNT):].strip("/").split("/")[0]
            if seg:
                used.add(seg)
    return used


def find_orphans(data: dict) -> list[str]:
    """孤儿资产：资产根下未被任何节点挂载引用的目录。"""
    used = used_segments(data)
    if not ASSETS.is_dir():
        return []
    # 只认"像资产的目录"：跳过点开头目录（隐藏/运行态，如资产误写在包内的 `.leyao-kb` ✗）——
    # 它们不是资产，不该出现在孤儿报告里；包内运行态另由 `run_checks.paths_external` 拦截（分工不重复）。
    return [ASSETS_MOUNT + p.name + "/" for p in sorted(ASSETS.iterdir())
            if p.is_dir() and not p.name.startswith(".") and p.name not in used]


# ---------- 渲染 ----------

def render(data: dict) -> str:
    """渲染 **agent 路由面**（瘦身版：只留路由必需信息）。

    设计依据见 `参考/工具引导调研/04-深挖与实验.md`：①顶部不放"快照/更新时间"——保持**稳定前缀**
    （提示缓存友好，改一行即失效）；②散文开销曾是 agent 读入量的 30%（903 token）→ 图例 4 行
    （注册默认资产时另加 ★ 行）；③维护命令块移入 `library/admin/README.md`（agent 不需要）。
    """
    lines = ["# 资产管理层 · 总路由地图"]
    d = data.get("defaults") or {}
    if d.get("default"):
        dn = next((n for n, _ in iter_nodes(data.get("nodes")) if n.get("id") == d["default"]), None) or {}
        mnt = f" → `{dn['mount']}`" if dn.get("mount") else ""
        lines.append(f"> ★ 默认资产（每次任务必读）：`{d['default']}` **{dn.get('title', '')}**{mnt}"
                     f"——卡在**用户数据区** `data/assets/{d['default']}/card.md`（框架挂载态 `.leyao-data/…`；"
                     "独立态 `~/.leyao-kb/card.md`）；只用于识别与定位（定义以池 authority 为准）；"
                     "读法与刷新见其 `references/card.md`，判据见 `processor/flow/3-execute.md` 0.5。")
    lines += [
        "> 读者：agent 与审阅者；**维护**请用管理台（`library/admin/`）或 `routes.json`（唯一事实源，本图由 `engine.py` 生成）。",
        "> 路由：按节点**描述**匹配 → 命中进其 `→ 挂载` 目录读 `SKILL.md`／`README.md` 调用；无命中按自带判据亲做。"
        "描述形态：六段齐备=判据链全能力；`（自由描述·降级匹配）`=关键词级；`（无描述·不可路由）`（模板见 `processor/shapes.md` 第 7 节）。",
        f"> 级联：`（N 个子节点 → 局部图 library/routes/<id>.md）` → 读局部图继续匹配（可再分片 → 任意级联），叶节点执行"
        f"（分片阈值：子节点 > {INLINE_KIDS_MAX} 或 子树节点 > {INLINE_SUBTREE_MAX}）。",
        "> 回写：交付后 `--routed <命中节点id>`；无命中写 `none`。同读用户区 `.leyao-data/data/memory.md`"
        "（命中「失效模式」先规避、命中「有效做法」直接复用）。",
        "",
    ]

    sizes = subtree_sizes(data.get("nodes"))

    def walk(ns, depth):
        for n in ns:
            indent = "  " * depth
            mount = f" → `{n['mount']}`" if n.get("mount") else ""
            kids = n.get("children") or []
            split = bool(kids) and needs_split(n, sizes)
            suffix = ""
            if kids:
                # 分形路由：超阈值（宽 / 深）的子树收进局部图，总图只留指针（可任意级联，规模与总图行数解耦）
                suffix = (f"（{len(kids)} 个子节点 → 局部图 `{local_map_rel(n)}`）" if split
                          else f"（{len(kids)} 个子节点）")
            st = desc_state(n.get("description"))
            flag = {"empty": "（无描述·不可路由）", "free": "（自由描述·降级匹配）"}.get(st, "")
            lines.append(f"{indent}- `{n.get('id')}` **{n.get('title')}** `{n.get('type')}`{mount}{suffix}{flag}")
            if n.get("description"):
                lines.append(f"{indent}  - _{n['description']}_")
            if kids and not split:
                walk(kids, depth + 1)

    walk(data.get("nodes") or [], 0)
    return "\n".join(lines)          # 维护命令块已移入 library/admin/README.md（agent 不需要；保持路由面瘦身）


def local_map_rel(node: dict) -> str:
    """局部图在仓库内的相对路径（总图指针与局部图互引的唯一写法；`<id>` 占位符由 doc_refs 跳过）。"""
    return (LOCAL_MAPS / f"{node.get('id')}.md").relative_to(LIB.parent).as_posix()


def local_map(data: dict, node: dict) -> str:
    """局部路由图（纯函数）：分片节点的子树；子层同样按阈值再分片 → **任意级联**。"""
    kids = node.get("children") or []
    sizes = subtree_sizes(kids)
    lines = [
        f"# 局部路由图 · {node.get('title')}（`{node.get('id')}`）",
        "",
        f"> 属于总图 `library/ROUTES.md` ｜ 本层 {len(kids)} 个子节点 ｜ 由 `library/engine.py` 生成，勿手工编辑。",
        "> 级联下钻：本层仍按描述匹配；命中带指针的子节点 → 再读其局部图（可任意级联），叶节点进 `→ 挂载` 执行。",
        "",
    ]

    def walk(ns, depth):
        for n in ns:
            indent = "  " * depth
            mount = f" → `{n['mount']}`" if n.get("mount") else ""
            sub = n.get("children") or []
            split = bool(sub) and needs_split(n, sizes)
            suffix = ""
            if sub:
                suffix = (f"（{len(sub)} 个子节点 → 局部图 `{local_map_rel(n)}`）" if split
                          else f"（{len(sub)} 个子节点）")
            st = desc_state(n.get("description"))
            flag = {"empty": "（无描述·不可路由）", "free": "（自由描述·降级匹配）"}.get(st, "")
            lines.append(f"{indent}- `{n.get('id')}` **{n.get('title')}** `{n.get('type')}`{mount}{suffix}{flag}")
            if n.get("description"):
                lines.append(f"{indent}  - _{n['description']}_")
            if sub and not split:
                walk(sub, depth + 1)

    walk(kids, 0)
    return "\n".join(lines)


def render_all(data: dict) -> tuple[list[str], list[str]]:
    """写总图 + 分片节点的局部图，并清理不再分片的旧图（包内零残留）→ (已写, 已删)。"""
    _write_atomic(ROUTES_MD, render(data))
    sizes = subtree_sizes(data.get("nodes"))
    wanted = {}
    for n, _ in iter_nodes(data.get("nodes")):
        if n.get("id") and needs_split(n, sizes):
            wanted[n["id"]] = local_map(data, n)
    removed = []
    if LOCAL_MAPS.exists():
        for f in sorted(LOCAL_MAPS.glob("*.md")):
            if f.stem not in wanted:
                f.unlink()
                removed.append(f.stem)
    if wanted:
        LOCAL_MAPS.mkdir(parents=True, exist_ok=True)
        for nid, txt in sorted(wanted.items()):
            _write_atomic(LOCAL_MAPS / f"{nid}.md", txt)
    elif LOCAL_MAPS.exists() and not any(LOCAL_MAPS.iterdir()):
        LOCAL_MAPS.rmdir()                 # 无可分片节点：不留空目录
    return sorted(wanted), sorted(removed)


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


BASE_CARD_PREFIXES = ("@", "＠")          # 基础卡片前缀（半角 @ / 全角 ＠——中文输入法可能产出全角）


def is_base(node: dict) -> bool:
    """基础卡片判定（唯一实现）：标题首个字符为 @（兼容全角 ＠；忽略首尾空白）。

    基础卡片是路由树地基（如 `@行业知识库` / `@通用导航库`），不可删除——守卫见 `node_remove`；
    管理台以本函数为唯一规则源（`/api/tree` 注入 `base` 标注，前端不重复判定）。
    """
    return str(node.get("title") or "").strip().startswith(BASE_CARD_PREFIXES)


def node_remove(data: dict, node_id: str):
    """摘除节点（含子树）。返回 (ok, msg, removed_node)。

    基础卡片保护：标题 @ 开头的节点不可删除；子树含基础卡片的祖先也不可整体删除
    （避免"删父级顺带删掉地基"——先把基础卡片移出该子树再删）。
    """
    node = find(data, node_id)
    if node is None:
        return False, f"节点不存在：{node_id}", None
    if is_base(node):
        return False, f"{node_id}: 基础卡片（名称首个字符为 @）不可删除", None
    blocked = [n.get("id") for n, _ in iter_nodes(node.get("children") or []) if is_base(n)]
    if blocked:
        return False, (f"{node_id}: 子树含基础卡片（{'、'.join(blocked)}），不可整体删除"
                       "——先把基础卡片移出该子树"), None
    lst = find_parent_list(data, node_id)
    lst[:] = [n for n in lst if n.get("id") != node_id]
    return True, "", node


# ---------- 命令（全部经 commit，唯一写路径） ----------

def _report(issues: list[str]) -> int:
    """统一输出：硬契约结果 + 软提示 + 孤儿提示。"""
    data_now = load()
    sizes = subtree_sizes(data_now.get("nodes"))
    local = [n["id"] for n, _ in iter_nodes(data_now.get("nodes"))
             if n.get("id") and needs_split(n, sizes)]
    print("[routes] ROUTES.md 已重绘" + (f"（局部图 {len(local)} 张：{', '.join(local)}）" if local else ""))
    if issues:
        print("[routes] 契约问题（硬）：")
        for i in issues:
            print("  -", i)
        return 1
    print("[routes] 契约校验通过（挂载存在 · id 唯一 · 分形图一致 · 默认资产合法）")
    hs = hints(load(), LIB.parent)
    if hs:
        print("[routes] 提示（非问题，不影响使用）：")
        for h in hs:
            print("  -", h)
    orphans = find_orphans(load())
    if orphans:
        print("[routes] 孤儿资产（未挂路由，可能待挂载）：")
        for o in orphans:
            print("  -", o)
    return 0


# ---------- 描述六段模板（路由判据 H1：**推荐**的能力等级，非写入门槛） ----------
# 自由描述合法（用户/管理员怎么写都行）→ 路由按关键词降级匹配，且降级在 hints 与 ROUTES.md 显式标注。

DESC_FIELDS = ("【何时用】", "【不适用】", "【别名】", "【输入前置】", "【时效性】", "【回退】")
DESC_FIELD_MAX = 120      # 每段字符软上限（超出仅在 hints 里提醒，不拦截）


def desc_problems(desc: str) -> list[str]:
    """**结构化检查（软）**：缺哪些六段字段；不拦截写入（模板与依据见 processor/shapes.md 第 7 节）。"""
    desc = desc or ""
    return ["缺字段 %s" % f for f in DESC_FIELDS if f not in desc]


def desc_state(desc: str) -> str:
    """描述形态（路由能力等级）：structured（六段齐备）｜ free（自由文本）｜ empty（未写）。"""
    d = (desc or "").strip()
    if not d:
        return "empty"
    return "structured" if not desc_problems(d) else "free"


def alias_problems(desc: str, title: str = "") -> list[str]:
    """【别名】自查（SKOS 精神）：别名不得与正式名（标题）相同；别名之间不得重复。"""
    import re as _re
    m = _re.search(r"【别名】([^｜|【\n]*)", desc or "")
    if not m:
        return []
    raw = m.group(1).strip(" 　")
    items = [x.strip(" 　") for x in _re.split(r"[、,，/／]", raw) if x.strip(" 　")]
    probs = []
    if title and title.strip() and title.strip() in items:
        probs.append("别名不得与节点标题相同：%s" % title.strip())
    dup = sorted({x for x in items if items.count(x) > 1})
    if dup:
        probs.append("别名内部重复：%s" % dup)
    return probs


def desc_length_notes(desc: str) -> list[str]:
    """六段各自的字符数超软上限 → 提示（不拦截）。"""
    import re as _re
    out = []
    for f in DESC_FIELDS:
        m = _re.search(_re.escape(f) + r"([^｜|【\n]*)", desc or "")
        if m and len(m.group(1).strip(" 　")) > DESC_FIELD_MAX:
            out.append("%s 段落超过 %d 字（建议精简）" % (f, DESC_FIELD_MAX))
    return out


def _note_desc(desc: str, title: str = "") -> None:
    """描述写入后的**软提示**（不拦截）：自由文本合法，但路由按关键词降级匹配。"""
    if desc_state(desc) != "structured":
        print("[routes] 提示：描述为自由文本（未按六段模板）→ 路由按关键词降级匹配"
              "（不保证『不适用 / 回退』判断）；模板见 processor/shapes.md 第 7 节")
    for p in alias_problems(desc, title):
        print("[routes] 提示：%s" % p)


def cmd_render() -> int:
    ok, payload = commit(lambda data: (True, ""))
    return _report(payload)


def cmd_add(args) -> int:
    node = {"id": args.id, "type": args.type, "title": args.title}
    if args.mount:
        node["mount"] = args.mount
    if args.description:
        node["description"] = args.description
        _note_desc(args.description, args.title)
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
            node["description"] = args.description          # 自由/结构化均可写入（不拦截）
        return True, ""

    ok, payload = commit(mutate)
    if not ok:
        print(f"[routes] {payload}")
        return 1
    print(f"[routes] 已更新：{args.id}")
    if args.description:
        _note_desc(args.description, (find(load(), args.id) or {}).get("title", ""))
    return _report(payload)


def cmd_default(args) -> int:
    """设/清**默认资产**（每次任务必读；至多一个）——唯一写路径 commit（与其它命令同规）。"""
    def mutate(data):
        if args.clear:
            if args.id or args.hook:
                return False, "--clear 与 --id/--hook 互斥（二者选一）"
            if not (data.get("defaults") or {}):
                return False, "当前未注册默认资产（无需清除）"
            data.pop("defaults", None)
            return True, ""
        if not args.id:
            return False, "需 --id <节点id>（或 --clear 取消）"
        node = find(data, args.id)
        if node is None:
            return False, f"节点不存在：{args.id}"
        if not node.get("mount"):
            return False, f"{args.id}: 默认资产必须有 mount（容器节点不可当默认——没有实体可读）"
        hook = args.hook or (data.get("defaults") or {}).get("hook") or "read"
        if hook not in DEFAULT_HOOKS:
            return False, f"hook 非法：{hook}（可选 {'|'.join(DEFAULT_HOOKS)}）"
        data["defaults"] = {"default": args.id, "hook": hook}
        return True, ""

    ok, payload = commit(mutate)
    if not ok:
        print(f"[routes] {payload}")
        return 1
    if args.clear:
        print("[routes] 已取消默认资产")
    else:
        hook_now = (load().get("defaults") or {}).get("hook", "read")   # 回显**实际落盘值**（省略 --hook 时保留现值）
        print(f"[routes] 默认资产 = {args.id}（hook={hook_now}）")
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
    p_df = sub.add_parser("default")
    p_df.add_argument("--id", default="")          # 设默认资产（与 --clear 二选一）
    p_df.add_argument("--hook", default="")        # 仅 read（省略 = 保留现值 / read）
    p_df.add_argument("--clear", action="store_true")
    args = parser.parse_args()

    try:
        if args.cmd == "add":
            return cmd_add(args)
        if args.cmd == "remove":
            return cmd_remove(args)
        if args.cmd == "move":
            return cmd_move(args)
        if args.cmd == "update":
            return cmd_update(args)
        if args.cmd == "default":
            return cmd_default(args)
        return cmd_render()
    except (RuntimeError, OSError) as e:                    # 可读报错，不抛裸栈（写锁超时 / 事实源损坏 / 磁盘）
        print("[routes] %s" % e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
