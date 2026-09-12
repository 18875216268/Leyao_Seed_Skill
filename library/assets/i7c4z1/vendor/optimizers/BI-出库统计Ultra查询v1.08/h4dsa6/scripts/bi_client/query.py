"""Compile a constrained query plan and execute the BI main query."""

from __future__ import annotations

import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .catalog import display_name, resolve_unique
from .credentials import load_credential, local_expired
from .errors import BiError
from .metadata import fetch_metadata, resolve_filter_field, resolve_tree_fields
from .result import parse_chart_main
from .transport import DirectTransport


_SPLIT_VALUES = re.compile(r"[\n,，;；、\s]+")
_SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
_DEFAULT_CONCURRENCY = 10
_MAX_CONCURRENCY = 30
_MAX_BATCH_QUERIES = 300
_MAX_ATTEMPTS = 3
_PROVINCE_NAMES = frozenset(
    {
        "北京市", "天津市", "河北省", "山西省", "内蒙古自治区",
        "辽宁省", "吉林省", "黑龙江省", "上海市", "江苏省", "浙江省",
        "安徽省", "福建省", "江西省", "山东省", "河南省", "湖北省",
        "湖南省", "广东省", "广西壮族自治区", "海南省", "重庆市",
        "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省",
        "青海省", "宁夏回族自治区", "新疆维吾尔自治区", "台湾省",
        "香港特别行政区", "澳门特别行政区",
    }
)
_CITY_SUFFIXES = ("市", "自治州", "地区", "盟", "行政区划")


@dataclass(frozen=True)
class QueryContext:
    credentials: dict[str, Any]
    metadata: dict[str, Any]
    default_date_range: tuple[str, str] | None = None


