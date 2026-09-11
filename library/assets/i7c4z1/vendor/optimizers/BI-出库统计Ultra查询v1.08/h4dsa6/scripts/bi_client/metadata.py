"""Load exact BI page/card metadata and merge it with the canonical allowlist."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .catalog import display_name
from .errors import BiError
from .transport import DirectTransport


def _same_field(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("fdId") and right.get("fdId"):
        return left["fdId"] == right["fdId"]
    return bool(left.get("name") and left.get("name") == right.get("name"))


def _find_target_field(selector: dict[str, Any], card_id: str) -> dict[str, Any] | None:
    mappings = ((selector.get("settings") or {}).get("asFilter") or {}).get("columnMappings") or []
    for mapping in mappings:
        for target in mapping.get("targetFields") or []:
            if target.get("cdId") == card_id:
                return dict(target)
    return None


def _normalize_selector(card: dict[str, Any], card_id: str) -> dict[str, Any]:
    content = card.get("content") or {}
    raw_source = content.get("source")
    source = raw_source if isinstance(raw_source, dict) else {}
    selector_type = content.get("selectorType") or ("TREE" if card.get("cdType") == "TREE_SELECTOR" else "")
    fields: list[dict[str, Any]] = []
    if source.get("field"):
        fields = [dict(source["field"])]
    if source.get("fieldSeq"):
        fields = [dict(field) for field in source["fieldSeq"]]
    return {
        "cdId": card.get("cdId"),
        "name": card.get("name"),
        "cdType": card.get("cdType"),
        "content": content,
        "settings": card.get("settings") or {},
        "selectorType": selector_type,
        "multiSelect": content.get("multiSelect"),
        "filterType": content.get("filterType") or ("BT" if selector_type == "TIME_MACRO" else "IN"),
        "fields": fields,
        "targetField": _find_target_field(card, card_id) or (fields[0] if fields else None),
    }


def _merge_by_key(
    canonical: list[dict[str, Any]],
    runtime: list[dict[str, Any]],
    *,
    label: str,
) -> list[dict[str, Any]]:
    runtime_map = {str(field.get("key")): field for field in runtime if field.get("key")}
    output = []
    for field in canonical:
        observed = runtime_map.get(str(field.get("key")))
        if observed:
            # metaType describes the source field, not its row/metric zone role;
            # baseFdType is optional runtime enrichment. Neither is stable identity.
            required = ["fdId", "fdType", "isAggregated", "calculationType"]
            # formula/aggrType 属展示性属性：BI 运行时已不回传明文公式（返回十六进制串），
            # 不参与一致性校验；字段身份由 key/fdId/fdType/isAggregated/calculationType 保证。
            optional_when_configured: list[str] = []
            for property_name in required:
                if property_name not in observed or observed.get(property_name) != field.get(property_name):
                    raise BiError(
                        "METADATA_MISMATCH",
                        f"运行时{label}定义与本地能力目录不一致。",
                        details={"field": display_name(field), "property": property_name},
                    )
            for property_name in optional_when_configured:
                if property_name in field and observed.get(property_name) != field.get(property_name):
                    raise BiError(
                        "METADATA_MISMATCH",
                        f"运行时{label}定义与本地能力目录不一致。",
                        details={"field": display_name(field), "property": property_name},
                    )
        merged = {**field, **(observed or {})}
        merged["displayName"] = field.get("displayName") or display_name(merged)
        merged["runtimeAvailable"] = str(field.get("key")) in runtime_map
        output.append(merged)
    return output


def fetch_metadata(
    transport: DirectTransport,
    profile: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any]:
    page_id = str(profile["pageId"])
    card_id = str(profile["cardId"])
    dataset_id = str(profile["datasetId"])
    data = transport.json(transport.bi_get(f"/api/page/{page_id}"))
    page = data.get("response")
    if not isinstance(page, dict):
        raise BiError("METADATA_MISMATCH", "BI 页面元数据缺少 response。")
    cards = page.get("cards") or []
    chart = next(
        (
            card
            for card in cards
            if card.get("cdId") == card_id
            and card.get("cdType") == "CHART"
            and (card.get("content") or {}).get("chartType") == "PIVOT_TABLE"
        ),
        None,
    )
    if not chart:
        raise BiError("METADATA_MISMATCH", "页面中没有找到配置的 BI 主查询卡片。")
    zone_data = ((((chart.get("content") or {}).get("meta") or {}).get("chartMain") or {}).get("zoneData"))
    if not isinstance(zone_data, dict):
        raise BiError("METADATA_MISMATCH", "BI 主查询卡片缺少 zoneData。")

    runtime_rows = zone_data.get("row") or []
    runtime_metrics = zone_data.get("metric") or []
    observed_datasets = {
        str(field.get("dsId"))
        for field in [*runtime_rows, *runtime_metrics]
        if field.get("dsId")
    }
    if observed_datasets and dataset_id not in observed_datasets:
        raise BiError(
            "METADATA_MISMATCH",
            "BI 主查询卡片的数据集与本地 profile 不一致。",
            details={"expectedDatasetId": dataset_id},
        )

    runtime_selectors = {
        str(card.get("cdId")): _normalize_selector(card, card_id)
        for card in cards
        if card.get("cdType") in {"SELECTOR", "TREE_SELECTOR"}
        or (card.get("content") or {}).get("selectorType")
    }
    selectors = []
    warnings = []
    for canonical in catalog.get("selectors") or []:
        runtime = runtime_selectors.get(str(canonical.get("cdId")))
        merged = dict(canonical)
        if runtime:
            merged.update(runtime)
            # Runtime metadata supplies mappings and BI details; the catalog
            # remains authoritative for identity and allowlist policy.
            for key in ("cdId", "name", "enabledForMainQuery", "aliases"):
                if key in canonical:
                    merged[key] = canonical[key]
            if not runtime.get("fields"):
                merged["fields"] = canonical.get("fields") or []
            if not runtime.get("targetField"):
                merged["targetField"] = canonical.get("targetField")
        merged["runtimeAvailable"] = bool(runtime)
        selectors.append(merged)
        if canonical.get("enabledForMainQuery") and not runtime:
            warnings.append(f"筛选器未出现在运行时页面元数据：{canonical.get('name')}")

    # 运行时新字段并入（权限自适应）：catalog 外、但当前用户可见的维度/指标/筛选器
    # 直接可用（displayName 取运行时名，业务语义待补）。语义字典漂移通过 warnings 显式提示。
    canonical_dim_keys = {str(f.get("key")) for f in catalog.get("dimensions") or []}
    canonical_met_keys = {str(f.get("key")) for f in catalog.get("metrics") or []}
    canonical_sel_ids = {str(f.get("cdId")) for f in catalog.get("selectors") or []}

    def _runtime_extra(field: dict[str, Any], known: set[str]) -> dict[str, Any]:
        merged = dict(field)
        merged["displayName"] = str(field.get("name") or "")
        merged["runtimeAvailable"] = True
        merged["semantic"] = False
        return merged

    extra_dims = [
        _runtime_extra(f, canonical_dim_keys)
        for f in runtime_rows
        if f.get("key") and str(f["key"]) not in canonical_dim_keys and str(f.get("name") or "") != "度量名"
    ]
    extra_mets = [
        _runtime_extra(f, canonical_met_keys)
        for f in runtime_metrics
        if f.get("key") and str(f["key"]) not in canonical_met_keys and f.get("metaType") != "MPH"
    ]
    extra_sels = []
    for cd_id, runtime in runtime_selectors.items():
        if cd_id in canonical_sel_ids or not runtime.get("targetField"):
            continue
        extra = dict(runtime)
        extra["enabledForMainQuery"] = True
        extra["runtimeAvailable"] = True
        extra["semantic"] = False
        extra_sels.append(extra)
    for label, extras in (("维度", extra_dims), ("指标", extra_mets), ("筛选器", extra_sels)):
        if extras:
            names = "、".join(str(e.get("name")) for e in extras)
            warnings.append(
                f"运行时发现语义字典外的{label}（可直接使用，业务口径待补）：{names}；"
                "建议运行 python scripts/sync_fields.py 同步到内置文档。"
            )

    normalized = {
        "pageId": page_id,
        "cardId": card_id,
        "datasetId": dataset_id,
        "dimensions": _merge_by_key(
            catalog.get("dimensions") or [],
            runtime_rows,
            label="维度",
        ) + extra_dims,
        "metrics": _merge_by_key(
            catalog.get("metrics") or [],
            runtime_metrics,
            label="指标",
        ) + extra_mets,
        "selectors": selectors + extra_sels,
        "columnFields": zone_data.get("column") or profile.get("columnFields") or [],
        "warnings": warnings,
    }
    digest_source = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    normalized["metadataVersion"] = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return normalized


def resolve_filter_field(selector: dict[str, Any], card_id: str) -> dict[str, Any] | None:
    return _find_target_field(selector, card_id) or selector.get("targetField") or next(
        iter(selector.get("fields") or []), None
    )


def resolve_tree_fields(selector: dict[str, Any], card_id: str, dataset_id: str) -> list[dict[str, Any]]:
    source_fields = selector.get("fields") or []
    mappings = ((selector.get("settings") or {}).get("asFilter") or {}).get("columnMappings") or []
    if not mappings:
        return [dict(field) for field in source_fields]

    mapped = []
    for source in source_fields:
        mapping = next((item for item in mappings if _same_field(item.get("sourceField") or {}, source)), None)
        target = next(
            (field for field in (mapping or {}).get("targetFields") or [] if field.get("cdId") == card_id),
            None,
        )
        merged = {**source, **(target or {})}
        merged["fdType"] = merged.get("fdType") or source.get("fdType") or "STRING"
        merged["metaType"] = merged.get("metaType") or source.get("metaType") or "DIM"
        merged["dsId"] = merged.get("dsId") or source.get("dsId") or dataset_id
        merged.pop("cdId", None)
        mapped.append(merged)
    return mapped
