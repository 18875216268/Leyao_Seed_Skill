#!/usr/bin/env python3
"""资产管理台 · 后端（零依赖）：全部业务委托同层 library/engine 引擎。

职责边界（启动入口见 console.py，本文件是被导入的模块）：
- 引擎（engine）持有唯一事实源 routes.json，并提供唯一写路径 commit 与节点规则；
  本文件只提供"HTTP 适配 + 资产搬运"，不复制引擎逻辑、不自行加锁落盘。
- 资产根为 library/assets/（"主页"）；节点挂载一律落在其下。
- 管理台语义「位置即归属」：归属由位置推导（engine.nearest_card 取最近一层卡片）；
  改位置 = 搬移（卡片 + 资产目录，含子树联动：子卡片目录随走、其 mount 前缀同步改写）。
- 写操作统一走引擎 `commit`（内含跨进程写锁 + 原子落盘 + 渲染 + 校验），与 CLI 并发安全共存。

接口：
  GET    /api/tree                          路由树 + 类型登记表 + 契约问题 + 孤儿资产
  GET    /api/exists?path=<本机路径>          复制源存在性探测（打开编辑弹窗时用）
  GET    /api/belong?mount=<挂载路径>         按位置求归属（最近一层卡片 id；空 = 主页顶层）
  POST   /api/node                          op=add|update（归属由位置推导；含获取复制 / 位置搬移 / 子树联动）
  POST   /api/render                        重绘 ROUTES.md
  GET    /api/pick-folder                   探针（就绪检查）
  POST   /api/pick-folder                   调起本机原生文件夹对话框
  DELETE /api/node?id=<id>[&purge=1]        删除节点（purge=1 同时删除其资产目录）
  DELETE /api/orphan?path=<挂载路径>         删除孤儿资产目录（限资产根内、未被挂载引用）
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.dont_write_bytecode = True          # 运行期零写包（不在包内生成 __pycache__）

HERE = Path(__file__).resolve().parent  # leyao-seed-core/library/admin
WEB = HERE / "web"
LIB = HERE.parent                       # ★ 管理台驻留在资产管理层内：leyao-seed-core/library
if not (LIB / "engine.py").is_file():
    raise SystemExit(
        f"[admin] 未找到资产管理层引擎：{LIB / 'engine.py'}\n"
        f"[admin] 管理台必须位于 library/admin/（当前：{HERE}）"
    )
sys.path.insert(0, str(LIB))
import engine                           # noqa: E402

REPO_ROOT = LIB.parent                  # leyao-seed-core/
ASSETS = LIB / "assets"                 # ★ 资产根（主页）
PICK_SCRIPT = HERE / "pick_folder.py"   # 原生文件夹对话框（tkinter 独立进程）
PORT = 8765


# ---------- 路径与资产搬运 ----------

def safe_target(mount: str) -> Path:
    """挂载路径必须落在 leyao-seed-core/ 内，防路径穿越。"""
    p = (REPO_ROOT / mount).resolve()
    root = REPO_ROOT.resolve()
    if p != root and root not in p.parents:
        raise ValueError(f"挂载路径越界: {mount}")
    return p


def _copy_into(source_abs: str, mount: str):
    """把来源文件夹的内容复制到挂载目录（不移动原件）。"""
    if not source_abs or not mount:
        return True, ""
    src = Path(source_abs)
    if not src.exists() or not src.is_dir():
        return False, f"关联的资产文件夹不存在: {source_abs}"
    try:
        target = safe_target(mount)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, dirs_exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return False, f"复制资产失败: {e}"
    return True, ""


def _clear_dir(mount: str, keep=frozenset()):
    """清空挂载目录下的内容（保留目录本身；keep 指定的子项名跳过——用于保护子卡片目录）。"""
    if not mount:
        return
    try:
        p = safe_target(mount)
    except ValueError:
        return
    if not p.exists() or not p.is_dir():
        return
    for child in list(p.iterdir()):
        if child.name in keep:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        except Exception:  # noqa: BLE001
            pass


def _drop_if_empty(mount: str):
    """若挂载目录已空则删除它。"""
    if not mount:
        return
    try:
        p = safe_target(mount)
        if p.exists() and p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    except Exception:  # noqa: BLE001
        pass


def _drop_empty_parents(mount: str):
    """从旧挂载目录起向上回收空目录（限资产根内）：清掉移动后遗留的中间层壳目录。"""
    if not mount:
        return
    try:
        p = safe_target(mount)
    except ValueError:
        return
    root = ASSETS.resolve()
    while p != root and root in p.resolve().parents:
        if p.exists():
            try:
                if not p.is_dir() or any(p.iterdir()):
                    break
                p.rmdir()
            except OSError:
                break
        p = p.parent


def _has_assets(mount: str) -> bool:
    """挂载目录下是否已有资产。"""
    if not mount:
        return False
    try:
        p = safe_target(mount)
    except ValueError:
        return False
    try:
        return p.is_dir() and any(p.iterdir())
    except Exception:  # noqa: BLE001
        return False


def _same_as_target(source_abs: str, mount: str) -> bool:
    """来源是否位于目标目录之内（等于或在其中），防止自清空 / 自复制丢数据。"""
    try:
        src = Path(source_abs).resolve()
        tgt = safe_target(mount)
        return src == tgt or tgt in src.parents
    except Exception:  # noqa: BLE001
        return False


def _same_path(a_abs: str, mount: str) -> bool:
    """某个绝对路径是否就是挂载目录本身。"""
    try:
        return Path(a_abs).resolve() == safe_target(mount)
    except Exception:  # noqa: BLE001
        return False


def _move_assets(old_mount: str, new_mount: str):
    """把旧挂载目录下的资产移动到新挂载目录（同名冲突则跳过，最后清理空旧目录）。

    返回「因目标同名已存在而未搬走」的子项名集合（供子树 mount 改写避让）。
    """
    skipped = set()
    if not old_mount or not new_mount or old_mount == new_mount:
        return skipped
    try:
        old = safe_target(old_mount)
        new = safe_target(new_mount)
    except ValueError:
        return skipped
    if not old.exists() or not old.is_dir():
        return skipped
    new.mkdir(parents=True, exist_ok=True)
    for child in list(old.iterdir()):
        dst = new / child.name
        if dst.exists():
            skipped.add(child.name)
            continue
        shutil.move(str(child), str(dst))
    _drop_if_empty(old_mount)
    return skipped


def _descendants(node, old_mount: str):
    """节点子树中位于旧位置内的子卡片：返回 [(节点, 相对路径, 首段名)]。

    位置即归属 ⇒ 子卡片目录必在父卡片目录内；搬移 / 槽位重置 / mount 前缀改写三处共用。
    """
    old = engine.norm_mount(old_mount)
    out = []
    if not old:
        return out
    for sub, _ in engine.iter_nodes(node.get("children") or []):
        m = engine.norm_mount(sub.get("mount"))
        if m and engine.mount_under(m, old):
            rel = m[len(old) + 1:]
            if rel:
                out.append((sub, rel, rel.split("/")[0]))
    return out


def _rmtree_long(path: Path):
    """删除深层目录（Windows 长路径安全）。"""
    p = str(path.resolve())
    if sys.platform.startswith("win"):
        bs = chr(92)
        prefix = bs + bs + "?" + bs
        if not p.startswith(prefix):
            p = prefix + p
    shutil.rmtree(p, ignore_errors=True)


def _annotate_source(nodes):
    """给每个节点标注其原位置是否还存在（供前端决定显示原位置还是位置）。"""
    for n in nodes or []:
        s = n.get("source")
        n["source_exists"] = bool(s and Path(s).exists())
        _annotate_source(n.get("children"))


# ---------- 原生文件夹对话框 ----------

def pick_folder(initial: str = "", mode: str = "dest"):
    """调起本机原生文件夹选择对话框。

    mode="source"：任意本机文件夹（资产来源），返回绝对路径。
    mode="dest"  ：限制在资产根 library/assets/ 内，返回相对项目根的挂载路径。
    """
    assets = ASSETS.resolve()

    if mode == "source":
        base = Path(initial) if initial and Path(initial).exists() else Path.home()
    else:
        try:
            base = (REPO_ROOT / initial).resolve() if initial else assets
        except Exception:  # noqa: BLE001
            base = assets
        if not (base == assets or assets in base.parents) or not base.exists():
            base = assets

    if not PICK_SCRIPT.exists():
        return False, "缺少 pick_folder.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(PICK_SCRIPT), "--initial", str(base)],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},   # 子进程零写包
        )
    except Exception as e:  # noqa: BLE001
        return False, f"无法启动文件夹对话框: {e}"
    if proc.returncode == 3:
        return False, "本机 Python 未安装 tkinter，无法弹出文件夹对话框"
    out = (proc.stdout or "").strip()
    if not out:
        return False, "已取消"

    p = Path(out).resolve()
    if mode == "source":
        return True, str(p)
    if not (p == assets or assets in p.parents):
        return False, "所选文件夹需位于资产目录 library/assets/ 内"
    rel = p.relative_to(assets).as_posix()       # "." 表示 assets 本身
    return True, engine.ASSETS_MOUNT + ("" if rel == "." else rel + "/")


# ---------- 业务：增 / 改 / 删 / 渲染 ----------

def add_node(id_, type_, title, mount, description=None, source=None):
    """新增卡片：位置即归属（归属由位置推导）；获取 = 复制源复制；位置空 → 默认主页。"""
    node = {"id": id_, "type": (type_ or "").strip(), "title": title}
    m = (mount or "").strip() or engine.ASSETS_MOUNT + id_ + "/"
    node["mount"] = m
    if description:
        node["description"] = description
    if source:
        node["source"] = source

    def mutate(data):
        parent = engine.nearest_card(data, m)             # 位置即归属
        ok, msg = engine.node_check(data, parent, node)   # 先校验，再搬运资产
        if not ok:
            return False, msg
        if source:
            ok, msg = _copy_into(source, m)
            if not ok:
                return False, msg
        else:
            try:                                          # 占位：卡片槽位先就绪（无源可复制时）
                safe_target(m).mkdir(parents=True, exist_ok=True)
            except ValueError:
                return False, f"挂载路径越界: {m}"
        return engine.node_add(data, parent, node)

    return engine.commit(mutate)


def _parent_of(nodes, nid, pid=""):
    """节点当前所在父 id（顶层为 ""）。"""
    for n in nodes or []:
        if n.get("id") == nid:
            return pid
        hit = _parent_of(n.get("children") or [], nid, n.get("id"))
        if hit is not None:
            return hit
    return None


def update_node(id_, title, mount, description=None, type_=None, source=None):
    """位置即归属：归属由位置推导；改位置 = 搬移（含子树联动）；改获取 = 重置槽位为复制源。"""
    def mutate(data):
        n = engine.find(data, id_)
        if not n:
            return False, "节点不存在"
        old_mount = n.get("mount") or ""
        old_source = n.get("source") or ""
        # 位置：缺省 = 不动；空值 = 默认主页（<资产根>/<卡片id>/）
        new_mount = old_mount if mount is None else ((mount or "").strip() or engine.ASSETS_MOUNT + id_ + "/")
        if engine.mount_under(new_mount, old_mount):
            return False, "位置不能在本卡片自己的目录内（会自我嵌套）"
        # ① 位置即归属：推导目标父（最近一层卡片）→ 变了就移树（含成环校验，失败整单放弃）
        want_parent = engine.nearest_card(data, new_mount, exclude_id=id_) if new_mount else ""
        if want_parent != (_parent_of(data.get("nodes"), id_) or ""):
            ok, msg = engine.node_move(data, id_, want_parent)
            if not ok:
                return False, msg
        # ② 字段写入
        if title:
            n["title"] = title
        if type_ is not None and str(type_).strip():
            n["type"] = str(type_).strip()
        if mount is not None:
            n["mount"] = new_mount
        if description is not None:
            if description:
                n["description"] = description
            else:
                n.pop("description", None)
        if source is not None:
            if source:
                n["source"] = source
            else:
                n.pop("source", None)
        new_source = n.get("source") or ""
        # 「来源 = 自身位置」不算改动（原位置丢失后回退显示的就是它自身，避免误清空）
        source_is_self = bool(new_source) and bool(old_mount) and _same_path(new_source, old_mount)
        source_changed = bool(new_source) and new_source != old_source and not source_is_self
        mount_changed = engine.norm_mount(new_mount) != engine.norm_mount(old_mount)
        kids = _descendants(n, old_mount)                     # 子卡片目录（搬移/重置/改写共用）
        keep = {top for _, _, top in kids}
        # ③ 资产搬运：改位置 = 移动（含子卡片目录）；改获取 = 重置槽位为复制源
        if mount_changed:
            moved = _has_assets(old_mount)
            skipped = _move_assets(old_mount, new_mount) if moved else set()
            if source_changed and not _same_as_target(new_source, new_mount):
                _clear_dir(new_mount, keep)                   # 重置槽位：只清自己的旧内容，保留子卡片目录
                ok, msg = _copy_into(new_source, new_mount)
                if not ok:
                    return False, msg
            elif not moved and new_source and not source_is_self:
                ok, msg = _copy_into(new_source, new_mount)   # 原位置无资产 → 从复制源取
                if not ok:
                    return False, msg
            try:                                              # 槽位占位：挂载目录必须存在（防死链）
                safe_target(new_mount).mkdir(parents=True, exist_ok=True)
            except ValueError:
                pass
            # ④ 子树联动：子卡片 mount 前缀改写（未被搬走的跳过：目录仍在原处）
            old_p, new_p = engine.norm_mount(old_mount), engine.norm_mount(new_mount)
            if old_p and new_p:
                for sub, rel, top in kids:
                    if top in skipped:
                        continue
                    sub["mount"] = new_p + "/" + rel + "/"
            _drop_empty_parents(old_mount)
        elif source_changed and not _same_as_target(new_source, new_mount):
            _clear_dir(old_mount, keep)                       # 重置槽位：只清自己的旧内容，保留子卡片目录
            ok, msg = _copy_into(new_source, new_mount)
            if not ok:
                return False, msg
        return True, ""

    return engine.commit(mutate)


def remove_node(id_, purge: bool = False):
    def mutate(data):
        ok, msg, node = engine.node_remove(data, id_)
        if not ok:
            return False, msg
        if purge:
            # 只删资产根内的目录（资产根外的路径不动）
            mount = node.get("mount") or ""
            if mount:
                try:
                    target = safe_target(mount)
                    if target.is_dir() and ASSETS.resolve() in target.resolve().parents:
                        _rmtree_long(target)
                    elif target.exists() and target.is_file():
                        target.unlink()
                except ValueError:
                    pass
                _drop_empty_parents(mount)
        return True, ""

    return engine.commit(mutate)


def remove_orphan(mount: str):
    """删除孤儿资产目录：限资产根内、且当前未被任何卡片挂载引用（服务端双重复核）。"""
    if not mount:
        return False, "缺少 path"
    try:
        target = safe_target(mount)
    except ValueError:
        return False, "路径越界"
    assets = ASSETS.resolve()
    if not (target.resolve() == assets or assets in target.resolve().parents) or target.resolve() == assets:
        return False, "只允许删除资产根 library/assets/ 内的目录"
    used = set()
    for n, _ in engine.iter_nodes(engine.load().get("nodes")):
        m = (n.get("mount") or "").replace("\\", "/").rstrip("/")
        if m:
            used.add(m)
    if mount.replace("\\", "/").rstrip("/") in used:
        return False, "该目录正被卡片挂载引用，不能按孤儿删除"
    if not target.is_dir():
        return False, "目录不存在"
    _rmtree_long(target)
    _drop_empty_parents(mount)
    return True, ""


def do_render():
    return engine.commit(lambda data: (True, ""))


# ---------- 长驻进程自愈：引擎更新 → 重载并重绘（防"旧进程用旧规则渲染"的静默失真） ----------

_ENGINE_MTIME = 0.0


def _fresh_engine() -> None:
    """engine.py 的 mtime 变了（框架被更新）→ 重载引擎 + 立即重绘一次。

    实测教训（2026-09-12）：管理台是**长驻进程**，改了 engine.py 后旧进程仍按旧规则渲染，
    表现为"改了代码但总图/局部图不按新规则生成"——静默失真 ✗。本函数在每个 HTTP 请求入口调用：
    首次请求只记基线；发现更新则 reload + commit(noop) 重绘，并打印到控制台（不静默）。
    """
    global _ENGINE_MTIME
    try:
        mt = Path(engine.__file__).resolve().stat().st_mtime
    except OSError:
        return
    if not _ENGINE_MTIME or mt == _ENGINE_MTIME:
        _ENGINE_MTIME = mt
        return
    import importlib
    importlib.reload(engine)                               # 同一模块对象原地重载，既有引用仍有效
    _ENGINE_MTIME = mt
    ok, payload = engine.commit(lambda data: (True, ""))
    print("[admin] 检测到 engine.py 更新 → 已重载并重绘（%s）"
          % ("健康" if ok and not payload else payload), flush=True)


# ---------- HTTP 处理 ----------

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=2))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _serve_file(self, path: Path, ctype):
        path = path.resolve()
        if WEB != path and WEB not in path.parents:
            return self._send(403, "forbidden")
        if not path.exists():
            return self._send(404, "not found")
        self._send(200, path.read_bytes(), ctype or self._guess(path))

    @staticmethod
    def _guess(p: Path) -> str:
        return {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
        }.get(p.suffix, "application/octet-stream")

    def do_GET(self):
        _fresh_engine()
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._serve_file(WEB / "index.html", "text/html; charset=utf-8")
        if u.path.startswith("/static/"):
            return self._serve_file(WEB / u.path[len("/static/"):], None)
        if u.path == "/api/tree":
            data = engine.load()
            _annotate_source(data.get("nodes"))
            return self._json({
                "version": data.get("version"),
                "updated": data.get("updated"),
                "nodes": data.get("nodes"),
                "types": engine.known_types(data),
                "root_name": ASSETS.name,
                "root": str(REPO_ROOT),
                "mount_prefix": engine.ASSETS_MOUNT,
                "issues": engine.validate(data, REPO_ROOT),
                "hints": engine.hints(data, REPO_ROOT),
                "orphans": engine.find_orphans(data),
            })
        if u.path == "/api/exists":
            qs = parse_qs(u.query)
            p = (qs.get("path") or [""])[0]
            try:
                exists = bool(p) and Path(p).exists()
            except OSError:
                exists = False
            return self._json({"ok": True, "exists": exists})
        if u.path == "/api/belong":
            qs = parse_qs(u.query)
            return self._json({"ok": True, "id": engine.nearest_card(engine.load(), (qs.get("mount") or [""])[0])})
        if u.path == "/api/pick-folder":
            # 探针：确认接口已挂载（不弹窗）
            return self._json({"ok": True, "ready": True})
        return self._send(404, "not found")

    def do_POST(self):
        _fresh_engine()
        u = urlparse(self.path)
        if u.path == "/api/node":
            body = json.loads(self._read_body() or b"{}")
            op = body.get("op", "add")
            if op == "update":
                ok, msg = update_node(body["id"], body.get("title"),
                                      body.get("mount"),
                                      body.get("description"), body.get("type"),
                                      body.get("source"))
            else:
                ok, msg = add_node(body["id"], body["type"],
                                   body.get("title", body["id"]),
                                   body.get("mount"),
                                   body.get("description"), body.get("source"))
            return self._json(_resp(ok, msg))
        if u.path == "/api/render":
            return self._json(_resp(*do_render()))
        if u.path == "/api/pick-folder":
            payload = json.loads(self._read_body() or b"{}")
            ok, res = pick_folder(payload.get("initial", ""), payload.get("mode", "dest"))
            if ok:
                return self._json({"ok": True, "path": res})
            return self._json({"ok": False, "msg": res})
        return self._send(404, "not found")

    def do_DELETE(self):
        _fresh_engine()
        u = urlparse(self.path)
        if u.path == "/api/node":
            qs = parse_qs(u.query)
            id_ = (qs.get("id") or [""])[0]
            purge = (qs.get("purge") or ["0"])[0] in ("1", "true", "yes")
            return self._json(_resp(*remove_node(id_, purge)))
        if u.path == "/api/orphan":
            qs = parse_qs(u.query)
            return self._json(_resp(*remove_orphan((qs.get("path") or [""])[0])))
        return self._send(404, "not found")

    def log_message(self, *a):
        pass


def _resp(ok, issues):
    """统一响应：成功时 issues = 契约问题清单（空即健康）；失败时 = 错误说明。"""
    if ok:
        return {"ok": True, "issues": issues}
    return {"ok": False, "msg": issues}


def run(host="127.0.0.1", port=None):
    port = port or PORT                  # 默认值在**调用时**解析（改 PORT / 指定端口都能生效，便于测试与换端口）
    ASSETS.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.serve_forever()
