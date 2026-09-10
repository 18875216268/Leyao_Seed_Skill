#!/usr/bin/env python3
"""资产管理台 · 后端（零依赖）：全部业务委托同层 library/engine 引擎。

职责边界（启动入口见 console.py，本文件是被导入的模块）：
- 引擎（engine）持有唯一事实源 routes.json，并提供唯一写路径 commit 与节点规则；
  本文件只提供"HTTP 适配 + 资产搬运"，不复制引擎逻辑、不自行加锁落盘。
- 资产根为 library/assets/（"主页"）；节点挂载一律落在其下。
- 写操作统一走引擎 `commit`（内含跨进程写锁 + 原子落盘 + 渲染 + 校验），与 CLI 并发安全共存。

接口：
  GET    /api/tree                          路由树 + 类型登记表 + 契约问题 + 孤儿资产
  POST   /api/node                          op=add|update（含关联复制/位置移动）
  POST   /api/render                        重绘 ROUTES.md
  GET    /api/pick-folder                   探针（就绪检查）
  POST   /api/pick-folder                   调起本机原生文件夹对话框
  DELETE /api/node?id=<id>[&purge=1]        删除节点（purge=1 同时删除其资产目录）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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


def _clear_dir(mount: str):
    """清空挂载目录下的内容（保留目录本身）。"""
    if not mount:
        return
    try:
        p = safe_target(mount)
    except ValueError:
        return
    if not p.exists() or not p.is_dir():
        return
    for child in list(p.iterdir()):
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
    """把旧挂载目录下的资产移动到新挂载目录（同名冲突则跳过，最后清理空旧目录）。"""
    if not old_mount or not new_mount or old_mount == new_mount:
        return
    try:
        old = safe_target(old_mount)
        new = safe_target(new_mount)
    except ValueError:
        return
    if not old.exists() or not old.is_dir():
        return
    new.mkdir(parents=True, exist_ok=True)
    for child in list(old.iterdir()):
        dst = new / child.name
        if dst.exists():
            continue
        shutil.move(str(child), str(dst))
    _drop_if_empty(old_mount)


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

def add_node(parent, id_, type_, title, mount, description=None, source=None):
    node = {"id": id_, "type": (type_ or "").strip(), "title": title}
    if mount:
        node["mount"] = mount
    if description:
        node["description"] = description
    if source:
        node["source"] = source

    def mutate(data):
        ok, msg = engine.node_check(data, parent, node)   # 先校验，再搬运资产
        if not ok:
            return False, msg
        if source:
            ok, msg = _copy_into(source, mount or "")
            if not ok:
                return False, msg
        return engine.node_add(data, parent, node)

    return engine.commit(mutate)


def update_node(id_, title, mount, description=None, type_=None, source=None):
    def mutate(data):
        n = engine.find(data, id_)
        if not n:
            return False, "节点不存在"
        old_mount = n.get("mount") or ""
        old_source = n.get("source") or ""
        if title:
            n["title"] = title
        if type_ is not None and str(type_).strip():
            n["type"] = str(type_).strip()
        if mount is not None:
            if mount:
                n["mount"] = mount
            else:
                n.pop("mount", None)
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

        new_mount = n.get("mount") or ""
        new_source = n.get("source") or ""
        # 「来源 = 自身位置」不算改动（原位置丢失后回退显示的就是它自身，避免误清空）
        source_is_self = bool(new_source) and bool(old_mount) and _same_path(new_source, old_mount)
        source_changed = bool(new_source) and new_source != old_source and not source_is_self

        if source_changed:
            # 改关联资产 → 清空「现有位置」下的资产，再把新关联资产复制到目标位置
            if old_mount and not _same_as_target(new_source, old_mount):
                _clear_dir(old_mount)
                if old_mount != new_mount:
                    _drop_if_empty(old_mount)
            if new_mount and not _same_as_target(new_source, new_mount):
                ok, msg = _copy_into(new_source, new_mount)
                if not ok:
                    return False, msg
        elif new_mount and new_mount != old_mount:
            # 改位置 → 移动原有资产到新位置；原位置没有资产才从关联资产复制
            if _has_assets(old_mount):
                _move_assets(old_mount, new_mount)
            elif new_source:
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
        return True, ""

    return engine.commit(mutate)


def do_render():
    return engine.commit(lambda data: (True, ""))


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
                "orphans": engine.find_orphans(data),
            })
        if u.path == "/api/pick-folder":
            # 探针：确认接口已挂载（不弹窗）
            return self._json({"ok": True, "ready": True})
        return self._send(404, "not found")

    def do_POST(self):
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
                ok, msg = add_node(body.get("parent", ""), body["id"], body["type"],
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
        u = urlparse(self.path)
        if u.path == "/api/node":
            qs = parse_qs(u.query)
            id_ = (qs.get("id") or [""])[0]
            purge = (qs.get("purge") or ["0"])[0] in ("1", "true", "yes")
            return self._json(_resp(*remove_node(id_, purge)))
        return self._send(404, "not found")

    def log_message(self, *a):
        pass


def _resp(ok, issues):
    """统一响应：成功时 issues = 契约问题清单（空即健康）；失败时 = 错误说明。"""
    if ok:
        return {"ok": True, "issues": issues}
    return {"ok": False, "msg": issues}


def run(host="127.0.0.1", port=PORT):
    ASSETS.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.serve_forever()
