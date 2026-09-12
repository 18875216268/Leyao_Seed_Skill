#!/usr/bin/env python3
"""注册表读取与校验：两库的地址 / 优先级 / 超时 / 覆盖诉求 / TTL 全部来自 registry.json（不改代码即可换源）。"""
from __future__ import annotations

from common import SKILL_ROOT, load_json

REGISTRY_F = SKILL_ROOT / "registry.json"
REQUIRED_FIELDS = ("id", "name", "kind", "covers_need")


def load() -> dict:
    data = load_json(REGISTRY_F, None)
    if not isinstance(data, dict) or not data.get("assets"):
        raise RuntimeError("registry.json 缺失或无效：%s" % REGISTRY_F)
    order = data.get("priority") or [a["id"] for a in data["assets"]]
    data["ordered_assets"] = sorted(data["assets"],
                                    key=lambda a: order.index(a["id"]) if a["id"] in order else 99)
    return data


def validate(data: dict | None = None) -> list:
    """返回问题列表（空 = 通过）。"""
    data = data or load()
    problems = []
    for asset in data["assets"]:
        for f in REQUIRED_FIELDS:
            if not asset.get(f):
                problems.append("资产 %s 缺字段 %s" % (asset.get("id", "?"), f))
        if asset.get("kind") == "pool" and not asset.get("endpoint"):
            problems.append("pool 资产 %s 缺 endpoint" % asset["id"])
        if asset.get("kind") == "cli" and not asset.get("cli"):
            problems.append("cli 资产 %s 缺 cli 路径" % asset["id"])
    if not data.get("priority"):
        problems.append("缺 priority（优先级链）")
    return problems


def by_need(need_type: str, data: dict | None = None) -> list:
    """按优先级返回可服务该 need_type 的资产。"""
    data = data or load()
    return [a for a in data["ordered_assets"] if need_type in (a.get("covers_need") or [])]


def ttl_for(need_type: str, data: dict | None = None) -> int:
    data = data or load()
    return int((data.get("ttl_seconds") or {}).get(need_type, 3600))


def semantic_threshold(data: dict | None = None) -> float:
    data = data or load()
    return float(data.get("semantic_threshold") or 0.85)


def budget_seconds(data: dict | None = None) -> float:
    data = data or load()
    return float(data.get("budget_seconds") or 20.0)
