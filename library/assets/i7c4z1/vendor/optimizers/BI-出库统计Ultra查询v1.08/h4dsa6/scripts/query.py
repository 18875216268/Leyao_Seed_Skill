#!/usr/bin/env python3
"""执行一批 BI 直查：输入 UTF-8 JSON 查询参数（`--payload-file` 或 stdin，二选一）。

用法：
  python scripts/query.py --payload-file plan.json   # 从文件读（推荐：无管道、无转义）
  python scripts/query.py < plan.json                # 从 stdin 读（管道，同款输入）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from bi_client.catalog import load_catalog
from bi_client.cli import configure_stdio, emit
from bi_client.errors import BiError
from bi_client.profile import load_profile
from bi_client.query import QueryService
from bi_client.transport import require_requests


MAX_INPUT_BYTES = 1_000_000


def _read_request(source: Path | None = None) -> dict[str, Any]:
    if source is None:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise BiError("INPUT_NOT_FOUND", f"输入文件不存在：{path}")
        with open(path, "rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise BiError("INPUT_TOO_LARGE", "输入 JSON 超过 1 MB 限制。")
    if not raw.strip():
        raise BiError("INPUT_REQUIRED", "请输入 JSON 查询参数（--payload-file <文件>，或从 stdin 传入）。")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BiError("INPUT_INVALID_JSON", "输入不是有效的 UTF-8 JSON。") from exc
    if not isinstance(value, dict):
        raise BiError("BATCH_INVALID", "输入必须是 JSON 对象。")
    return value


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="执行一批 BI 直查（输入 UTF-8 JSON：--payload-file 或 stdin）")
    parser.add_argument("--payload-file", type=Path, help="JSON 查询参数文件（不传则从 stdin 读）")
    args = parser.parse_args()
    try:
        request = _read_request(args.payload_file)
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
