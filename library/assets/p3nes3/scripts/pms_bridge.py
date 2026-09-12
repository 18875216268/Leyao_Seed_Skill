

#!/usr/bin/env python3
"""【已废弃 · 存档】原桥接器已由 `vendor/SUBSKILL_ROUTING.md` 的「桥接指引」（引导式）取代 ✗。
**本文件全部内容（含下方“用法”段）均为历史存档：不得执行、不得复制、不构成任何调用路径** ✗。
保留本文件仅为历史对照，不构成任何调用路径 ✗。见 git 记录/工作区设计稿。"去校验"桥接（派生自 vendor/leyo-sys，不改原包 ✗）。

用法（用户区运行，产物落用户区）：
  pms_bridge.py gen  --pkg <leyo-sys 目录> --out <bridge 输出目录>      # 派生：抽取 动作→路径+字段 映射
  pms_bridge.py list --bridge <dir>                                     # 列出可直打的动作
  pms_bridge.py run  --bridge <dir> --action <动作名> [--body-file f.json] [--body '<json>']
                    [--start "yyyy-MM-dd HH:mm:ss"] [--end ...] [--business-types "4"]  # 便捷组装
说明：鉴权一律使用**自有登录器产出的 token**（凭证文件/环境变量），不读取也不要求集团 Agent Key；
     仅访问 datacenter/web 域（该域接受自有 token ✓）；失败即如实报错、不伪装 ✗。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CRED = Path.home() / ".promo_profit_monitor" / "credential.json"
HOST = "https://pms.leyopharm.com"


def creds():
    tok = os.environ.get("PMS_TOKEN") or ""
    pid = os.environ.get("PMS_PROVIDER_ID") or ""
    if not tok and CRED.exists():
        c = json.loads(CRED.read_text(encoding="utf-8"))
        tok = c.get("token") or ""
        pid = pid or str(c.get("providerId") or c.get("provider_id") or "")
    return tok, pid


def scan(pkg: Path) -> dict:
    """从集团包 scripts/index.js 抽取「动作→路径+字段」与「路径→字段表」。"""
    js = pkg / "scripts" / "index.js"
    t = js.read_text(encoding="utf-8", errors="replace")
    varmap = dict(re.findall(r'([A-Za-z_$][\w$]*)\s*=\s*"(/(?:datacenter_pms|api)/[^"]+)"', t))
    fields = {}
    for var, path in varmap.items():
        m = re.search(r'' + re.escape(var) + r'\s*=\s*\[([^\]]{0,6000})\]', t)
        if m:
            fields[path] = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', m.group(1))
    acts = {}
    for act, var in re.findall(r'"([a-z0-9_.]+)"\s*:\s*([A-Za-z_$][\w$]*)', t):
        if var in varmap:
            p = varmap[var]
            acts[act] = {"path": p, "fields": fields.get(p, [])}
    return {"actions": acts, "paths": {p: fields.get(p, []) for p in varmap.values()}}


def cmd_gen(a):
    pkg, out = Path(a.pkg), Path(a.out)
    data = scan(pkg)
    if not data["actions"]:
        print(json.dumps({"ok": False, "error": "未从 index.js 抽到任何动作（包结构变化？）"}, ensure_ascii=False))
        return 1
    h = hashlib.sha256((pkg / "scripts" / "index.js").read_bytes()).hexdigest()[:12]
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(
        {"base_pkg": str(pkg), "index_js_sha12": h, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
         "tool": "pms_bridge/1.0"}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "routes.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "actions": len(data["actions"]), "sha12": h, "out": str(out)}, ensure_ascii=False))
    return 0


def cmd_list(a):
    d = json.loads((Path(a.bridge) / "routes.json").read_text(encoding="utf-8"))
    for k in sorted(d["actions"]):
        print("  %-42s %s" % (k, d["actions"][k]["path"]))
    print("  共 %d 个动作" % len(d["actions"]))
    return 0


def cmd_run(a):
    br = Path(a.bridge)
    man = json.loads((br / "manifest.json").read_text(encoding="utf-8"))
    hitsha = hashlib.sha256((Path(man["base_pkg"]) / "scripts" / "index.js").read_bytes()).hexdigest()[:12]
    if hitsha != man["index_js_sha12"]:
        print(json.dumps({"ok": False, "error": "桥接已失效：集团包已更新（哈希 %s ≠ %s）→ 重新 gen" % (hitsha, man["index_js_sha12"])}, ensure_ascii=False))
        return 1
    routes = json.loads((br / "routes.json").read_text(encoding="utf-8"))
    act = routes["actions"].get(a.action)
    if not act:
        print(json.dumps({"ok": False, "error": "未知动作：%s（用 list 查看）" % a.action}, ensure_ascii=False))
        return 1
    tok, pid = creds()
    if not tok:
        print(json.dumps({"ok": False, "error": "缺自有 token（PMS_TOKEN 或凭证文件）"}, ensure_ascii=False))
        return 1
    body = {}
    if a.body_file:
        body = json.loads(Path(a.body_file).read_text(encoding="utf-8"))
    elif a.body:
        body = json.loads(a.body)
    body.setdefault("providerId", int(pid) if str(pid).isdigit() else pid)
    body.setdefault("timeType", 1)
    if a.start and a.end:
        body.update({"dateTimeStart": a.start, "dateTimeEnd": a.end})
    if a.business_types:
        body["businessTypes"] = [int(x) for x in a.business_types.split(",") if x.strip()]
    body["token"] = tok
    req = urllib.request.Request(HOST + act["path"], data=json.dumps(body, ensure_ascii=False).encode(),
                                 method="POST", headers={"token": tok, "Content-Type": "application/json",
                                                         "User-Agent": "pms_bridge/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read(6000).decode("utf-8", "replace"))
        print(json.dumps({"ok": bool(j.get("data")), "code": j.get("code"), "message": j.get("message"),
                          "path": act["path"], "data": j.get("data")}, ensure_ascii=False)[:4000])
        return 0 if j.get("data") else 1
    except urllib.error.HTTPError as e:
        print(json.dumps({"ok": False, "http": e.code, "path": act["path"]}, ensure_ascii=False))
        return 1
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description="集团包桥接（派生·去校验；仅用自有 token）")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen"); g.add_argument("--pkg", required=True); g.add_argument("--out", required=True); g.set_defaults(f=cmd_gen)
    l = sub.add_parser("list"); l.add_argument("--bridge", required=True); l.set_defaults(f=cmd_list)
    r = sub.add_parser("run"); r.add_argument("--bridge", required=True); r.add_argument("--action", required=True)
    r.add_argument("--body-file", default=""); r.add_argument("--body", default="")
    r.add_argument("--start", default=""); r.add_argument("--end", default=""); r.add_argument("--business-types", default="")
    r.set_defaults(f=cmd_run)
    a = p.parse_args()
    return a.f(a)


if __name__ == "__main__":
    raise SystemExit(main())