def _positive_int(value: Any, field: str, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not re.fullmatch(r"[1-9]\d*", str(value).strip()):
        raise BiError("QUERY_INVALID", f"{field} 必须是正整数。")
    return int(value)


def _validated_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise BiError("QUERY_INVALID", f"{field} 必须是 YYYY-MM-DD 日期。") from exc
    if parsed.isoformat() != text:
        raise BiError("QUERY_INVALID", f"{field} 必须是 YYYY-MM-DD 日期。")
    return text


def _default_date_range() -> list[str]:
    today = datetime.now(_SHANGHAI).date()
    start = today.replace(day=1)
    end = today - timedelta(days=1)
    if start > end:
        start = end
    return [start.isoformat(), end.isoformat()]


def _strip_internal(field: dict[str, Any]) -> dict[str, Any]:
    blocked = {"displayName", "runtimeAvailable", "enabledForMainQuery", "aliases"}
    return {key: value for key, value in field.items() if key not in blocked}


def _normalize_values(value: Any, max_values: int, max_chars: int) -> list[str]:
    if isinstance(value, dict):
        if isinstance(value.get("values"), list):
            source = value["values"]
        elif isinstance(value.get("selected"), list):
            source = value["selected"]
        elif "manual" in value:
            text = str(value.get("manual") or "")
            if len(text) > max_chars:
                raise BiError("QUERY_LIMIT_EXCEEDED", "批量筛选文本过长。")
            source = _SPLIT_VALUES.split(text)
        else:
            source = []
    elif isinstance(value, list):
        source = value
    else:
        source = [value]
    output = []
    for item in source:
        if item is None:
            continue
        text = ("true" if item else "false") if isinstance(item, bool) else str(item).strip()
        if text:
            output.append(text)
    output = list(dict.fromkeys(output))
    if len(output) > max_values:
        raise BiError(
            "QUERY_LIMIT_EXCEEDED",
            "筛选值数量超过限制。",
            details={"max": max_values, "actual": len(output)},
        )
    if sum(len(item) for item in output) > max_chars:
        raise BiError("QUERY_LIMIT_EXCEEDED", "筛选值总长度超过限制。")
    return output


def _validate_province_names(values: list[str]) -> None:
    invalid = [value for value in values if value not in _PROVINCE_NAMES]
    if invalid:
        raise BiError(
            "QUERY_INVALID",
            "省份必须使用完整行政区名称，例如重庆市、四川省。",
            details={"invalid": invalid},
        )


def _validate_region_paths(paths: list[list[str]]) -> None:
    _validate_province_names([path[0] for path in paths])
    invalid_cities = [
        path[1]
        for path in paths
        if len(path) > 1 and not path[1].endswith(_CITY_SUFFIXES)
    ]
    if invalid_cities:
        raise BiError(
            "QUERY_INVALID",
            "城市必须使用完整行政区名称，例如重庆市、成都市。",
            details={"invalid": invalid_cities},
        )


def _filter_specs(
    plan: dict[str, Any],
    profile: dict[str, Any],
    default_date_range: tuple[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    specs: list[dict[str, Any]] = []
    warnings: list[str] = []
    date = plan.get("date")
    if isinstance(date, dict):
        specs.append(
            {
                "field": date.get("field") or profile["defaultDateFilter"]["name"],
                "range": [date.get("start"), date.get("end")],
                "operator": "include",
            }
        )
    elif date is not None:
        raise BiError("QUERY_INVALID", "date 必须是对象。")
    filters = plan.get("filters") or {}
    if isinstance(filters, dict):
        for field, value in filters.items():
            spec = {"field": field, "operator": "include"}
            if isinstance(value, dict):
                spec.update(value)
            else:
                spec["values"] = value if isinstance(value, list) else [value]
            specs.append(spec)
    elif isinstance(filters, list):
        for item in filters:
            if not isinstance(item, dict) or not item.get("field"):
                raise BiError("QUERY_INVALID", "filters 数组中的每项都必须包含 field。")
            specs.append(dict(item))
    else:
        raise BiError("QUERY_INVALID", "filters 必须是对象或数组。")

    has_date = any("range" in spec for spec in specs)
    if not has_date:
        specs.insert(
            0,
            {
                "field": profile["defaultDateFilter"]["name"],
                "range": list(default_date_range or _default_date_range()),
                "operator": "include",
                "macroName": profile["defaultDateFilter"].get("macroName") or "",
            },
        )
        warnings.append("用户未指定日期，已使用出库日期本月第一天到昨天。")
    return specs, warnings


class QueryService:
    def __init__(self, profile: dict[str, Any], catalog: dict[str, Any]) -> None:
        self.profile = profile
        self.catalog = catalog

    def prepare(self) -> QueryContext:
        credentials = load_credential()
        if not credentials:
            raise BiError("AUTH_REQUIRED", "父 skill 凭证不可用，请先运行父 skill 登录：python scripts/bi_login.py")
        if local_expired(credentials):
            raise BiError("AUTH_EXPIRED", "本地 BI 凭证已过期，请通过父 skill 登录器重新扫码。")
        default_range = tuple(_default_date_range())
        for attempt in range(_MAX_ATTEMPTS):
            transport = DirectTransport(self.profile, credentials)
            try:
                metadata = fetch_metadata(transport, self.profile, self.catalog)
                return QueryContext(
                    credentials=credentials,
                    metadata=metadata,
                    default_date_range=default_range,
                )
            except BiError as exc:
                if not exc.retryable or attempt == _MAX_ATTEMPTS - 1:
                    raise
                time.sleep(0.5 * (2**attempt))
            finally:
                transport.close()
        raise AssertionError("unreachable")

    def run_prepared(self, plan: dict[str, Any], context: QueryContext) -> dict[str, Any]:
        if not isinstance(plan, dict):
            raise BiError("QUERY_INVALID", "查询项必须是 JSON 对象。")
        transport = DirectTransport(self.profile, context.credentials)
        try:
            body, effective, warnings = self._build(
                plan,
                context.metadata,
                default_date_range=context.default_date_range,
            )
            response = transport.json(
                transport.bi_post(f"/api/card/{self.profile['cardId']}/data", json=body)
            )
        finally:
            transport.close()
        parsed = parse_chart_main(
            response,
            dimensions=effective["dimensionFields"],
            metrics=effective["metricFields"],
            page=effective["page"],
            page_size=effective["pageSize"],
            max_rows=_positive_int(
                (self.profile.get("limits") or {}).get("outputRows"),
                "配置 outputRows",
                1000,
            ),
        )
        parsed["effectiveQuery"] = {
            "filters": effective["publicFilters"],
            "dimensions": [display_name(field) for field in effective["dimensionFields"]],
            "metrics": [display_name(field) for field in effective["metricFields"]],
            "sort": effective["publicSort"],
        }
        parsed["warnings"] = [*context.metadata.get("warnings", []), *warnings]
        parsed["provenance"] = {
            "profileId": self.profile["profileId"],
            "pageId": self.profile["pageId"],
            "cardId": self.profile["cardId"],
            "datasetId": self.profile["datasetId"],
            "metadataVersion": context.metadata["metadataVersion"],
            "queriedAt": int(time.time()),
        }
        return parsed

    def run_many(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        queries, concurrency = self._validate_batch(request)
        context = self.prepare()
        with ThreadPoolExecutor(max_workers=min(concurrency, len(queries))) as executor:
            return list(executor.map(lambda plan: self._run_item(plan, context), queries))

    def _run_item(self, plan: dict[str, Any], context: QueryContext) -> dict[str, Any]:
        query_id = plan["id"]
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return {"id": query_id, "ok": True, "data": self.run_prepared(plan, context)}
            except BiError as exc:
                if not exc.retryable or attempt == _MAX_ATTEMPTS - 1:
                    return {"id": query_id, "ok": False, "error": exc.to_dict()}
                time.sleep(0.5 * (2**attempt))
            except Exception:
                return {
                    "id": query_id,
                    "ok": False,
                    "error": BiError("INTERNAL_ERROR", "BI 查询发生未分类错误。").to_dict(),
                }
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_batch(request: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        if not isinstance(request, dict):
            raise BiError("BATCH_INVALID", "输入必须是 JSON 对象。")
        queries = request.get("queries")
        if not isinstance(queries, list) or not queries:
            raise BiError("BATCH_INVALID", "queries 必须是非空数组。")
        if len(queries) > _MAX_BATCH_QUERIES:
            raise BiError("BATCH_INVALID", f"单批次查询不能超过 {_MAX_BATCH_QUERIES} 项。")
        concurrency = request.get("concurrency", _DEFAULT_CONCURRENCY)
        if (
            isinstance(concurrency, bool)
            or not isinstance(concurrency, int)
            or not 1 <= concurrency <= _MAX_CONCURRENCY
        ):
            raise BiError("BATCH_INVALID", "concurrency 必须是 1 到 30 的整数。")
        ids: set[str] = set()
        for index, plan in enumerate(queries):
            if not isinstance(plan, dict):
                raise BiError("BATCH_INVALID", f"queries[{index}] 必须是 JSON 对象。")
            query_id = plan.get("id")
            if not isinstance(query_id, str) or not query_id.strip():
                raise BiError("BATCH_INVALID", f"queries[{index}].id 必须是非空字符串。")
            if query_id in ids:
                raise BiError("BATCH_INVALID", f"查询 id 重复：{query_id}")
            ids.add(query_id)
        return queries, concurrency

    def _build(
        self,
        plan: dict[str, Any],
        metadata: dict[str, Any],
        default_date_range: tuple[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        limits = self.profile.get("limits") or {}
        max_page_size = _positive_int(limits.get("pageSize"), "配置 pageSize", 100)
        page = _positive_int(plan.get("page"), "page", 1)
        max_page = _positive_int(limits.get("maxPage"), "配置 maxPage", 10000)
        if page > max_page:
            raise BiError("QUERY_LIMIT_EXCEEDED", f"page 不能超过 {max_page}。")
        page_size = _positive_int(plan.get("pageSize"), "pageSize", min(100, max_page_size))
        if page_size < 1 or page_size > max_page_size:
            raise BiError("QUERY_LIMIT_EXCEEDED", f"pageSize 必须在 1 到 {max_page_size} 之间。")

        dimension_names = plan.get("dimensions") or []
        metric_names = plan.get("metrics") or []
        if not isinstance(dimension_names, list) or not isinstance(metric_names, list):
            raise BiError("QUERY_INVALID", "dimensions 和 metrics 必须是数组。")
        if not metric_names:
            raise BiError("QUERY_INVALID", "至少需要指定一个聚合指标。")
        available_dimensions = [item for item in metadata["dimensions"] if item.get("runtimeAvailable")]
        available_metrics = [item for item in metadata["metrics"] if item.get("runtimeAvailable")]
        dimension_fields = [
            resolve_unique(name, available_dimensions, label="查询字段")
            for name in dimension_names
        ]
        metric_fields = [
            resolve_unique(name, available_metrics, label="聚合字段")
            for name in metric_names
        ]
        selected_keys = [str(field.get("key")) for field in [*dimension_fields, *metric_fields]]
        if len(selected_keys) != len(set(selected_keys)):
            raise BiError("QUERY_INVALID", "dimensions 和 metrics 中不能包含重复字段。")

        selectors = [
            item
            for item in metadata["selectors"]
            if item.get("enabledForMainQuery") and item.get("runtimeAvailable")
        ]
        specs, warnings = _filter_specs(plan, self.profile, default_date_range)
        filters: list[dict[str, Any]] = []
        tree_filters: list[dict[str, Any]] = []
        public_filters: list[dict[str, Any]] = []
        max_values = int(limits.get("filterValues") or 1000)
        max_chars = int(limits.get("filterTextCharacters") or 20000)
        used_selectors: set[str] = set()
        for spec in specs:
            selector = resolve_unique(str(spec.get("field") or ""), selectors, label="筛选字段")
            selector_id = str(selector.get("cdId") or "")
            if selector_id in used_selectors:
                raise BiError("QUERY_INVALID", f"筛选字段不能重复：{selector.get('name')}")
            used_selectors.add(selector_id)
            operator = str(spec.get("operator") or "include").lower()
            if operator not in {"include", "exclude"}:
                raise BiError("QUERY_INVALID", "筛选 operator 只允许 include 或 exclude。")
            selector_type = selector.get("selectorType")
            filter_type = "NOT_IN" if operator == "exclude" else (selector.get("filterType") or "IN")
            if selector_type == "TIME_MACRO":
                if operator == "exclude":
                    raise BiError("QUERY_INVALID", "日期筛选不支持 exclude。")
                date_range = spec.get("range")
                if not isinstance(date_range, list) or len(date_range) != 2 or not all(date_range):
                    raise BiError("QUERY_INVALID", f"日期筛选 {selector.get('name')} 必须包含开始和结束日期。")
                date_range = [
                    _validated_date(date_range[0], "开始日期"),
                    _validated_date(date_range[1], "结束日期"),
                ]
                if date_range[0] > date_range[1]:
                    raise BiError("QUERY_INVALID", "开始日期不能晚于结束日期。")
                field = resolve_filter_field(selector, str(self.profile["cardId"]))
                if not field:
                    raise BiError("METADATA_MISMATCH", f"筛选器缺少目标字段：{selector.get('name')}")
                entry = {
                    "name": field.get("name"),
                    "fdId": field.get("fdId"),
                    "dsId": self.profile["datasetId"],
                    "cdId": self.profile["cardId"],
                    "fdType": field.get("fdType") or "DATE",
                    "filterType": "BT",
                    "sourceCdId": selector.get("cdId"),
                    "filterValue": date_range,
                    "displayValue": date_range,
                }
                if spec.get("macroName"):
                    entry["macroName"] = spec["macroName"]
                    entry["displayValue"] = []
                filters.append(entry)
                public_filters.append({"field": selector.get("name"), "operator": "between", "value": date_range})
                continue
            if selector_type == "TREE":
                paths = spec.get("treePaths")
                if paths is None and spec.get("manual") is not None:
                    values = _normalize_values({"manual": spec.get("manual")}, max_values, max_chars)
                    paths = [[part.strip() for part in re.split(r"[>/]", value) if part.strip()] for value in values]
                if not isinstance(paths, list) or not paths:
                    raise BiError("QUERY_INVALID", f"树筛选 {selector.get('name')} 需要 treePaths。")
                normalized_paths = [
                    [str(part).strip() for part in path if str(part or "").strip()]
                    for path in paths
                    if isinstance(path, list)
                ]
                normalized_paths = [path for path in normalized_paths if path]
                if not normalized_paths:
                    raise BiError("QUERY_INVALID", f"树筛选 {selector.get('name')} 没有有效路径。")
                _validate_region_paths(normalized_paths)
                if len(normalized_paths) > max_values:
                    raise BiError("QUERY_LIMIT_EXCEEDED", "树筛选路径数量超过限制。")
                if sum(len(part) for path in normalized_paths for part in path) > max_chars:
                    raise BiError("QUERY_LIMIT_EXCEEDED", "树筛选路径总长度超过限制。")
                tree_fields = resolve_tree_fields(
                    selector,
                    str(self.profile["cardId"]),
                    str(self.profile["datasetId"]),
                )
                if any(len(path) > len(tree_fields) for path in normalized_paths):
                    raise BiError("QUERY_INVALID", f"树筛选 {selector.get('name')} 路径层级过深。")
                tree_filters.append(
                    {
                        "name": selector.get("name"),
                        "dsId": self.profile["datasetId"],
                        "cdId": self.profile["cardId"],
                        "sourceCdId": selector.get("cdId"),
                        "filterType": filter_type,
                        "withPath": (selector.get("content") or {}).get("withPath") is not False,
                        # 直连键名修正（实测 fieldSeq/filterValue 会被 BI 5001 拒绝）：
                        # 树筛选必须用 fields（层级字段序列）+ values（路径数组）。
                        "fields": tree_fields,
                        "values": normalized_paths,
                    }
                )
                public_filters.append({"field": selector.get("name"), "operator": operator, "value": normalized_paths})
                continue

            values = _normalize_values(spec, max_values, max_chars)
            if not values:
                raise BiError("QUERY_INVALID", f"筛选器 {selector.get('name')} 没有有效值。")
            if selector.get("name") == "省份":
                _validate_province_names(values)
            field = resolve_filter_field(selector, str(self.profile["cardId"]))
            if not field:
                raise BiError("METADATA_MISMATCH", f"筛选器缺少目标字段：{selector.get('name')}")
            filters.append(
                {
                    "name": field.get("name"),
                    "fdId": field.get("fdId"),
                    "dsId": self.profile["datasetId"],
                    "cdId": self.profile["cardId"],
                    "fdType": field.get("fdType") or "STRING",
                    "filterType": filter_type,
                    "sourceCdId": selector.get("cdId"),
                    "filterValue": values,
                    "displayValue": values,
                }
            )
            public_filters.append({"field": selector.get("name"), "operator": operator, "value": values})

        sort_spec = plan.get("sort")
        header_sortings = None
        public_sort = None
        if sort_spec:
            if not isinstance(sort_spec, dict):
                raise BiError("QUERY_INVALID", "sort 必须是对象。")
            order = str(sort_spec.get("order") or "").lower()
            if order not in {"asc", "desc"}:
                raise BiError("QUERY_INVALID", "sort.order 只允许 asc 或 desc。")
            selected = [*dimension_fields, *metric_fields]
            sort_field = resolve_unique(str(sort_spec.get("field") or ""), selected, label="排序字段")
            dim_index = next((i for i, field in enumerate(dimension_fields) if field.get("key") == sort_field.get("key")), -1)
            if dim_index >= 0:
                header_sortings = [{"order": order, "zoneId": "row", "index": dim_index, "sortField": f"c{dim_index}"}]
            else:
                metric_index = next(i for i, field in enumerate(metric_fields) if field.get("key") == sort_field.get("key"))
                index = len(dimension_fields) + metric_index
                header_sortings = [
                    {
                        "order": order,
                        "zoneId": "column",
                        "index": index,
                        "sortField": f"c{index}",
                        "mIndex": metric_index,
                    }
                ]
            public_sort = {"field": display_name(sort_field), "order": order}

        body = {
            "offset": (page - 1) * page_size,
            "limit": page_size,
            "filters": filters,
            "treeFilters": tree_filters,
            "dynamicParams": [],
            "dynamicFieldFilters": [],
            "combinationFilters": [],
            "layerTreeFilters": [],
            "headerSortings": header_sortings,
            "rowExpand": None,
            "sorting": [],
            "name": self.profile["cardName"],
            "zoneFilter": {
                "zoneData": {
                    "row": [_strip_internal(field) for field in dimension_fields],
                    "column": metadata["columnFields"],
                    "metric": [_strip_internal(field) for field in metric_fields],
                    "sorting": [],
                }
            },
            "taskRequestId": uuid.uuid4().hex[:18],
        }
        effective = {
            "page": page,
            "pageSize": page_size,
            "dimensionFields": dimension_fields,
            "metricFields": metric_fields,
            "publicFilters": public_filters,
            "publicSort": public_sort,
        }
        return body, effective, warnings
