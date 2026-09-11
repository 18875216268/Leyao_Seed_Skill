#!/usr/bin/env python3
"""Read PMS promotion-profit filter options and resolve exact names."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import requests

from pms_common import (
    APP_VERSION,
    MODULE,
    PMS_BASE,
    PmsClient,
    PmsError,
    QueryScope,
    default_state_path,
    load_runtime_state,
    now_text,
    print_error,
    resolve_scope,
    resolve_token,
    success_payload,
    unix_seconds,
    write_json_or_print,
)


OPERATORS = f"{PMS_BASE}/api/drugs/operatorChooseList/v520"


def get_operator_options(
    client: PmsClient, state: dict[str, Any], scope: QueryScope
) -> list[dict[str, Any]]:
    token = resolve_token(state)
    body = client.post_form(
        OPERATORS,
        {
            "providerId": scope.provider_id,
            "token": token,
            "__module__": MODULE,
            "module": MODULE,
            "appVersion": APP_VERSION,
            "platform": "web",
            "timestamp": unix_seconds(),
            "buildTime": state.get("build_time") or now_text(),
        },
    )
    options = body.get("data")
    if not isinstance(options, list) or not all(
        isinstance(option, dict) for option in options
    ):
        raise PmsError("Operator options response data must be a list of objects.")
    return options


def exact_operator(options: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [option for option in options if str(option.get("erpName", "")) == name]
    if not matches:
        raise PmsError(f"No operating manager exactly matches {name!r}.")
    ids = {str(option.get("userId", "")) for option in matches}
    if "" in ids or len(ids) != 1:
        raise PmsError(f"Operating manager name {name!r} is not uniquely resolvable.")
    return matches[0]


def resolve_operator_filter(
    args: argparse.Namespace, state: dict[str, Any], scope: QueryScope
) -> None:
    name = getattr(args, "operator_name", "")
    if not name:
        return
    client = PmsClient(
        resolve_token(state), insecure=args.insecure, no_proxy=args.no_proxy
    )
    option = exact_operator(get_operator_options(client, state, scope), name)
    args.operator_id = str(option["userId"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read PMS promotion-profit options")
    sub = parser.add_subparsers(dest="kind", required=True)
    operators = sub.add_parser("operators", help="List or resolve operating managers")
    operators.add_argument("--name", help="Return one exact erpName match")
    operators.add_argument("--provider-id")
    operators.add_argument("--state-file", type=Path, default=default_state_path())
    operators.add_argument("--token")
    operators.add_argument("--output", type=Path)
    operators.add_argument("--insecure", action="store_true")
    operators.add_argument("--no-proxy", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    state = load_runtime_state(args)
    scope_args = argparse.Namespace(
        provider_id=args.provider_id, warehouse_id=[], all_warehouses=True
    )
    scope = resolve_scope(scope_args, state)
    client = PmsClient(
        resolve_token(state), insecure=args.insecure, no_proxy=args.no_proxy
    )
    options = get_operator_options(client, state, scope)
    data: Any = exact_operator(options, args.name) if args.name else options
    write_json_or_print(
        success_payload(
            "operators", data, providerId=scope.provider_id, records=len(options)
        ),
        args.output,
    )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(args)
    except (PmsError, requests.RequestException) as exc:
        print_error(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
