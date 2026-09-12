#!/usr/bin/env python3
"""乐药云智库「数据库凭证自动登录」桥接脚本（多凭证版）。

功能（零侵入 leyou_cloud.py，只做凭证存取 + 委托 status/login）：
1. 优先使用本地凭证；本地没有或失效 → 自动到数据库按「最新优先」依次尝试所有凭证；
2. 数据库凭证失效 → 立即删除该条（leyou_zhiku/<key>），继续尝试后面的凭证；
3. 所有凭证均失效 → 调起企业微信扫码登录（流程与 leyou_cloud.py login 一致）；
4. 登录成功 → 按「账号身份指纹」写入数据库：同一账号 → 覆盖更新（键保持）；
   不同账号 → 新增（键 = 指纹摘要）。指纹默认取 token 中间段（seg1），
   可用 --id-field 切换（token_seg1/uuid/token）；同时写回本地；
5. 后续使用凭证仍优先本地，重复以上流程。

AI 调用面（只暴露 4 个命令，数据库配置全部封在脚本内，AI 无需感知）：
  python leyou_firebase_login.py auto              一键自动登录（默认含扫码）
  python leyou_firebase_login.py auto --no-scan   只尝试现有凭证，全失效即退出(码3)，不弹扫码
  python leyou_firebase_login.py check-db         只读查看数据库凭证列表
  python leyou_firebase_login.py clear-db --key <key>   删除指定凭证（--all 清空列表）
  python leyou_firebase_login.py push-local       把本地凭证写入数据库（新增/覆盖）

安全边界（结构性保证，非靠确认）：
- 本脚本 Firebase 读写只指向唯一节点 pms/18875216268/leyou_zhiku 及其子条目；
- 不提供任何 URL / 节点名参数 → 无注入面，AI 无法触及其它任何节点；
- 每次请求前经 _assert_safe_url() 白名单断言（仅允许容器读 + 单条凭证写删）；
- 规则层最终兜底：leyou_zhiku 之外任何子节点命中 $other validate:false 被 Firebase 拒绝。

配套前提：Firebase 控制台规则需将 leyou_zhiku 配置为凭证列表容器（$creds）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import quote


# ---------------- Firebase 配置（封在脚本内，AI 不可见） ----------------

class FirGuizeLeixing(StrEnum):
    """云端保存的规则类型。"""

    LEYOU_ZHIKU = "leyou_zhiku"  # 云智库登录凭证列表


@dataclass(frozen=True, slots=True)
class FirPeizhi:
    """Firebase Web 项目参数和 Realtime Database REST 根地址。"""

    api_key: str
    auth_domain: str
    database_url: str
    project_id: str
    storage_bucket: str
    messaging_sender_id: str
    app_id: str
    measurement_id: str
    fangwen_miyue: str

    def guize_dizhi(self, leixing: FirGuizeLeixing) -> str:
        """返回带访问秘钥路径的规则节点 REST 地址。"""

        miyue = quote(self.fangwen_miyue, safe="")
        return f"{self.database_url.rstrip('/')}/pms/{miyue}/{leixing.value}.json"


FIR_PEIZHI = FirPeizhi(
    api_key="AIzaSyAfKRUNy1EbgpJjvhTSI6O5XKDXtniSKmO0",
    auth_domain="shuju-c2c7a.firebaseapp.com",
    database_url="https://shuju-c2c7a-default-rtdb.asia-southeast1.firebasedatabase.app",
    project_id="shuju-c2c7a",
    storage_bucket="shuju-c2c7a.firebasestorage.app",
    messaging_sender_id="694883714744",
    app_id="1:694883714744:web:93427b5f805e31382a164d",
    measurement_id="G-WE9KXGBK9K",
    fangwen_miyue="18875216268",
)

# ---------------- 常量 ----------------

LEYOU_DIR = Path(__file__).resolve().parent
LEYOU_SCRIPT = LEYOU_DIR / "leyou_cloud.py"
TOKEN_FILE = LEYOU_DIR / "leyou_token.json"
ZHIKU_BASE = FIR_PEIZHI.guize_dizhi(FirGuizeLeixing.LEYOU_ZHIKU)  # .../pms/18875216268/leyou_zhiku.json（容器读）
ZHIKU_DIR = ZHIKU_BASE[: -len(".json")]                          # .../pms/18875216268/leyou_zhiku（单条父路径）
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) leyou-firebase-login/0.3"

KEY_RE = re.compile(r"^[0-9a-f]{12}$")  # 凭证键 = token 摘要，12 位十六进制


def _md5_hex(s: str) -> str:
    return hashlib.md5((s or "").encode("utf-8")).hexdigest()


def cred_key(fp: str) -> str:
    """凭证键：账号身份指纹的 md5 摘要前 12 位。

    同账号（同指纹）→ 同键 → 覆盖更新；异账号（异指纹）→ 异键 → 新增。
    """
    return _md5_hex(fp)[:12]


def account_fingerprint(creds, id_field=None):
    """账号身份指纹：用于判断「是否是同一个账号」，决定新增 or 更新。

    凭证中无账号级 ID（HelpLook 无用户信息接口），采用优先级策略：
    - 默认：token 中间段 seg1（32hex，最可能的账号/会话稳定标识；uuid 实测为占位符不可靠）
    - 兜底：uuid（若合理）→ 整个 token
    - 可显式指定 id_field：token_seg1 | uuid | token
    返回 (指纹字符串, 来源说明)。
    """
    c = creds if isinstance(creds, dict) else {}
    token = c.get("token") or ""
    segs = token.split("_")
    seg1 = segs[1] if len(segs) > 2 else ""
    uuid = (c.get("uuid") or "").strip()

    if id_field == "token":
        return token, "token"
    if id_field == "uuid":
        return (uuid or token), ("uuid" if uuid else "token")
    if id_field == "token_seg1":
        return (seg1 or token), ("token_seg1" if seg1 else "token")
    if seg1 and len(seg1) >= 8:
        return seg1, "token_seg1"
    if uuid and uuid.lower() not in ("u", "none", "null", "") and len(uuid) >= 8:
        return uuid, "uuid"
    return token, "token"


def resolve_target_key(container, fp, token, id_field=None):
    """决定写入键：同账号(同指纹) → 复用旧条目键（覆盖更新，键保持稳定）；
    异账号 → 新增指纹键。同时返回需迁移清理的旧格式键（按 token 摘要生成）。

    返回 (target_key, legacy_key)。
    """
    new_key = cred_key(fp)
    target_key = new_key
    for k, node in (container or {}).items():
        try:
            meta = json.loads((node or {}).get("neirong") or "{}")
        except Exception:
            continue
        if not meta.get("token"):
            continue
        if account_fingerprint(meta, id_field)[0] == fp:
            target_key = k  # 同账号 → 覆盖旧条目（键保持稳定）
            break
    legacy_key = cred_key(token)  # 旧版按 token 摘要生成的键，用于迁移清理
    return target_key, legacy_key


# ---------------- Firebase REST（仅标准库） ----------------

def _assert_safe_url(url):
    """白名单断言：只允许 容器读（.../leyou_zhiku.json）与 单条凭证（.../leyou_zhiku/<12hex>.json）。

    唯一路径 = 结构性保证：URL 偏差（含注入）直接抛错，杜绝触及其它任何节点。
    """
    if url == ZHIKU_BASE:
        return
    if url.startswith(ZHIKU_DIR + "/") and url.endswith(".json"):
        tail = url[len(ZHIKU_DIR) + 1: -len(".json")]
        if KEY_RE.match(tail):
            return
    raise ValueError(f"越权访问拦截：不允许操作该路径 {url}")


def _fb_request(method, url, payload=None):
    _assert_safe_url(url)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": UA}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else None


def fb_get_container():
    """读整个凭证列表：{key: {neirong, gengxin_shijian}}；无节点 → None。"""
    return _fb_request("GET", ZHIKU_BASE)


def fb_put_entry(key, neirong, gengxin_shijian):
    """写入单条凭证（同 key 覆盖更新；异 key 新增）。

    注意：Firebase REST 路径必须以 .json 结尾 → .../leyou_zhiku/<key>.json
    """
    return _fb_request("PUT", f"{ZHIKU_DIR}/{key}.json", {"neirong": neirong, "gengxin_shijian": gengxin_shijian})


def fb_delete_entry(key):
    """删除单条失效凭证。"""
    return _fb_request("DELETE", f"{ZHIKU_DIR}/{key}.json")


# ---------------- 委托 leyou_cloud.py ----------------

def _run_leyou(*args):
    """调用 leyou_cloud.py 并解析 JSON；兼容流式输出（取最后一个 JSON 行）。"""
    r = subprocess.run(
        [sys.executable, str(LEYOU_SCRIPT), *args],
        capture_output=True, text=True, timeout=360,
    )
    out = None
    for line in reversed((r.stdout or "").strip().splitlines()):
        try:
            out = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if out is None:
        return {"ok": False, "error": "UNPARSEABLE", "exit_code": r.returncode,
                "stdout": (r.stdout or "")[:500]}
    out["exit_code"] = r.returncode
    return out


def creds_to_local(creds):
    """把凭证写入本地 leyou_token.json，字段与 leyou_cloud.py 兼容。"""
    c = json.loads(creds) if isinstance(creds, str) else (creds or {})
    store = {
        "token": c.get("token"), "uuid": c.get("uuid"),
        "login_at": c.get("login_at"), "expires_at": c.get("expires_at"),
        "watermark": c.get("watermark"),
    }
    TOKEN_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return store


def local_creds():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _try_creds(creds):
    """写入本地并校验；返回 (有效?, status dict)。"""
    creds_to_local(creds)
    st = _run_leyou("status", "--compact")
    return bool(st.get("ok") and st.get("logged_in")), st


# ---------------- 主流程（多凭证依次尝试） ----------------

def auto_login(scan=True, id_field=None):
    """1.本地优先 → 2.库中按时间倒序依次尝试（失效即删）→ 3.全失效扫码 → 4.按账号指纹新增/覆盖写库。"""
    deleted = []

    # 1) 本地凭证优先
    local = local_creds()
    if local.get("token"):
        try:
            ok, st = _try_creds(local)
            if ok:
                return {"ok": True, "source": "local", "logged_in": True,
                        "token": st.get("token"), "uuid": st.get("uuid"),
                        "expires_at": st.get("expires_at")}
            TOKEN_FILE.unlink(missing_ok=True)  # 本地失效 → 清理
        except Exception:
            pass

    # 2) 数据库凭证列表依次尝试（最新优先）
    try:
        container = fb_get_container()
    except Exception as e:
        return {"ok": False, "error": "DB_READ_FAIL", "detail": str(e),
                "action": "降级：继续本地登录"}
    if container:
        items = sorted(
            container.items(),
            key=lambda kv: (kv[1] or {}).get("gengxin_shijian", 0) or 0,
            reverse=True,
        )
        for key, node in items:
            creds = (node or {}).get("neirong")
            if not creds:
                continue
            try:
                ok, st = _try_creds(creds)
                if ok:
                    return {"ok": True, "source": "db", "logged_in": True,
                            "key": key, "token": st.get("token"), "uuid": st.get("uuid"),
                            "expires_at": st.get("expires_at"), "deleted": deleted}
                # 失效 → 删除该条，继续尝试后面的
                try:
                    fb_delete_entry(key)
                    deleted.append(key)
                except Exception:
                    pass
            except Exception:
                pass

    # 3) 全部失效 → 登录
    if not scan:
        return {"ok": False, "error": "LOGIN_REQUIRED",
                "reason": "本地与数据库凭证均无效", "deleted": deleted,
                "hint": "python leyou_firebase_login.py auto 扫码登录"}

    lg = _run_leyou("login", "--compact")
    if not lg.get("ok"):
        return {"ok": False, "error": lg.get("error", "LOGIN_FAIL"),
                "message": lg.get("message", "扫码登录失败"),
                "detail": lg.get("hint", ""), "deleted": deleted}

    # 4) 按账号指纹写入：同账号 → 覆盖更新（键保持）；异账号 → 新增
    token = lg.get("token") or ""
    fp, fp_src = account_fingerprint(lg, id_field)
    save_err, saved, action = None, False, "added"
    try:
        container = fb_get_container() or {}
        target_key, legacy_key = resolve_target_key(container, fp, token, id_field)
        action = "updated" if target_key != cred_key(fp) else "added"
        fb_put_entry(target_key, json.dumps(lg, ensure_ascii=False), int(time.time() * 1000))
        saved = True
        # 迁移清理：旧版按 token 摘要生成的同 token 条目
        if legacy_key in container and legacy_key != target_key:
            try:
                fb_delete_entry(legacy_key)
            except Exception:
                pass
    except Exception as e:
        save_err = str(e)
    creds_to_local(lg)  # 本地也更新，下次优先本地
    return {"ok": True, "source": "scan", "logged_in": True,
            "key": target_key if not save_err else None,
            "action": action, "fingerprint_source": fp_src,
            "token": token, "uuid": lg.get("uuid"),
            "expires_at": lg.get("expires_at"),
            "db_saved": saved, "db_save_error": save_err, "deleted": deleted}


def main(argv=None):
    p = argparse.ArgumentParser(description="乐药云智库数据库凭证自动登录（多凭证版）")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auto", help="一键自动登录（默认含扫码）")
    a.add_argument("--no-scan", action="store_true", help="只尝试现有凭证，全失效即退出(码3)")
    a.add_argument("--id-field", choices=["token_seg1", "uuid", "token"], default=None,
                   help="账号指纹来源：token_seg1(默认)/uuid/token")
    sub.add_parser("check-db", help="只读查看数据库凭证列表")
    c = sub.add_parser("clear-db", help="删除数据库凭证")
    c.add_argument("--key", default=None, help="删除指定凭证键")
    c.add_argument("--all", action="store_true", help="清空凭证列表")
    psh = sub.add_parser("push-local", help="把本地 leyou_token.json 凭证写入数据库（按账号指纹新增/覆盖）")
    psh.add_argument("--id-field", choices=["token_seg1", "uuid", "token"], default=None,
                     help="账号指纹来源：token_seg1(默认)/uuid/token")
    args = p.parse_args(argv)

    try:
        if args.cmd == "auto":
            out = auto_login(scan=not args.no_scan, id_field=args.id_field)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if out.get("ok") else (3 if out.get("error") in ("LOGIN_REQUIRED", "DB_READ_FAIL") else 5)
        if args.cmd == "check-db":
            container = fb_get_container()
            if not container:
                print(json.dumps({"ok": True, "count": 0, "node": "leyou_zhiku"}, ensure_ascii=False, indent=2))
                return 0
            entries = []
            for key, node in sorted(container.items(),
                                    key=lambda kv: (kv[1] or {}).get("gengxin_shijian", 0) or 0,
                                    reverse=True):
                creds = (node or {}).get("neirong")
                meta = {}
                try:
                    meta = json.loads(creds) if isinstance(creds, str) else {}
                except Exception:
                    meta = {"parse_error": True}
                entries.append({
                    "key": key, "gengxin_shijian": (node or {}).get("gengxin_shijian"),
                    "token_masked": (meta.get("token") or "")[:6] + "…" if meta.get("token") else None,
                    "expires_at": meta.get("expires_at"), "watermark": meta.get("watermark"),
                })
            print(json.dumps({"ok": True, "count": len(entries), "node": "leyou_zhiku",
                              "entries": entries}, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "clear-db":
            if not (args.key or args.all):
                print(json.dumps({"ok": False, "error": "ARG_REQUIRED",
                                  "hint": "请指定 --key <键> 删除单条，或 --all 清空列表（--all 会逐个删除）"},
                                 ensure_ascii=False, indent=2), file=sys.stderr)
                return 2
            if args.key:
                fb_delete_entry(args.key)
                print(json.dumps({"ok": True, "action": "deleted", "key": args.key}, ensure_ascii=False, indent=2))
                return 0
            container = fb_get_container() or {}
            for key in container:
                fb_delete_entry(key)
            print(json.dumps({"ok": True, "action": "cleared", "count": len(container)}, ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "push-local":
            c = local_creds()
            if not c.get("token"):
                print(json.dumps({"ok": False, "error": "NO_LOCAL_TOKEN",
                                  "hint": "本地 leyou_token.json 无 token，先执行 leyou_cloud.py login"},
                                 ensure_ascii=False, indent=2), file=sys.stderr)
                return 3
            fp, fp_src = account_fingerprint(c, args.id_field)
            container = fb_get_container() or {}
            target_key, legacy_key = resolve_target_key(container, fp, c["token"], args.id_field)
            action = "updated" if target_key != cred_key(fp) else "added"
            fb_put_entry(target_key, json.dumps(c, ensure_ascii=False), int(time.time() * 1000))
            if legacy_key in container and legacy_key != target_key:
                try:
                    fb_delete_entry(legacy_key)
                except Exception:
                    pass
            print(json.dumps({"ok": True, "action": action, "key": target_key,
                              "fingerprint_source": fp_src,
                              "token_masked": c["token"][:6] + "…",
                              "expires_at": c.get("expires_at")}, ensure_ascii=False, indent=2))
            return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": "EXEC_FAIL", "detail": str(e)},
                         ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
