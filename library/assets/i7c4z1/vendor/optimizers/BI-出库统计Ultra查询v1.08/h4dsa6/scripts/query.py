#!/usr/bin/env python3
"""Execute one batch of direct BI queries from UTF-8 JSON stdin."""

from __future__ import annotations

import json
import sys
from typing import Any

from bi_client.catalog import load_catalog
from bi_client.cli import configure_stdio, emit
from bi_client.errors import BiError
from bi_client.profile import load_profile
from bi_client.query import QueryService
from bi_client.transport import require_requests


MAX_INPUT_BYTES = 1_000_000


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise BiError("INPUT_TOO_LARGE", "输入 JSON 超过 1 MB 限制。")
    if not raw.strip():
        raise BiError("INPUT_REQUIRED", "请通过 stdin 传入 JSON 查询参数。")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BiError("INPUT_INVALID_JSON", "stdin 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(value, dict):
        raise BiError("BATCH_INVALID", "输入必须是 JSON 对象。")
    return value


def main() -> int:
    configure_stdio()
    try:
        request = _read_request()
        require_requests()
        results = QueryService(load_profile(), load_catalog()).run_many(request)
        emit({"ok": True, "results": results})
        return 0
    except BiError as exc:
        emit({"ok": False, "error": exc.to_dict()})
        return 1
    except Exception:
        emit({
            "ok": False,
            "error": BiError("INTERNAL_ERROR", "BI 查询发生未分类错误。").to_dict(),
        })
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
