#!/usr/bin/env python3
"""通用 PMS 接口发送器（AI 直读原样包 → 构造请求 → 发送）。

范式：AI 直接读 vendor/leyo-sys 与 vendor/optimizers 原样文档，
理解接口（host / path / content_type / 必填参数），构造请求，
本脚本只负责把请求发出去并把响应 / 导出文件拿回来。

token 来源优先级：--token > 环境变量 PMS_TOKEN > 自有登录组件凭证仓库最近登录账号
（本地检查，绝不弹窗；仓库为空时给出登录指引）。

请求构造（二选一）：
- 完整 URL：--url <https://.../api/...>
- host 基址 + 路径：--host-key <pmsHost|datacenterHost|authHost>（从 sync_config.json 的
  host_endpoints 解析）配合 --path <相对路径>

传输约定（与集团执行器一致）：
- 集团接口均为 POST（传输层只实现 POST 系列方法），故不提供 method 选项
- content_type 决定请求体形态：application/json（默认） / application/x-www-form-urlencoded
- token 在 header 与 body 双发
- APP_VERSION / platform / MODULE 沿用 pms_common 现有值

与子 skill 的关系：本脚本只发请求，接口「理解」完全交给 AI 读 vendor 原样文档
（按 vendor/SUBSKILL_ROUTING.md 路由）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from pms_common import (
    PmsClient,
    PmsError,
    configure_stdio,
    configured_base,
    stored_token,
    error_payload,
)


def origin_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def build_url(args: argparse.Namespace) -> str:
    if args.url:
        return args.url
    base = configured_base(args.host_key or "", "")
    if not base:
        raise PmsError(
            f"Cannot resolve host base for --host-key {args.host_key!r}. "
            "Use --url <full> or add host_endpoints to sync_config.json."
        )
    path = str(args.path or "").lstrip("/")
    return f"{base.rstrip('/')}/{path}"


def load_payload_file(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.expanduser().exists():
        raise PmsError(f"Payload file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PmsError(f"Cannot read payload file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PmsError(f"Payload file must contain a JSON object: {path}")
    return data


def send(
    url: str,
    token: str,
    payload: dict[str, Any],
    *,
    content_type: str,
    insecure: bool,
    no_proxy: bool,
    out_file: Path | None,
) -> dict[str, Any]:
    """按给定 URL/请求体发起一次调用；out_file 存在时走导出落盘链路。

    集团接口均为 POST（PmsClient 传输层只实现 POST 系列方法），故不暴露 method 选项。
    """
    base = origin_of(url)
    client = PmsClient(token, insecure=insecure, no_proxy=no_proxy, origin=base)
    # token 双发：header 由 PmsClient._headers 提供，body 内再注入一次
    body = dict(payload)
    body["token"] = token
    try:
        if out_file is not None:
            exported = client.post_export(
                url, body, content_type=content_type, destination=out_file
            )
            parsed = exported["body"] or {}
        elif "x-www-form-urlencoded" in content_type:
            parsed = client.post_form(url, body)
        else:
            parsed = client.post_json(url, body)
    except (PmsError, requests.RequestException) as exc:
        raise PmsError(f"Error calling {url}: {exc}") from exc
    result: dict[str, Any] = {
        "ok": True,
        "method": "POST",
        "url": url,
        "content_type": content_type,
        "status_code": 200,
        "body": parsed,
    }
    if out_file is not None:
        result["saved_to"] = exported["saved_to"]
        result["bytes"] = exported["bytes"]
        result["export_mode"] = exported["mode"]
    return result


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="通用 PMS 接口发送器（AI 直读原样包 → 构造请求 → 发送）"
    )
    parser.add_argument("--url", help="完整请求 URL（与 --host-key/--path 二选一）")
    parser.add_argument(
        "--host-key",
        help="sync_config.json host_endpoints 的键名（如 pmsHost/datacenterHost/authHost），配合 --path",
    )
    parser.add_argument("--path", help="相对路径，配合 --host-key 使用")
    parser.add_argument(
        "--content-type",
        default="application/json",
        help="application/json（默认）或 application/x-www-form-urlencoded",
    )
    parser.add_argument("--payload-file", type=Path, help="JSON 请求体文件；token 自动注入，无需手写")
    parser.add_argument(
        "--token",
        help="PMS token；默认取自有登录组件凭证仓库中最近登录账号（也可用 PMS_TOKEN 环境变量）",
    )
    parser.add_argument("--provider-id", help="Convenience: inject providerId if absent in payload")
    parser.add_argument("--output", type=Path, help="Write response JSON to this file")
    parser.add_argument(
        "--out-file",
        type=Path,
        help="导出类接口专用：把结果落盘为文件（自动识别文件流 / data.url 两种导出形态）",
    )
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--no-proxy", action="store_true")
    args = parser.parse_args()

    try:
        url = build_url(args)
        # token 优先级：--token > 环境变量 PMS_TOKEN > 凭证仓库最近登录账号（仅本地检查，不弹窗）
        token = args.token or os.getenv("PMS_TOKEN") or stored_token()
        payload = load_payload_file(args.payload_file)
        if args.provider_id and "providerId" not in payload:
            payload["providerId"] = args.provider_id

        result = send(
            url,
            token,
            payload,
            content_type=args.content_type,
            insecure=args.insecure,
            no_proxy=args.no_proxy,
            out_file=args.out_file,
        )

        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
            print(f"written to {args.output}", file=sys.stderr)
        else:
            print(text)
        return 0
    except (PmsError, requests.RequestException) as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
