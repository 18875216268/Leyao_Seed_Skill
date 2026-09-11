#!/usr/bin/env python3
"""Query the three PMS promotion-profit views as JSON or CSV."""

from __future__ import annotations

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from pms_common import (
    DATA_BASE,
    MAX_SPAN_SECONDS,
    PmsClient,
    PmsError,
    QueryScope,
    add_common_filters,
    apply_time_defaults,
    base_payload,
    day_ranges,
    load_runtime_state,
    print_error,
    resolve_scope,
    resolve_token,
    success_payload,
    validate_span,
    write_csv,
    write_json_or_print,
)
from pms_options import resolve_operator_filter


ENDPOINTS = {
    "summary": f"{DATA_BASE}/pms_operation/web/ysbOrder/orderGoods/pv1461",
    "goods": f"{DATA_BASE}/pms_operation/web/ysbOrder/orderGoodsDetail/pv1461",
    "orders": f"{DATA_BASE}/pms_operation/web/ysbOrder/pageOrderDetail/pv1461",
}


def add_query_specific(parser: argparse.ArgumentParser, kind: str) -> None:
    is_detail = kind != "summary"
    add_common_filters(parser, include_pagination=is_detail, include_operator=is_detail)
    parser.add_argument(
        "--all-warehouses",
        action="store_true",
        help="Use [] instead of cached warehouse IDs",
    )
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    if is_detail:
        parser.add_argument(
            "--concurrency",
            type=int,
            default=1,
            help="Optional day-query concurrency (1-20; default 1)",
        )
    if kind == "orders":
        parser.add_argument("--no-dedupe", action="store_true")
        parser.add_argument("--auto-op", action="append", default=[])
        parser.add_argument("--sui-xin-gou", choices=["", "0", "1"], default="")
        parser.add_argument("--link-flag", action="append", default=[])
        parser.add_argument("--wholesale-special", action="append", default=[])


def _records_from_body(body: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    data = body.get("data")
    if not isinstance(data, dict):
        raise PmsError("PMS detail response data must be an object.")
    records = data.get("records")
    if not isinstance(records, list) or not all(
        isinstance(row, dict) for row in records
    ):
        raise PmsError("PMS detail response data.records must be a list of objects.")
    try:
        pages = int(data.get("pages", 1))
    except (TypeError, ValueError) as exc:
        raise PmsError("PMS detail response data.pages must be an integer.") from exc
    if pages < 0 or (pages == 0 and records):
        raise PmsError(
            "PMS detail response data.pages must be non-negative and match records."
        )
    return records, pages


def _summary_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data")
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise PmsError("PMS summary response data must be a list of objects.")
    return data


def _dedupe_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        fingerprint = json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(row)
    return result


def _new_client(args: argparse.Namespace, state: dict[str, Any]) -> PmsClient:
    return PmsClient(
        resolve_token(state), insecure=args.insecure, no_proxy=args.no_proxy
    )


def fetch_range(
    args: argparse.Namespace,
    state: dict[str, Any],
    scope: QueryScope,
    kind: str,
    start: str,
    end: str,
    client: PmsClient,
) -> list[dict[str, Any]]:
    payload = base_payload(args, state, scope, kind, start, end)
    body = client.post_json(ENDPOINTS[kind], payload)
    if kind == "summary":
        return _summary_from_body(body)
    rows, pages = _records_from_body(body)
    if args.all_pages:
        for page in range(2, pages + 1):
            payload["current"] = page
            next_rows, _ = _records_from_body(
                client.post_json(ENDPOINTS[kind], payload)
            )
            rows.extend(next_rows)
    return rows


def plan_query(
    args: argparse.Namespace, kind: str
) -> tuple[list[tuple[str, str]], int]:
    concurrency = getattr(args, "concurrency", 1)
    if concurrency < 1 or concurrency > 20:
        raise PmsError("--concurrency must be between 1 and 20.")
    if kind != "summary":
        if args.page < 1 or args.page_size < 1:
            raise PmsError("--page and --page-size must be greater than zero.")
        if args.all_pages and args.page != 1:
            raise PmsError("--all-pages requires --page 1.")
    split_days = bool(getattr(args, "split_days", False))
    start_dt, end_dt = validate_span(args.start, args.end, allow_long=True)
    span = (end_dt - start_dt).total_seconds()
    if kind == "summary" and span > MAX_SPAN_SECONDS:
        raise PmsError(
            "Summary data is an aggregate array; use a <=48h range or query each day separately."
        )
    if span <= MAX_SPAN_SECONDS:
        return [(args.start, args.end)], concurrency
    if not split_days:
        raise PmsError("Use --split-days for detail queries longer than 48 hours.")
    return day_ranges(start_dt, end_dt), concurrency


def fetch_ranges(
    args: argparse.Namespace,
    state: dict[str, Any],
    scope: QueryScope,
    kind: str,
    ranges: list[tuple[str, str]],
    concurrency: int,
) -> list[dict[str, Any]]:
    if concurrency == 1 or len(ranges) == 1:
        client = _new_client(args, state)
        results = [
            fetch_range(args, state, scope, kind, start, end, client)
            for start, end in ranges
        ]
    else:
        thread_state = threading.local()

        def fetch(item: tuple[str, str]) -> list[dict[str, Any]]:
            client = getattr(thread_state, "client", None)
            if client is None:
                client = _new_client(args, state)
                thread_state.client = client
            return fetch_range(args, state, scope, kind, item[0], item[1], client)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            results = list(executor.map(fetch, ranges))
    return [row for result in results for row in result]


def run(args: argparse.Namespace, kind: str) -> int:
    apply_time_defaults(args)
    state = load_runtime_state(args)
    scope = resolve_scope(args, state)
    if kind != "summary":
        resolve_operator_filter(args, state, scope)
    ranges, concurrency = plan_query(args, kind)
    rows = fetch_ranges(args, state, scope, kind, ranges, concurrency)
    if kind == "orders" and not args.no_dedupe:
        rows = _dedupe_orders(rows)
    result = success_payload(
        kind,
        rows,
        providerId=scope.provider_id,
        warehouseIds=list(scope.warehouse_ids),
        payTimeStart=args.start,
        payTimeEnd=args.end,
        records=len(rows),
        concurrency=concurrency,
    )
    if args.format == "csv":
        write_csv(rows, args.output)
    else:
        write_json_or_print(result, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query PMS promotion-profit data")
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("summary", "goods", "orders"):
        child = sub.add_parser(kind)
        add_query_specific(child, kind)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args, args.kind)
    except (PmsError, requests.RequestException) as exc:
        print_error(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
