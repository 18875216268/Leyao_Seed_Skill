#!/usr/bin/env python3
"""BI 卡片导出：把卡片数据导出为 Excel（xlsx）。

实测三步链（服务端异步任务）：
1) POST /api/write/file/{cardId}?typeOp=EXCEL  → taskId（+ 卡片显示名）
2) GET  /api/task/{taskId}                     → 轮询至终态 **FINISHED**（失败为 FAILED）
3) POST /api/export/file/common/{taskId}       → xlsx 二进制流

要点：
- 请求体与取数请求体一致（可用 --payload-file 传筛选条件）；导出结果为卡片全量数据。
- 大文件采用流式落盘，不整体载入内存。
- 等待超时后可用 --task <taskId> 直接继续下载，无需重新提交。

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

import requests

# 登录器与公共底座在 skill 根 scripts/（纯登录框架），本脚本位于 vendor/bi-cookie/scripts/
_FRAMEWORK_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_FRAMEWORK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK_SCRIPTS))

import bi_login as lb  # noqa: E402  登录模块入口：注入 vendor 路径并再导出公开 API
from bi_common import BiError, configure_stdio, error_payload  # noqa: E402

TERMINAL_OK = {"FINISHED"}
TERMINAL_FAIL = {"FAILED", "FAIL", "CANCELLED", "CANCELED"}
DEFAULT_PAYLOAD = {"offset": 0, "limit": 1000, "view": "GRID"}


def _hint(message: str) -> None:
    print(f"[bi-export] {message}", file=sys.stderr, flush=True)


def _unwrap(body: Any) -> dict[str, Any]:
    """拆掉 raw-backend-response 信封。"""
    if isinstance(body, dict):
        inner = body.get("response")
        return inner if isinstance(inner, dict) else body
    return {}


def _safe_name(name: str) -> str:
    """去掉文件名非法字符。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "").strip())
    return cleaned[:80] or "bi_export"


