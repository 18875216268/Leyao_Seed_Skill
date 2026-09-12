"""Normalize Guandata chartMain responses without losing numeric values."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .catalog import display_name
from .errors import BiError


def _metric_headers(column_values: list[Any], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headers = []
    for group in column_values:
        if isinstance(group, list):
            headers.append(dict((group[-1] if group else {}) or {}))
        else:
            headers.append(dict(group or {}))
    if headers:
        return _overlay_catalog_labels(headers, fallback)
    if fallback:
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 响应缺少指标列身份。")
    return []


def _overlay_catalog_labels(
    runtime_fields: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep runtime order and formats while using stable catalog display names."""
    def identities(field: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
        values = (field.get(key) for key in keys)
        return {str(value).strip().casefold() for value in values if str(value or "").strip()}

    strong_keys = ("key", "fdId")
    weak_keys = ("name", "displayName", "alias", "title", "originTitle")
    unmatched = list(range(len(fallback)))
    output = []
    for index, runtime in enumerate(runtime_fields):
        runtime_strong = identities(runtime, strong_keys)
        candidates = [
            position
            for position in unmatched
            if runtime_strong & identities(fallback[position], strong_keys)
        ]
        if not candidates:
            runtime_weak = identities(runtime, weak_keys)
            candidates = [
                position
                for position in unmatched
                if runtime_weak & identities(fallback[position], weak_keys)
            ]
        if len(candidates) != 1:
            raise BiError(
                "RESPONSE_SCHEMA_CHANGED",
                "BI 返回列与查询字段身份不一致或存在歧义。",
                details={"columnIndex": index, "candidateCount": len(candidates)},
            )
        fallback_position = candidates[0]
        unmatched.remove(fallback_position)
        fallback_field = fallback[fallback_position]
        merged = dict(runtime)
        merged["displayName"] = display_name(fallback_field)
        merged["key"] = fallback_field.get("key") or merged.get("key")
        output.append(merged)
    return output


def _dimension_headers(row_meta: list[Any], fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headers = [dict(item or {}) for item in row_meta]
    if headers:
        return _overlay_catalog_labels(headers, fallback)
    if fallback:
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 响应缺少维度列身份。")
    return []


def _header_name(field: dict[str, Any]) -> str:
    return str(
        field.get("displayName")
        or field.get("alias")
        or field.get("title")
        or field.get("originTitle")
        or field.get("name")
        or field.get("key")
        or "-"
    )


def _format_metric(value: Any, fmt: dict[str, Any] | None) -> str:
    if value is None:
        return "-"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    fmt = fmt or {}
    places = int(fmt.get("decimalPlaces") if fmt.get("decimalPlaces") is not None else 2)
    places = max(0, min(places, 12))
    quant = Decimal(1).scaleb(-places)
    specifier = str((fmt.get("specifier") or ""))
    if "%" in specifier:
        try:
            divisor = Decimal(str(fmt.get("divideDataBy") or 1))
            if divisor == 0:
                divisor = Decimal(1)
        except InvalidOperation:
            divisor = Decimal(1)
        result = (number / divisor * 100).quantize(quant, rounding=ROUND_HALF_UP)
        return f"{result:.{places}f}%"
    result = number.quantize(quant, rounding=ROUND_HALF_UP)
    return f"{result:.{places}f}"


def _dimension_cell(cell: Any) -> dict[str, Any]:
    if not isinstance(cell, dict):
        return {"raw": cell, "display": "" if cell is None else str(cell)}
    display = cell.get("title")
    if display is None:
        display = cell.get("displayValue", cell.get("value", ""))
    raw = cell.get("v", cell.get("value", display))
    return {"raw": raw, "display": "" if display is None else str(display)}


def _metric_cell(cell: Any, fmt: dict[str, Any] | None) -> dict[str, Any]:
    raw = cell.get("v") if isinstance(cell, dict) else cell
    return {"raw": raw, "display": _format_metric(raw, fmt), "format": fmt or {}}


def parse_chart_main(
    response: dict[str, Any],
    *,
    dimensions: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    page: int,
    page_size: int,
    max_rows: int | None = None,
) -> dict[str, Any]:
    chart = ((response.get("response") or {}).get("chartMain"))
    if not isinstance(chart, dict):
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 响应缺少 chartMain。")
    row_block = chart.get("row") or {}
    column_block = chart.get("column") or {}
    row_meta = row_block.get("meta") or []
    row_values = row_block.get("values") or []
    data = chart.get("data") or []
    headers = _metric_headers(column_block.get("values") or [], metrics)
    formats = ((column_block.get("metricFieldFormat") or {}).get("numberFormat") or [])

    dimension_headers = _dimension_headers(row_meta, dimensions)
    if len(dimension_headers) != len(dimensions):
        raise BiError(
            "RESPONSE_SCHEMA_CHANGED",
            "BI 返回的维度列数量与查询计划不一致。",
            details={"expected": len(dimensions), "actual": len(dimension_headers)},
        )
    if len(headers) != len(metrics):
        raise BiError(
            "RESPONSE_SCHEMA_CHANGED",
            "BI 返回的指标列数量与查询计划不一致。",
            details={"expected": len(metrics), "actual": len(headers)},
        )
    output_names = [_header_name(field) for field in [*dimension_headers, *headers]]
    if len(output_names) != len(set(output_names)):
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回列名称重复，无法安全生成结果。")

    normalized_rows: list[dict[str, Any]] = []
    if row_values:
        if len(data) < len(row_values):
            raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回的维度行与指标行数量不一致。")
        pairs = zip(row_values, data)
    elif isinstance(data, list) and data:
        metric_cells = data[0] if isinstance(data[0], list) else data
        pairs = [([], metric_cells)]
    else:
        pairs = []

    for dim_cells, metric_cells in pairs:
        dim_cells = dim_cells or []
        metric_cells = metric_cells or []
        if len(dim_cells) != len(dimension_headers):
            raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回的维度单元格数量不一致。")
        if len(metric_cells) != len(headers):
            raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回的指标单元格数量不一致。")
        is_grandtotal = any(isinstance(cell, dict) and cell.get("isGrandtotal") for cell in dim_cells)
        values: dict[str, Any] = {}
        for index, meta in enumerate(dimension_headers):
            values[_header_name(meta)] = _dimension_cell(dim_cells[index])
        for index, meta in enumerate(headers):
            fmt_index = int(meta.get("fmt_idx") if meta.get("fmt_idx") is not None else index)
            fmt = formats[fmt_index] if fmt_index < len(formats) else {}
            values[_header_name(meta)] = _metric_cell(metric_cells[index], fmt)
        normalized_rows.append({"isGrandtotal": bool(is_grandtotal), "values": values})

    normal_rows = [row for row in normalized_rows if not row["isGrandtotal"]]
    if max_rows is not None and len(normal_rows) > max_rows:
        raise BiError(
            "QUERY_LIMIT_EXCEEDED",
            "BI 返回行数超过本地输出限制。",
            details={"max": max_rows, "actual": len(normal_rows)},
        )
    grand_rows = [row for row in normalized_rows if row["isGrandtotal"]]
    aggregate_only = not row_meta and not row_values and normal_rows
    summary_source = grand_rows[0] if grand_rows else (normal_rows[0] if aggregate_only else None)
    metric_names = {_header_name(header) for header in headers}
    summary = None
    if summary_source:
        summary = {
            name: value
            for name, value in summary_source["values"].items()
            if name in metric_names
        }

    if "count" not in chart or "hasMoreData" not in chart:
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 响应缺少分页元信息。")
    total_count = chart.get("count")
    try:
        total_count = int(total_count)
    except (TypeError, ValueError):
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回的 count 不是整数。")
    if total_count < 0 or not isinstance(chart.get("hasMoreData"), bool):
        raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回的分页元信息无效。")
    return {
        "page": page,
        "pageSize": page_size,
        "totalCount": total_count,
        "hasMoreData": bool(chart.get("hasMoreData")),
        "summary": summary,
        "rows": [] if aggregate_only else [row["values"] for row in normal_rows],
        "columns": [
            *[{"kind": "dimension", "name": _header_name(field), "key": field.get("key")} for field in dimension_headers],
            *[{"kind": "metric", "name": _header_name(field), "key": field.get("key")} for field in headers],
        ],
    }
