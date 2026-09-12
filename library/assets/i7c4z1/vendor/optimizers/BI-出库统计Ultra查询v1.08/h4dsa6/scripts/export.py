#!/usr/bin/env python3
"""导出本板聚合查询结果为 xlsx：引擎构造请求体 → UI 采集同款三步异步链。

链路（与观远 UI「离线导出」逐字节一致，见 references/api查询文档.md §4）：
  ① POST /api/write/file/{cardId}?typeOp=EXCEL   请求体 = 取数请求体（filters 必带；可带 zoneFilter 克隆）
  ② GET  /api/task/{taskId}                      轮询至 FINISHED（失败 FAILED）
  ③ POST /api/export/file/common/{taskId}        体 {"time","fileNameWithTime","downloadFileName"} → xlsx 二进制流

用法（stdin 与 query.py 同款 DSL 批次，取首个查询构造请求体）：
  python scripts/export.py --out <输出.xlsx> [--timeout 300] [--poll-interval 5]
  python scripts/export.py --task <taskId>            # 超时/中断后续传（轮询+下载）
  python scripts/export.py --list 20                  # 导出中心任务列表（找回 taskId）

红线：本卡禁止无筛选导出（服务端全量 50×33 透视需 20+ 分钟）；请求体必须至少带日期筛选。
输出：JSON 到 stdout；进度提示到 stderr。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from bi_client.catalog import load_catalog
from bi_client.cli import configure_stdio, emit
from bi_client.credentials import load_credential
from bi_client.errors import BiError
from bi_client.profile import load_profile
from bi_client.query import QueryService
from bi_client.transport import DirectTransport, require_requests

MAX_INPUT_BYTES = 1_000_000
_TERMINAL_OK = {"FINISHED"}
_TERMINAL_FAIL = {"FAILED", "FAIL", "CANCELLED", "CANCELED"}
_SAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]')


def _hint(message: str) -> None:
    print(f"[export] {message}", file=sys.stderr, flush=True)


def _read_plan() -> dict[str, Any]:
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
    queries = value.get("queries")
    if isinstance(queries, list) and queries:
        if len(queries) > 1:
            _hint("批次含多个查询，仅导出第一个；其余请分批导出。")
        return queries[0]
    return value


def _unwrap(body: dict[str, Any]) -> dict[str, Any]:
    inner = body.get("response")
    return inner if isinstance(inner, dict) else body


def _submit(transport: DirectTransport, body: dict[str, Any]) -> tuple[str, str]:
    data = _unwrap(transport.json(transport.export_submit(body)))
    task_id = str(data.get("taskId") or "")
    if not task_id:
        raise BiError("EXPORT_SUBMIT_FAILED", f"提交导出未返回 taskId：{json.dumps(data, ensure_ascii=False)[:200]}")
    post_body = data.get("postBody") if isinstance(data.get("postBody"), dict) else {}
    name = str(post_body.get("downloadFileName") or data.get("fileName") or "bi_export")
    return task_id, name


def _poll(transport: DirectTransport, task_id: str, timeout: int, interval: int) -> str:
    deadline = time.time() + max(1, timeout)
    waited = 0
    while True:
        data = _unwrap(transport.json(transport.task_status(task_id)))
        status = str(data.get("status") or "").upper()
        if status in _TERMINAL_OK:
            return status
        if status in _TERMINAL_FAIL:
            raise BiError("EXPORT_FAILED", f"导出任务失败（{status}）：{json.dumps(data, ensure_ascii=False)[:200]}")
        if time.time() >= deadline:
            raise BiError("EXPORT_TIMEOUT", f"等待导出超时（{timeout}s）；任务可能仍在服务端执行，可用 --task {task_id} 续传")
        time.sleep(max(1, interval))
        waited += max(1, interval)
        if waited % 15 == 0:
            _hint(f"导出中… 已等待 {waited}s（当前 {status or '未知'}）")


def _download(transport: DirectTransport, task_id: str, out_path: Path, name: str) -> int:
    body = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S.000+08:00"),
        "fileNameWithTime": True,
        "downloadFileName": name,
    }
    total = 0
    with transport.export_download(task_id, body) as resp:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    handle.write(chunk)
                    total += len(chunk)
    if total < 2 or out_path.read_bytes()[:2] != b"PK":
        raise BiError("EXPORT_DOWNLOAD_FAILED", "下载内容不是有效的 xlsx（缺少 PK 文件头）。")
    return total


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="导出本板聚合查询结果为 xlsx（三步异步链）")
    parser.add_argument("--out", help="输出 xlsx 路径（默认 ./<卡名>_<时间戳>.xlsx）")
    parser.add_argument("--task", help="已有导出任务 id（跳过提交，直接轮询+下载）")
    parser.add_argument("--list", type=int, default=None, metavar="LIMIT", help="查看导出中心任务列表，不提交/下载")
    parser.add_argument("--timeout", type=int, default=300, help="等待导出完成的最长秒数（默认 300）")
    parser.add_argument("--poll-interval", type=int, default=5, help="轮询间隔秒数（默认 5）")
    args = parser.parse_args()

    try:
        require_requests()
        profile, catalog = load_profile(), load_catalog()
        transport = DirectTransport(profile, load_credential())

        if args.list is not None:
            data = _unwrap(transport.json(transport.export_task_history(args.list)))
            items = data.get("data") if isinstance(data.get("data"), list) else []
            emit({"ok": True, "count": len(items), "tasks": items})
            return 0

        stamp = time.strftime("%Y%m%d_%H%M%S")
        default_name = str(profile.get("cardName") or "bi_export")
        out_path = Path(args.out).expanduser() if args.out else Path.cwd() / f"{_SAFE_NAME.sub('_', default_name)[:80]}_{stamp}.xlsx"

        if args.task:
            task_id, name = str(args.task), default_name
            _hint(f"续传任务 {task_id}")
        else:
            plan = _read_plan()
            service = QueryService(profile, catalog)
            context = service.prepare()
            body, _effective, warnings = service._build(plan, context.metadata, context.default_date_range)
            if not body.get("filters"):
                raise BiError("EXPORT_FILTER_REQUIRED", "导出请求体缺少筛选——本卡禁止无筛选导出。")
            for warning in warnings:
                _hint(f"警告：{warning}")
            task_id, name = _submit(transport, body)
            _hint(f"已提交导出任务 {task_id}（{name}）")

        status = _poll(transport, task_id, args.timeout, args.poll_interval)
        _hint(f"任务完成：{status}")
        total = _download(transport, task_id, out_path, name)
        _hint(f"已保存 {out_path}（{total} 字节）")
        emit({"ok": True, "taskId": task_id, "status": status, "file": str(out_path), "bytes": total})
        return 0
    except BiError as exc:
        emit({"ok": False, "error": exc.to_dict()})
        return 1
    except Exception:
        emit({"ok": False, "error": BiError("INTERNAL_ERROR", "导出发生未分类错误。").to_dict()})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
