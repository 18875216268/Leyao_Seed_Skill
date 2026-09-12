#!/usr/bin/env python3
"""源装载器：把 registry 的声明（kind=pool/cli）分派到具体客户端。

为什么要这一层：resolve 只认"声明与协议"，不认具体源实现 →
新增知识源 = registry 加一条 + （新 kind 时）在此加一个分支（当前两种 kind 已覆盖两库）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import query_norm  # noqa: E402
from sources import leyou_bridge, pool  # noqa: E402


def pool_search(problem: str, need_type: str, asset: dict, limit: int, tier: str | None,
                terms: list | None = None, kind: str | None = None) -> dict:
    """terms 由调用方传入（与相关性判定共用同一份检索词）；缺省时自行计算。"""
    return pool.search(terms or query_norm.search_terms(problem), need_type,
                       endpoint=asset.get("endpoint", ""),
                       limit=limit,
                       timeout=float(asset.get("timeout_s") or 6.0),
                       retry=int(asset.get("retry") or 0),
                       tier=tier,
                       use_category=bool(asset.get("use_category_filter", False)),
                       kind=kind)


def leyou_search(problem: str, asset: dict) -> dict:
    return leyou_bridge.search(problem, cli=asset.get("cli"),
                               timeout=float(asset.get("timeout_s") or 12.0))


def leyou_status(asset: dict) -> dict:
    return leyou_bridge.status(cli=asset.get("cli"), timeout=float(asset.get("timeout_s") or 10.0))
