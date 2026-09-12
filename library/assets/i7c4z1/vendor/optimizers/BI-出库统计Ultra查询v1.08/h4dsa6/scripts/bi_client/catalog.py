"""Canonical field catalog and ambiguity-safe name resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .errors import BiError
from .profile import skill_root


def load_catalog(root: Path | None = None) -> dict[str, Any]:
    path = (root or skill_root()) / "resources" / "catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BiError(
            "CATALOG_INVALID",
            "无法读取 BI 字段目录。",
            details={"path": str(path)},
        ) from exc
    if not isinstance(data.get("dimensions"), list) or not isinstance(data.get("metrics"), list):
        raise BiError("CATALOG_INVALID", "BI 字段目录结构不完整。")
    return data


def display_name(field: dict[str, Any]) -> str:
    return str(
        field.get("displayName")
        or field.get("alias")
        or field.get("title")
        or field.get("originTitle")
        or field.get("name")
        or field.get("key")
        or ""
    )


def _names(item: dict[str, Any], get_display: Callable[[dict[str, Any]], str]) -> set[str]:
    values = {
        item.get("key"),
        item.get("cdId"),
        item.get("name"),
        item.get("alias"),
        item.get("title"),
        item.get("originTitle"),
        get_display(item),
    }
    values.update(item.get("aliases") or [])
    return {str(value).strip().casefold() for value in values if str(value or "").strip()}


def resolve_unique(
    text: str,
    items: list[dict[str, Any]],
    *,
    label: str,
    get_display: Callable[[dict[str, Any]], str] = display_name,
) -> dict[str, Any]:
    needle = str(text or "").strip().casefold()
    if not needle:
        raise BiError("FIELD_NOT_FOUND", f"{label}不能为空。")
    matches = [item for item in items if needle in _names(item, get_display)]
    if not matches:
        raise BiError("FIELD_NOT_FOUND", f"不支持的{label}：{text}")
    unique = {str(item.get("key") or item.get("cdId") or id(item)): item for item in matches}
    if len(unique) > 1:
        raise BiError(
            "FIELD_AMBIGUOUS",
            f"{label}名称存在歧义：{text}",
            details={"candidates": [get_display(item) for item in unique.values()]},
        )
    return dict(next(iter(unique.values())))

