"""Shared transport, credential, validation, and CLI helpers for PMS promo profit v1.08.

登录由父 skill 负责，本模块不实现任何登录流程，只消费凭证。

凭证来源优先级（与父 skill 自有登录组件一致）：
    --token  >  环境变量 PMS_TOKEN  >  凭证文件（--state-file / PMS_STATE_FILE，可选）
凭证文件由父 skill 或用户提供，本包不生成它；文件不存在时不会报错，
此时必须显式提供 --token 或 PMS_TOKEN。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests


APP_VERSION = "14.30.1"
MODULE = "promo_profit_monitor"
ORIGIN = "https://pms.ysbang.cn"
DATA_BASE = "https://pms.leyopharm.com"
PMS_BASE = "https://pms.ysbang.cn"
DEFAULT_TIMEOUT = 90
MAX_SPAN_SECONDS = 172800
USER_AGENT = "pms-cxml/1.08"
CHINA_TIMEZONE = timezone(timedelta(hours=8))
LOGIN_HINT = (
    "Obtain a credential from the parent skill (python scripts/pms_login.py at the skill root), "
    "or supply one via --token, PMS_TOKEN, or --state-file."
)


class PmsError(RuntimeError):
    """A user-actionable PMS/API error."""


def default_state_path() -> Path:
    """凭证文件路径（可选）：PMS_STATE_FILE 优先，默认 ~/.promo_profit_monitor/credential.json。

    该文件由父 skill 或用户提供，本包不写入它；不存在时视为无凭证文件。
    """
    configured = os.getenv("PMS_STATE_FILE")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".promo_profit_monitor" / "credential.json"


def now_text() -> str:
    return datetime.now(CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def unix_seconds() -> str:
    return str(int(time.time()))


def trace_id() -> str:
    return uuid.uuid4().hex


def load_state(path: Path) -> dict[str, Any]:
    """读取用户提供的凭证文件；文件不存在或格式错误时给出可执行的修复提示。"""
    path = path.expanduser()
    if not path.exists():
        raise PmsError(f"Credential file not found: {path}. {LOGIN_HINT}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PmsError(f"Cannot read credential file {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise PmsError(f"Credential file must contain a JSON object: {path}")
    return state


def resolve_token(state: dict[str, Any], explicit_token: str | None = None) -> str:
    """取凭证：--token > PMS_TOKEN > 凭证文件。都没有时给出父 skill 获取指引。"""
    token = explicit_token or os.getenv("PMS_TOKEN") or state.get("token")
    if not token:
        raise PmsError(f"No PMS token found. {LOGIN_HINT}")
    return str(token)


def load_runtime_state(args: argparse.Namespace) -> dict[str, Any]:
    state = load_state(args.state_file) if args.state_file.expanduser().exists() else {}
    state = dict(state)
    state["token"] = resolve_token(state, getattr(args, "token", None))
    return state


def _normalize_id(value: Any) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True)
class QueryScope:
    provider_id: int | str
    warehouse_ids: tuple[int, ...]


def _warehouse_ids_for_provider(
    state: dict[str, Any], provider_id: int | str
) -> set[int] | None:
    for company in state.get("warehouses") or []:
        if not isinstance(company, dict) or str(company.get("providerId")) != str(
            provider_id
        ):
            continue
        return {
            int(item["warehouseId"])
            for item in company.get("warehouseList") or []
            if isinstance(item, dict) and item.get("warehouseId") is not None
        }
    return None


def resolve_scope(args: argparse.Namespace, state: dict[str, Any]) -> QueryScope:
    value = (
        getattr(args, "provider_id", None)
        or state.get("provider_id")
        or os.getenv("PMS_PROVIDER_ID")
    )
    if value in (None, ""):
        raise PmsError(
            "providerId is required. Pass --provider-id, set PMS_PROVIDER_ID, "
            "or supply a credential file that contains it."
        )
    provider_id = _normalize_id(value)
    available_providers = {
        str(item.get("id"))
        for item in state.get("providers") or []
        if isinstance(item, dict) and item.get("id") is not None
    }
    if available_providers and str(provider_id) not in available_providers:
        raise PmsError(f"providerId {provider_id} is not available for this account.")

    explicit_warehouses = parse_int_values(getattr(args, "warehouse_id", []))
    all_warehouses = bool(getattr(args, "all_warehouses", False))
    if all_warehouses and explicit_warehouses:
        raise PmsError("--all-warehouses cannot be combined with --warehouse-id.")
    if explicit_warehouses:
        allowed = _warehouse_ids_for_provider(state, provider_id)
        invalid = [
            value
            for value in explicit_warehouses
            if allowed is not None and value not in allowed
        ]
        if invalid:
            raise PmsError(
                f"warehouseIds {invalid} do not belong to providerId {provider_id}."
            )
        warehouse_ids = explicit_warehouses
    elif all_warehouses or str(provider_id) != str(state.get("provider_id")):
        warehouse_ids = []
    else:
        warehouse_ids = [int(value) for value in state.get("warehouse_ids") or []]
    return QueryScope(provider_id, tuple(dict.fromkeys(warehouse_ids)))


def parse_csv_values(values: Iterable[str] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        output.extend(item.strip() for item in str(value).split(",") if item.strip())
    return output


def parse_int_values(values: Iterable[str] | None) -> list[int]:
    output: list[int] = []
    for value in parse_csv_values(values):
        try:
            output.append(int(value))
        except ValueError as exc:
            raise PmsError(f"Expected integer list, got: {value}") from exc
    return output


def parse_range_divide(value: str | None, default: list[int]) -> list[int]:
    values = parse_int_values([value]) if value else default
    if not values or any(item <= 0 for item in values) or values != sorted(set(values)):
        raise PmsError("--range-divide must be positive, strictly increasing integers.")
    return values


def validate_datetime(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise PmsError(f"Invalid datetime {value!r}; use yyyy-MM-dd HH:mm:ss.") from exc


def resolve_time_range(
    start: str | None, end: str | None, *, now: datetime | None = None
) -> tuple[str, str]:
    if bool(start) != bool(end):
        raise PmsError("--start and --end must be supplied together or both omitted.")
    if start and end:
        return start, end
    current = now or datetime.now(CHINA_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CHINA_TIMEZONE)
    else:
        current = current.astimezone(CHINA_TIMEZONE)
    day = current.strftime("%Y-%m-%d")
    return f"{day} 00:00:00", f"{day} 23:59:59"


def apply_time_defaults(args: argparse.Namespace) -> tuple[str, str]:
    args.start, args.end = resolve_time_range(
        getattr(args, "start", None), getattr(args, "end", None)
    )
    return args.start, args.end


def validate_span(
    start: str, end: str, allow_long: bool = False
) -> tuple[datetime, datetime]:
    start_dt, end_dt = validate_datetime(start), validate_datetime(end)
    if end_dt < start_dt:
        raise PmsError("payTimeEnd must be greater than or equal to payTimeStart.")
    if not allow_long and (end_dt - start_dt).total_seconds() > MAX_SPAN_SECONDS:
        raise PmsError(
            "A direct PMS query may span at most 48 hours. Split the range by day or use export."
        )
    return start_dt, end_dt


def day_ranges(start: datetime, end: datetime) -> list[tuple[str, str]]:
    ranges: list[tuple[str, str]] = []
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        day_start = max(start, datetime.combine(cursor, datetime.min.time()))
        day_end = min(
            end, datetime.combine(cursor, datetime.max.time().replace(microsecond=0))
        )
        ranges.append(
            (
                day_start.strftime("%Y-%m-%d %H:%M:%S"),
                day_end.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        cursor += timedelta(days=1)
    return ranges


def add_common_filters(
    parser: argparse.ArgumentParser,
    *,
    include_pagination: bool = True,
    include_operator: bool = True,
    output_required: bool = False,
) -> None:
    parser.add_argument(
        "--state-file",
        type=Path,
        default=default_state_path(),
        help="Credential file (optional): provided by the parent skill or the user",
    )
    parser.add_argument(
        "--token",
        help="PMS token; defaults to the PMS_TOKEN env var or the credential file",
    )
    parser.add_argument("--provider-id")
    parser.add_argument(
        "--warehouse-id",
        action="append",
        default=[],
        help="Repeat or comma-separate warehouse IDs",
    )
    parser.add_argument(
        "--start",
        help="yyyy-MM-dd HH:mm:ss; omit with --end to use today in China time",
    )
    parser.add_argument(
        "--end",
        help="yyyy-MM-dd HH:mm:ss; omit with --start to use today in China time",
    )
    parser.add_argument("--staff-name", default="")
    if include_operator:
        operator = parser.add_mutually_exclusive_group()
        operator.add_argument("--operator-id", default="")
        operator.add_argument(
            "--operator-name",
            default="",
            help="Exact operating-manager name; resolved to operatorId by the options API",
        )
    parser.add_argument("--goods-code", default="")
    parser.add_argument("--djbh", default="")
    parser.add_argument("--business-type", action="append", default=[])
    parser.add_argument(
        "--activity-type",
        action="append",
        default=[],
        help="deliverTypesV1070 request code",
    )
    parser.add_argument("--client-type", action="append", default=[])
    parser.add_argument(
        "--province",
        action="append",
        default=[],
        help="Province name selected by the AI/user; repeat or comma-separate",
    )
    parser.add_argument("--city", action="append", default=[])
    parser.add_argument(
        "--range-divide", help="Comma-separated positive ascending boundaries"
    )
    parser.add_argument("--ascs", default="")
    parser.add_argument("--descs", default="")
    if include_pagination:
        parser.add_argument("--page", type=int, default=1)
        parser.add_argument("--page-size", type=int, default=100)
        parser.add_argument("--all-pages", action="store_true")
        parser.add_argument(
            "--split-days",
            action="store_true",
            help="For detail queries, split ranges longer than 48h by local day",
        )
    parser.add_argument("--output", type=Path, required=output_required)
    parser.add_argument(
        "--insecure", action="store_true", help="Disable TLS certificate verification"
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Ignore system and environment proxy settings for this command",
    )


class PmsClient:
    def __init__(
        self,
        token: str,
        *,
        insecure: bool = False,
        no_proxy: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.token = token
        self.verify = not insecure
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = not no_proxy

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "appversion": APP_VERSION,
            "platform": "web",
            "module": MODULE,
            "origin": ORIGIN,
            "referer": f"{ORIGIN}/",
            "token": self.token,
            "timestamp": unix_seconds(),
            "traceid": trace_id(),
            "user-agent": USER_AGENT,
            "content-type": "application/json",
        }

    def post_raw(
        self, url: str, payload: dict[str, Any], *, timeout: int | None = None
    ) -> requests.Response:
        response = self.session.post(
            url,
            headers=self._headers(),
            json=payload,
            verify=self.verify,
            timeout=timeout or self.timeout,
        )
        if response.status_code >= 400:
            raise PmsError(f"HTTP {response.status_code} from PMS endpoint")
        return response

    def post_json(
        self, url: str, payload: dict[str, Any], *, retries: int = 2
    ) -> dict[str, Any]:
        for attempt in range(retries + 1):
            response = self.post_raw(url, payload)
            try:
                body = response.json()
            except ValueError as exc:
                raise PmsError(
                    f"Expected JSON from {url}, got {response.headers.get('content-type')}"
                ) from exc
            if not isinstance(body, dict):
                raise PmsError(f"Expected a JSON object from {url}.")
            code = str(body.get("code", ""))
            if code == "42053":
                if attempt < retries:
                    time.sleep(min(30, 2**attempt * 2))
                    continue
                raise PmsError(
                    f"PMS error 42053 after {retries + 1} attempts: {body.get('message') or body.get('msg')}"
                )
            if code == "401":
                raise PmsError(f"PMS token expired or invalid (401). {LOGIN_HINT}")
            if code != "40001":
                raise PmsError(
                    f"PMS error {code or 'MISSING_CODE'}: {body.get('message') or body.get('msg')}"
                )
            return body

    def post_form(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        headers["content-type"] = "application/x-www-form-urlencoded"
        response = self.session.post(
            url,
            headers=headers,
            data=payload,
            verify=self.verify,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise PmsError(f"HTTP {response.status_code} from PMS endpoint")
        try:
            body = response.json()
        except ValueError as exc:
            raise PmsError(f"Expected JSON from {url}.") from exc
        if not isinstance(body, dict):
            raise PmsError(f"Expected a JSON object from {url}.")
        code = str(body.get("code", ""))
        if code == "401":
            raise PmsError(f"PMS token expired or invalid (401). {LOGIN_HINT}")
        if code != "40001":
            raise PmsError(
                f"PMS error {code or 'MISSING_CODE'}: {body.get('message') or body.get('msg')}"
            )
        return body

    def download(self, url: str, destination: Path) -> int:
        if urlparse(url).scheme.lower() != "https":
            raise PmsError("Export download URL must use HTTPS.")
        bytes_written = 0
        with self.session.get(
            url,
            headers={"user-agent": USER_AGENT},
            verify=self.verify,
            timeout=self.timeout,
            stream=True,
        ) as response:
            if response.status_code >= 400:
                raise PmsError(f"HTTP {response.status_code} while downloading export.")
            if urlparse(response.url).scheme.lower() != "https":
                raise PmsError("Export download redirected to a non-HTTPS URL.")
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
                        bytes_written += len(chunk)
        return bytes_written


def base_payload(
    args: argparse.Namespace,
    state: dict[str, Any],
    scope: QueryScope,
    kind: str,
    start: str,
    end: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "providerId": scope.provider_id,
        "warehouseIds": list(scope.warehouse_ids),
        "staffName": args.staff_name,
        "goodsCode": args.goods_code,
        "djbh": args.djbh,
        "businessType": parse_int_values(args.business_type),
        "deliverTypesV1070": parse_int_values(args.activity_type),
        "clientType": parse_int_values(args.client_type),
        "provinceName": parse_csv_values(args.province),
        "cityName": parse_csv_values(args.city),
        "payTimeStart": start,
        "payTimeEnd": end,
        "current": getattr(args, "page", 1),
        "size": getattr(args, "page_size", 100),
        "ascs": args.ascs,
        "descs": args.descs,
        "rangeDivideList": parse_range_divide(
            args.range_divide,
            [1, 200, 500, 1000, 2000],
        ),
        "token": resolve_token(state),
        "buildTime": state.get("build_time")
        or os.getenv("PMS_BUILD_TIME")
        or now_text(),
    }
    if kind in {"goods", "orders"}:
        payload["operatorId"] = args.operator_id
    if kind == "orders":
        payload.update(
            {
                "autoOpFlags": parse_int_values(args.auto_op),
                "beSuiXinGou": args.sui_xin_gou,
                "linkFlags": parse_int_values(args.link_flag),
                "wholesaleSpecialFlags": parse_int_values(args.wholesale_special),
            }
        )
    return payload


def write_json_or_print(value: Any, output: Path | None) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def success_payload(kind: str, data: Any, **meta: Any) -> dict[str, Any]:
    """Create the stable envelope consumed by AI callers."""
    return {"ok": True, "kind": kind, "meta": meta, "data": data}


def error_payload(exc: BaseException) -> dict[str, Any]:
    message = re.sub(r"(?i)(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", str(exc))
    message = re.sub(
        r"(?i)\b(token|password|auth_code)=([^\s&,]+)",
        r"\1=[redacted]",
        message,
    )
    if isinstance(exc, requests.exceptions.ProxyError):
        code = "PROXY_ERROR"
    elif isinstance(exc, requests.exceptions.Timeout):
        code = "TIMEOUT"
    elif isinstance(exc, requests.exceptions.SSLError):
        code = "TLS_ERROR"
    else:
        match = re.search(r"\b(401|42053)\b", message)
        code = match.group(1) if match else "CLIENT_ERROR"
    actions = {
        "401": LOGIN_HINT,
        "42053": "Check the datetime format, reduce the range to 48 hours, or split detail queries by day.",
        "PROXY_ERROR": "Retry the same command with --no-proxy; do not change system proxy settings.",
        "TIMEOUT": "Check network/service status and retry the same bounded operation.",
        "TLS_ERROR": "Repair the local CA configuration; use --insecure only in a controlled environment.",
    }
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "action": actions.get(
                code, "Check the command parameters and dependencies."
            ),
        },
    }


def print_error(exc: BaseException) -> None:
    print(json.dumps(error_payload(exc), ensure_ascii=False), file=sys.stderr)


def write_csv(records: list[dict[str, Any]], output: Path | None) -> None:
    if not records:
        text = ""
    else:
        keys = list(dict.fromkeys(key for row in records for key in row.keys()))
        import io

        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        text = buffer.getvalue()
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8-sig")
    else:
        sys.stdout.write(text)