class Exporter:
    """导出客户端：提交任务 → 轮询 → 下载。"""

    def __init__(self) -> None:
        credential = lb.verify_credential(validate_remote=False)
        if not credential.get("authenticated"):
            raise BiError("AUTH_REQUIRED", "本地凭证不可用，请先登录：python scripts/bi_login.py")
        self.base = str(credential.get("biBase") or "")
        self.headers = dict(credential.get("headers") or {})
        self.headers.setdefault("Content-Type", "application/json")
        self.session = requests.Session()
        self.session.trust_env = False

    def close(self) -> None:
        self.session.close()

    def submit(self, card_id: str, payload: dict[str, Any]) -> tuple[str, str]:
        resp = self.session.post(
            f"{self.base}/api/write/file/{card_id}",
            params={"typeOp": "EXCEL"},
            headers=self.headers,
            json=payload,
            timeout=120,
            allow_redirects=False,
        )
        if resp.status_code != 200:
            raise BiError("EXPORT_SUBMIT_FAILED", f"提交导出失败（HTTP {resp.status_code}）：{resp.text[:200]}")
        data = _unwrap(resp.json())
        task_id = str(data.get("taskId") or data.get("id") or "")
        if not task_id:
            raise BiError("EXPORT_SUBMIT_FAILED", f"提交导出未返回 taskId：{str(data)[:200]}")
        post_body = data.get("postBody") if isinstance(data.get("postBody"), dict) else {}
        name = post_body.get("downloadFileName") or data.get("fileName") or card_id
        return task_id, str(name)

    def wait(self, task_id: str, timeout: int, interval: int) -> tuple[str, dict[str, Any]]:
        deadline = time.time() + max(1, timeout)
        waited = 0
        while time.time() < deadline:
            resp = self.session.get(
                f"{self.base}/api/task/{task_id}", headers=self.headers, timeout=60, allow_redirects=False
            )
            data = _unwrap(resp.json()) if resp.status_code == 200 else {}
            status = str(data.get("status") or "").upper()
            if status in TERMINAL_OK:
                return status, data
            if status in TERMINAL_FAIL:
                raise BiError("EXPORT_FAILED", f"导出任务失败（{status}）：{str(data)[:200]}")
            time.sleep(max(1, interval))
            waited += interval
            if waited % 15 == 0:
                _hint(f"导出中… 已等待 {waited}s（当前 {status or '未知'}）")
        raise BiError("EXPORT_TIMEOUT", f"等待导出超时（{timeout}s）；任务可能仍在服务端执行，可用 --task {task_id} 继续下载")

    def list_tasks(self, limit: int, resource_type: str = "CARD") -> list[dict[str, Any]]:
        """导出中心任务列表（UI 的 task-offline history，用于找回 taskId）。"""
        resp = self.session.get(
            f"{self.base}/api/task-offline/guandata/history",
            params={"offset": 0, "limit": max(1, limit), "exportAsyncResourceType": resource_type},
            headers=self.headers,
            timeout=60,
            allow_redirects=False,
        )
        if resp.status_code != 200:
            raise BiError("EXPORT_LIST_FAILED", f"查询任务列表失败（HTTP {resp.status_code}）：{resp.text[:200]}")
        data = _unwrap(resp.json())
        items = data.get("data")
        return items if isinstance(items, list) else []

    def download(self, task_id: str, out_path: Path, download_name: str | None = None) -> int:
        body: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S.000+08:00"),
            "fileNameWithTime": True,
        }
        if download_name:
            body["downloadFileName"] = download_name
        with self.session.post(
            f"{self.base}/api/export/file/common/{task_id}",
            headers=self.headers,
            json=body,
            timeout=300,
            allow_redirects=False,
            stream=True,
        ) as resp:
            if resp.status_code != 200:
                raise BiError(
                    "EXPORT_DOWNLOAD_FAILED", f"下载失败（HTTP {resp.status_code}）：{resp.text[:200]}"
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with open(out_path, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        handle.write(chunk)
                        total += len(chunk)
        return total


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="BI 卡片导出：把卡片数据导出为 Excel（xlsx）")
    parser.add_argument("--card", help="卡片 cardId")
    parser.add_argument("--task", help="已有导出任务 id（跳过提交，直接轮询+下载）")
    parser.add_argument("--list", type=int, default=None, metavar="LIMIT", help="查看最近导出任务列表（导出中心），不提交/下载")
    parser.add_argument("--out", help="输出 xlsx 路径（默认 ./<卡名>_<时间戳>.xlsx）")
    parser.add_argument("--payload-file", type=Path, help="导出请求体 JSON（筛选条件，同取数请求体）")
    parser.add_argument("--timeout", type=int, default=600, help="等待导出完成的最长秒数（默认 600）")
    parser.add_argument("--poll-interval", type=int, default=3, help="轮询间隔秒数（默认 3）")
    args = parser.parse_args()

    if not args.card and not args.task and args.list is None:
        parser.error("需要 --card（提交新导出）、--task（继续已有任务）或 --list（查看任务列表）")

    payload = dict(DEFAULT_PAYLOAD)
    if args.payload_file:
        path = args.payload_file.expanduser()
        if not path.is_file():
            print(json.dumps({"ok": False, "error": f"请求体文件不存在：{path}"}, ensure_ascii=False))
            return 1
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": f"请求体解析失败：{exc}"}, ensure_ascii=False))
            return 1
        if not isinstance(loaded, dict):
            print(json.dumps({"ok": False, "error": "请求体必须是 JSON 对象"}, ensure_ascii=False))
            return 1
        payload = loaded

    exporter = Exporter()
    try:
        if args.list is not None:
            items = exporter.list_tasks(args.list)
            rows = [
                {
                    "taskId": str(t.get("taskId") or ""),
                    "resourceName": str(t.get("resourceName") or ""),
                    "status": str(t.get("taskStatus") or ""),
                    "submitTime": str(t.get("submitTime") or ""),
                    "finishedTime": str(t.get("finishedTime") or ""),
                }
                for t in items
            ]
            print(json.dumps({"ok": True, "count": len(rows), "tasks": rows}, ensure_ascii=False, indent=2))
            return 0
        if args.task:
            task_id, display_name = str(args.task), "bi_export"
            _hint(f"复用已有任务 {task_id}")
        else:
            task_id, display_name = exporter.submit(str(args.card), payload)
            _hint(f"已提交导出任务 {task_id}（{display_name}）")

        status, task_data = exporter.wait(task_id, args.timeout, args.poll_interval)
        _hint(f"任务完成：{status}")

        if args.out:
            out_path = Path(args.out).expanduser()
        else:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_path = Path.cwd() / f"{_safe_name(display_name)}_{stamp}.xlsx"

        total = exporter.download(task_id, out_path, display_name if display_name != "bi_export" else None)
        _hint(f"已保存 {out_path}（{total} 字节）")
        print(
            json.dumps(
                {
                    "ok": True,
                    "cardId": args.card,
                    "taskId": task_id,
                    "status": status,
                    "file": str(out_path),
                    "bytes": total,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except BiError as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        exporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
