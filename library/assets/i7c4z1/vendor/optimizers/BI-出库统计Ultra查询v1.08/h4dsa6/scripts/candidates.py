#!/usr/bin/env python3
"""筛选器候选值查询：实时获取 + 按当前登录用户缓存。

筛选项的具体选项不内置（随公司、账号权限与业务数据变化），一律实时获取；
获取结果按当前用户（loginId）缓存，TTL 默认 24 小时，只缓存用户查询过的筛选器。

用法：
  python scripts/candidates.py --filter "业务类型"             # 取候选（缓存优先）
  python scripts/candidates.py --filter "业务类型" --refresh   # 强制重新实测
  python scripts/candidates.py --list                         # 缓存概况
  python scripts/candidates.py --ttl-hours 0 --filter "省份"   # TTL=0 即每次都实测

时间区间类（出库日期/支付日期）无候选接口，用查询项 `date` 传值；
区域树（省份-城市-区）走 treeSelector 接口，本脚本不拉取。
输出：JSON 到 stdout。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from bi_client.catalog import load_catalog
from bi_client.cli import configure_stdio, emit
from bi_client.credentials import load_credential
from bi_client.errors import BiError
from bi_client.profile import load_profile
from bi_client.transport import DirectTransport, require_requests

MAX_TTL_HOURS = 24 * 7
CACHE_FILE = Path(__file__).resolve().parent.parent / "resources" / "candidate_cache.json"


def _cache_key(credential: dict[str, Any]) -> str:
    """缓存按当前用户隔离；拿不到登录身份时用凭证指纹前 12 位。"""
    login_id = str(credential.get("loginId") or "").strip()
    if login_id:
        return login_id
    return "adhoc-" + str(credential.get("token") or "")[-12:]


def _load_cache() -> dict[str, Any]:
    if not CACHE_FILE.is_file():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(cache: dict[str, Any]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _resolve_selector(catalog: dict[str, Any], name: str) -> dict[str, Any]:
    for sel in catalog.get("selectors") or []:
        if str(sel.get("name")) == name:
            return sel
    raise BiError(
        "FILTER_NOT_FOUND",
        f"筛选器不存在：{name}；可用名称见 references/parameters.md 的筛选字段表。",
    )


def _fetch(transport: DirectTransport, sel: dict[str, Any]) -> list[str]:
    data = transport.json(transport.selector_candidates(str(sel["cdId"])))
    inner = data.get("response") if isinstance(data.get("response"), dict) else data
    items = inner.get("result") if isinstance(inner, dict) else None
    if not isinstance(items, list):
        raise BiError("RESPONSE_SCHEMA_CHANGED", "候选值响应缺少 result[]。")
    return [str(it.get("value")) if isinstance(it, dict) else str(it) for it in items]


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="筛选器候选值查询（实时获取 + 按用户缓存）")
    parser.add_argument("--filter", help="筛选字段名称（如 业务类型）")
    parser.add_argument("--search", help="树筛选专用：按关键字获取完整子树（如 --search 重庆）")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新实测")
    parser.add_argument("--ttl-hours", type=float, default=24.0, help="缓存有效期小时数（默认 24；0=每次实测）")
    parser.add_argument("--list", action="store_true", help="查看当前用户的候选值缓存概况")
    args = parser.parse_args()

    try:
        require_requests()
        catalog, profile = load_catalog(), load_profile()
        credential = load_credential()
        user_key = _cache_key(credential)
        cache = _load_cache().get(user_key) or {}

        if args.list:
            rows = [
                {"filter": name, "count": len(entry.get("options") or []), "fetchedAt": entry.get("fetchedAt")}
                for name, entry in sorted(cache.items())
            ]
            emit({"ok": True, "user": user_key, "cached": rows})
            return 0

        if not args.filter:
            parser.error("需要 --filter <筛选字段名称>（或 --list）")

        sel = _resolve_selector(catalog, args.filter)
        stype = str(sel.get("selectorType") or "")
        ftype = str(sel.get("filterType") or "")

        entry = cache.get(args.filter) or {}
        fresh = entry.get("fetchedAt") and (time.time() - float(entry["fetchedAt"])) < max(0.0, args.ttl_hours) * 3600
        if fresh and not args.refresh and entry.get("options") is not None:
            emit({"ok": True, "filter": args.filter, "source": "cache", "count": len(entry["options"]),
                  "options": entry["options"], "fetchedAt": entry["fetchedAt"]})
            return 0

        if stype in ("TIME_MACRO", "TIME") or ftype == "BT":
            result = {"ok": True, "filter": args.filter, "source": "none",
                      "note": "时间区间类筛选无候选接口，用查询项 date 传值（YYYY-MM-DD 闭区间）"}
        elif "TREE" in stype.upper():
            search = str(args.search or "").strip()
            if not search:
                result = {"ok": True, "filter": args.filter, "source": "none",
                          "note": "区域树候选过大：空体会截断（exceedLimit）。请加 --search <关键字> 按条件获取完整子树"
                                  "（如 --search 重庆），或直接用查询项 treePaths 传完整行政区路径。"}
            else:
                cache_key = f"{args.filter}｜search={search}"
                entry = cache.get(cache_key) or {}
                fresh = entry.get("fetchedAt") and (time.time() - float(entry["fetchedAt"])) < max(0.0, args.ttl_hours) * 3600
                if fresh and not args.refresh:
                    emit({"ok": True, "filter": args.filter, "source": "cache", "search": search,
                          "tree": entry.get("tree"), "fetchedAt": entry["fetchedAt"]})
                    return 0
                transport = DirectTransport(profile, credential)
                try:
                    data = transport.json(transport.tree_candidates(str(sel["cdId"]), {"search": search}))
                finally:
                    transport.close()
                inner = data.get("response") if isinstance(data.get("response"), dict) else data
                tree = inner.get("result") if isinstance(inner, dict) else None
                exceed = bool(inner.get("exceedLimit")) if isinstance(inner, dict) else False
                cache[cache_key] = {"fetchedAt": time.time(), "tree": tree}
                _save_cache({user_key: cache})
                result = {"ok": True, "filter": args.filter, "source": "live", "search": search,
                          "count": inner.get("count") if isinstance(inner, dict) else None,
                          "exceedLimit": exceed, "tree": tree,
                          "note": "exceedLimit=true 表示结果仍被截断，请细化关键字"}
        else:
            transport = DirectTransport(profile, credential)
            try:
                options = _fetch(transport, sel)
            finally:
                transport.close()
            cache[args.filter] = {"fetchedAt": time.time(), "options": options}
            _save_cache({user_key: cache})
            result = {"ok": True, "filter": args.filter, "source": "live",
                      "count": len(options), "options": options, "note": "候选随账号权限与业务数据变化，以本次实测为准"}
        emit(result)
        return 0
    except BiError as exc:
        emit({"ok": False, "error": exc.to_dict()})
        return 1
    except Exception:
        emit({"ok": False, "error": BiError("INTERNAL_ERROR", "候选值查询发生未分类错误。").to_dict()})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
