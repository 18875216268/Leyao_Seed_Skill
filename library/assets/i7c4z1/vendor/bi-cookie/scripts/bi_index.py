#!/usr/bin/env python3
"""BI 卡片索引：缓存「每张卡可传哪些筛选参数」，供离线快速检索。

设计要点：
- 索引是**事实缓存**，不是业务事实源；权威来源永远是 `GET /api/card/{cardId}`。
- 读时补新：查询某卡时若条目缺失或超过 TTL，实时拉取并写回索引。
- 网络失败时降级使用旧索引并标注 `_stale`，绝不阻塞取数。

输出：JSON 到 stdout（供 AI 解析）；人类可读提示到 stderr。
用法见 --help。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

# 登录器与公共底座在 skill 根 scripts/（纯登录框架），本脚本位于 vendor/bi-cookie/scripts/
_FRAMEWORK_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_FRAMEWORK_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK_SCRIPTS))

INDEX_FILE = Path(__file__).resolve().parents[1] / "data" / "card_index.json"

import bi_login as lb  # noqa: E402  登录模块入口：注入 vendor 路径并再导出公开 API
from bi_common import BiError, configure_stdio, error_payload  # noqa: E402

DEFAULT_TTL_DAYS = 7
MAX_WORKERS = 8
FILTER_KEYS = ("name", "fdId", "dsId", "cdId", "fdType", "filterType", "filterLevel")
ZONE_FIELD_KEYS = ("row", "column", "metric")


def _hint(message: str) -> None:
    print(f"[bi-index] {message}", file=sys.stderr, flush=True)


def _unwrap(body: Any) -> dict[str, Any]:
    """拆掉 raw-backend-response 信封 {"result":"ok","response":{...}}。"""
    if isinstance(body, dict):
        inner = body.get("response")
        if isinstance(inner, dict):
            return inner
        return body
    return {}


def load_index() -> dict[str, Any]:
    if not INDEX_FILE.is_file():
        return {"meta": {"generatedAt": 0, "ttlDays": DEFAULT_TTL_DAYS, "cardCount": 0}, "cards": {}}
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"meta": {"generatedAt": 0, "ttlDays": DEFAULT_TTL_DAYS, "cardCount": 0}, "cards": {}}
    data.setdefault("meta", {})
    data.setdefault("cards", {})
    return data


def save_index(index: dict[str, Any]) -> None:
    """原子写入，避免中断产生半截文件。"""
    index["meta"]["generatedAt"] = int(time.time())
    index["meta"]["cardCount"] = len(index["cards"])
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".card_index.", suffix=".tmp", dir=str(INDEX_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(index, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, INDEX_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Indexer:
    """读取 BI 元数据的只读客户端。"""

    def __init__(self) -> None:
        credential = lb.verify_credential(validate_remote=False)
        if not credential.get("authenticated"):
            raise BiError("AUTH_REQUIRED", "本地凭证不可用，请先登录：python scripts/bi_login.py")
        self.base = str(credential.get("biBase") or "")
        self.headers = dict(credential.get("headers") or {})
        self.session = requests.Session()
        self.session.trust_env = False

    def close(self) -> None:
        self.session.close()

    def get(self, path: str, timeout: int = 60) -> dict[str, Any]:
        try:
            resp = self.session.get(
                self.base + path, headers=self.headers, timeout=timeout, allow_redirects=False
            )
        except requests.RequestException as exc:
            _hint(f"请求失败 {path}：{type(exc).__name__}")
            return {}
        if resp.status_code != 200:
            _hint(f"请求异常 {path} -> HTTP {resp.status_code}")
            return {}
        try:
            return _unwrap(resp.json())
        except ValueError:
            return {}


def extract_entry(meta: dict[str, Any]) -> dict[str, Any]:
    """从卡片元信息提取索引条目（只留构造请求必需的字段）。"""
    content = meta.get("content") or {}
    chart_main = (content.get("meta") or {}).get("chartMain") or {}
    zone_data = chart_main.get("zoneData") or {}

    filters: list[dict[str, Any]] = []
    for item in zone_data.get("filters") or []:
        if isinstance(item, dict):
            filters.append({key: item.get(key) for key in FILTER_KEYS})

    dynamic: list[dict[str, Any]] = []
    for group in (chart_main.get("dynamicZoneInfo") or {}).get("dzMappings") or []:
        if isinstance(group, dict):
            dynamic.append({"dzId": group.get("dzId"), "name": group.get("name")})

    fields: list[str] = []
    for zone in ZONE_FIELD_KEYS:
        for item in zone_data.get(zone) or []:
            if isinstance(item, dict) and item.get("name"):
                fields.append(str(item.get("name")))

    return {
        "cardId": meta.get("cdId"),
        "name": meta.get("name"),
        "chartType": content.get("chartType"),
        "pgId": meta.get("pgId"),
        "cdType": meta.get("cdType"),
        "filters": filters,
        "dz": dynamic,
        "fields": sorted(set(fields)),
        "updatedAt": int(time.time()),
    }


def collect_page_ids(node: dict[str, Any], out: list[str]) -> None:
    """递归收集目录树中的页面 id。"""
    for child in node.get("contents") or []:
        if not isinstance(child, dict):
            continue
        if child.get("isPage") and child.get("id"):
            out.append(str(child.get("id")))
        if child.get("contents"):
            collect_page_ids(child, out)


def cmd_build(index: dict[str, Any], indexer: Indexer, ttl_days: int) -> dict[str, Any]:
    root = indexer.get("/api/page-v3")
    page_ids: list[str] = []
    collect_page_ids(root, page_ids)
    _hint(f"目录页 {len(page_ids)} 个，开始读取卡片清单")

    card_ids: list[str] = []
    for page_id in page_ids:
        page = indexer.get(f"/api/page/{page_id}")
        for card in page.get("cards") or []:
            if isinstance(card, dict) and card.get("cdType") == "CHART" and card.get("cdId"):
                card_ids.append(str(card.get("cdId")))
    card_ids = sorted(set(card_ids))
    _hint(f"数据卡 {len(card_ids)} 张，开始读取元信息")

    def fetch(card_id: str) -> tuple[str, dict[str, Any]]:
        return card_id, indexer.get(f"/api/card/{card_id}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(fetch, card_ids))

    indexed = 0
    for card_id, meta in results:
        if meta:
            index["cards"][card_id] = extract_entry(meta)
            indexed += 1
    index["meta"]["ttlDays"] = ttl_days
    save_index(index)
    return {"built": indexed, "pages": len(page_ids), "cards": len(card_ids)}


def is_stale(entry: dict[str, Any] | None, ttl_days: int) -> bool:
    if not entry:
        return True
    age = time.time() - int(entry.get("updatedAt") or 0)
    return age > ttl_days * 86400


def cmd_card(
    index: dict[str, Any], indexer: Indexer, card_id: str, ttl_days: int, force: bool
) -> dict[str, Any]:
    entry = index["cards"].get(card_id)
    if force or is_stale(entry, ttl_days):
        meta = indexer.get(f"/api/card/{card_id}")
        if meta:
            index["cards"][card_id] = extract_entry(meta)
            save_index(index)
            result = dict(index["cards"][card_id])
            result["_refreshed"] = True
            return result
        result = dict(entry or {})
        result["_stale"] = True
        _hint("实时刷新失败，返回旧索引条目")
        return result
    return dict(entry or {})


def cmd_search(index: dict[str, Any], keyword: str) -> list[dict[str, Any]]:
    needle = keyword.lower()
    hits: list[dict[str, Any]] = []
    for card_id, entry in index["cards"].items():
        haystack = " ".join(
            [str(entry.get("name") or ""), str(entry.get("chartType") or "")]
            + [str(f.get("name") or "") for f in entry.get("filters") or []]
            + list(entry.get("fields") or [])
        ).lower()
        if needle in haystack:
            hits.append(
                {
                    "cardId": card_id,
                    "name": entry.get("name"),
                    "chartType": entry.get("chartType"),
                    "pgId": entry.get("pgId"),
                    "filterCount": len(entry.get("filters") or []),
                    "filters": [
                        {"name": f.get("name"), "fdId": f.get("fdId"), "filterType": f.get("filterType")}
                        for f in entry.get("filters") or []
                    ],
                    "hasDynamicZone": bool(entry.get("dz")),
                }
            )
    return hits


def cmd_refresh(index: dict[str, Any], indexer: Indexer, ttl_days: int) -> dict[str, Any]:
    stale_ids = [cid for cid, entry in index["cards"].items() if is_stale(entry, ttl_days)]
    if not stale_ids:
        return {"refreshed": 0, "checked": len(index["cards"])}

    def fetch(card_id: str) -> tuple[str, dict[str, Any]]:
        return card_id, indexer.get(f"/api/card/{card_id}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(fetch, stale_ids))
    refreshed = 0
    for card_id, meta in results:
        if meta:
            index["cards"][card_id] = extract_entry(meta)
            refreshed += 1
    save_index(index)
    return {"refreshed": refreshed, "checked": len(index["cards"])}


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="BI 卡片索引：缓存每卡可传的筛选参数，支持离线检索与读时补新"
    )
    parser.add_argument("--build", action="store_true", help="全量构建索引（遍历目录→页面→卡片）")
    parser.add_argument("--card", metavar="CARD_ID", help="查某张卡的可传参数（过期自动实时刷新）")
    parser.add_argument("--search", metavar="KEYWORD", help="离线关键词检索卡片/筛选/字段")
    parser.add_argument("--refresh", action="store_true", help="只刷新超过 TTL 的条目")
    parser.add_argument("--stats", action="store_true", help="查看索引概况")
    parser.add_argument("--ttl-days", type=int, default=DEFAULT_TTL_DAYS, help=f"有效期天数（默认 {DEFAULT_TTL_DAYS}）")
    parser.add_argument("--force", action="store_true", help="强制实时刷新（忽略 TTL）")
    args = parser.parse_args()

    index = load_index()
    try:
        if args.stats:
            meta = dict(index["meta"])
            meta["cards"] = len(index["cards"])
            meta["indexPath"] = str(INDEX_FILE)
            print(json.dumps(meta, ensure_ascii=False, indent=2))
            return 0

        if args.search:
            print(json.dumps(cmd_search(index, args.search), ensure_ascii=False, indent=2))
            return 0

        indexer = Indexer()
        try:
            if args.build:
                print(json.dumps(cmd_build(index, indexer, args.ttl_days), ensure_ascii=False, indent=2))
            elif args.card:
                print(json.dumps(cmd_card(index, indexer, args.card, args.ttl_days, args.force), ensure_ascii=False, indent=2))
            elif args.refresh:
                print(json.dumps(cmd_refresh(index, indexer, args.ttl_days), ensure_ascii=False, indent=2))
            else:
                parser.error("需要 --build / --card / --search / --refresh / --stats 之一")
        finally:
            indexer.close()
        return 0
    except BiError as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
