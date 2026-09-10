#!/usr/bin/env python3
"""PAT 通道薄封装：凭证窗口 + guancli 调用 + REST 探测（本包不实现登录）。

用法：
  python scripts/pat_call.py --status                     # 凭证窗口状态（不打印令牌明文）
  python scripts/pat_call.py --whoami                     # guancli 身份确认
  python scripts/pat_call.py --sql "SELECT ..." --ds <dsId>
  python scripts/pat_call.py --probe                      # REST 候选端点探测（无副作用）

凭证来源优先级（对齐手册）：
  1. --token <gdpat_…>
  2. 环境变量 BI_PAT_TOKEN
  3. 凭证文件：BI_PAT_CREDENTIAL_FILE 或本包 resources/credential.local.json（{"pat":"gdpat_…"}）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://bi.leyopharm.com"


def _credential(explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("BI_PAT_TOKEN", "").strip()
    if env:
        return env
    configured = os.environ.get("BI_PAT_CREDENTIAL_FILE", "").strip()
    path = Path(configured).expanduser() if configured else SKILL_ROOT / "resources" / "credential.local.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(data.get("pat") or data.get("token") or "").strip()
    return ""


def _guancli(args: list[str]) -> int:
    if shutil.which("guancli") is None:
        print(json.dumps({"ok": False, "error": "guancli 不可用：请先安装 Node ≥20 与 @guandata/guancli（见 references/api与cli.md §2）。"}, ensure_ascii=False))
        return 1
    proc = subprocess.run(["guancli", *args], capture_output=True)
    sys.stdout.write(proc.stdout.decode("utf-8", "replace"))
    sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
    return proc.returncode


def _login_if_needed(pat: str) -> int:
    """guancli 登录（幂等：失败即重新 login，再 whoami 确认）。"""
    if _guancli(["auth", "status", "--profile", "guanbi"]) == 0:
        return 0
    return _guancli(["auth", "login", "--url", BASE_URL, "--pat", pat, "--profile", "guanbi", "--default"])


def _probe(pat: str) -> int:
    """REST 候选端点无副作用探测：只报状态码，不用于生产取数。"""
    try:
        import requests
    except ImportError:
        print(json.dumps({"ok": False, "error": "缺少 requests，请先 python -m pip install requests"}, ensure_ascii=False))
        return 1
    candidates = [
        ("GET", "/public-api/v2/user/info"),
        ("GET", "/api/user/profile"),
        ("POST", "/public-api/dataset/execute-sql"),
    ]
    headers = {"X-Personal-Token": pat, "X-Guandata-Client": "guancli"}
    report = []
    for method, path in candidates:
        try:
            resp = requests.request(
                method, f"{BASE_URL}{path}", headers=headers,
                json={} if method == "POST" else None, timeout=20, allow_redirects=False,
            )
            report.append({"method": method, "path": path, "status": resp.status_code, "body": resp.text[:160]})
        except requests.RequestException as exc:
            report.append({"method": method, "path": path, "error": str(exc)[:160]})
    print(json.dumps({"ok": True, "probe": report, "note": "探测结果仅供登记 REST 端点，未探明前生产取数走 guancli。"}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PAT 通道薄封装（凭证窗口 + guancli/REST 探测）")
    parser.add_argument("--token", help="PAT 令牌（gdpat_…，优先级最高）")
    parser.add_argument("--status", action="store_true", help="凭证窗口状态（不打印令牌明文）")
    parser.add_argument("--whoami", action="store_true", help="guancli 身份确认")
    parser.add_argument("--sql", help="要执行的 SQL")
    parser.add_argument("--ds", help="目标数据集 dsId")
    parser.add_argument("--probe", action="store_true", help="REST 候选端点探测（无副作用）")
    args = parser.parse_args()

    pat = _credential(args.token)

    if args.status:
        has_guancli = shutil.which("guancli") is not None
        print(json.dumps({
            "ok": True,
            "pat": "已配置" if pat else "未配置（--token / BI_PAT_TOKEN / credential.local.json 三选一）",
            "guancli": "可用" if has_guancli else "不可用（需 Node ≥20 + @guandata/guancli）",
            "baseUrl": BASE_URL,
        }, ensure_ascii=False))
        return 0

    if not pat:
        print(json.dumps({"ok": False, "error": "无 PAT 令牌：请在 BI 页面 personal-access-token 领取后，用 --token、BI_PAT_TOKEN 或 credential.local.json 传入。"}, ensure_ascii=False))
        return 1

    if args.probe:
        return _probe(pat)
    if args.whoami:
        return _login_if_needed(pat) or _guancli(["auth", "whoami", "--profile", "guanbi"])
    if args.sql:
        if not args.ds:
            print(json.dumps({"ok": False, "error": "--sql 需要 --ds <dsId> 同用。"}, ensure_ascii=False))
            return 1
        code = _login_if_needed(pat)
        if code != 0:
            return code
        return _guancli(["ds", "execute-sql", "--profile", "guanbi", "--inputs", args.ds, "--sql", args.sql])

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
