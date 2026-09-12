#!/usr/bin/env python3
"""BI 通用请求壳（Cookie 卡片通道执行器）。

本模块只负责：读取登录凭证 → 按调用方给定的 URL/路径发送请求 → 原样返回响应。
它不实现任何具体业务查询、字段解析、分页、导出或业务口径判断；
具体取数方式由 AI 读取 vendor/bi-cookie 文档后决定（host / path / method / 请求体都由 AI 给出）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 登录器与公共底座在 skill 根 scripts/（纯登录框架），本脚本位于 vendor/bi-cookie/scripts/
_FRAMEWORK_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_FRAMEWORK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK_SCRIPTS))

from bi_common import BiClient, BiError, configure_stdio, error_payload, get_credential  # noqa: E402

CONFIG_FILE = Path(__file__).resolve().parents[3] / "sync_config.json"


def load_host(host_key: str) -> str:
    """从 sync_config.json 的 host_endpoints 取主机地址（主机外置，不写死在代码里）。"""
    try:
        config = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BiError("CONFIG_UNREADABLE", f"无法读取 sync_config.json：{exc}") from exc
    hosts = config.get("host_endpoints") or {}
    base = str(hosts.get(host_key) or "").rstrip("/")
    if not base:
        available = "、".join(sorted(hosts)) or "（空）"
        raise BiError("CONFIG_ERROR", f"sync_config.json 缺少 host_endpoints.{host_key}；可用：{available}")
    return base


def load_payload_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    path = path.expanduser()
    if not path.is_file():
        raise BiError("PAYLOAD_NOT_FOUND", f"请求体文件不存在：{path}")
    try:
        # utf-8-sig 同时兼容带 BOM（记事本 / PowerShell 默认）与不带 BOM 的 JSON。
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BiError("PAYLOAD_BAD", f"无法读取请求体文件：{exc}") from exc
    if not isinstance(data, dict):
        raise BiError("PAYLOAD_BAD", "请求体文件必须是 JSON 对象")
    return data


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="BI 文档驱动通用请求壳：AI 读 vendor/bi-cookie 后给出 URL/路径/请求体，本脚本只负责发送"
    )
    parser.add_argument("--url", help="完整 URL（含主机），优先级高于 --host-key/--path")
    parser.add_argument("--host-key", default="biHost", help="sync_config.json 中 host_endpoints 的键名（默认 biHost）")
    parser.add_argument("--path", help="接口路径，如 /api/card/<cardId>/data")
    parser.add_argument("--method", choices=["GET", "POST"], help="默认：有请求体用 POST，否则 GET")
    parser.add_argument("--payload-file", type=Path, help="JSON 请求体文件；内容不改")
    parser.add_argument("--output", type=Path, help="将响应封装写入 JSON 文件")
    parser.add_argument("--relogin", action="store_true", help="强制重新企微扫码登录")
    parser.add_argument("--no-ui", action="store_true", help="凭证不可用时不弹登录窗口")
    parser.add_argument("--no-remote", action="store_true", help="跳过登录凭证远端校验")
    parser.add_argument("--insecure", action="store_true", help="关闭 TLS 校验，仅限受控环境")
    parser.add_argument("--no-proxy", action="store_true", help="不使用系统代理")
    args = parser.parse_args()

    try:
        if args.url:
            target = str(args.url)
        elif args.path:
            base = load_host(args.host_key)
            suffix = args.path if args.path.startswith("/") else f"/{args.path}"
            target = f"{base}{suffix}"
        else:
            parser.error("需要 --url，或 --path（配合 --host-key）")

        payload = load_payload_file(args.payload_file)
        method = args.method or ("POST" if payload else "GET")

        credential = get_credential(
            force_relogin=args.relogin,
            interactive=not args.no_ui,
            validate_remote=not args.no_remote,
        )
        client = BiClient(credential, insecure=args.insecure, no_proxy=args.no_proxy)
        body = client.request(method, target, payload)
        result = {"ok": True, "method": method, "url": target, "body": body}
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.expanduser().parent.mkdir(parents=True, exist_ok=True)
            args.output.expanduser().write_text(text + "\n", encoding="utf-8")
            print(f"已写入 {args.output}", file=sys.stderr)
        else:
            print(text)
        return 0
    except BiError as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
