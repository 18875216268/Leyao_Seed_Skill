#!/usr/bin/env python3
"""Export PMS promotion-profit data, including encrypted order exports."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

from pms_common import (
    DATA_BASE,
    PmsClient,
    PmsError,
    QueryScope,
    add_common_filters,
    apply_time_defaults,
    base_payload,
    load_runtime_state,
    print_error,
    resolve_scope,
    resolve_token,
    success_payload,
    validate_span,
)
from pms_options import resolve_operator_filter


SYNC_ENDPOINTS = {
    "summary": f"{DATA_BASE}/pms_operation/web/ysbOrder/orderGoodsExport/pv1461",
    "goods": f"{DATA_BASE}/pms_operation/web/ysbOrder/orderGoodsDetailExport/pv1461",
}
ORDER_SUBMIT = f"{DATA_BASE}/pms_operation/web/ysbOrder/exportBigOrderDetail/pv22110"
COUNT_GOODS = f"{DATA_BASE}/pms_operation/web/ysbOrder/countOrderDetail/pv22110"
TASK_LIST = f"{DATA_BASE}/datacenter_pms/web/exportinfo/listExportInfoByType/pv583"
TASK_PASSWORD = f"{DATA_BASE}/datacenter_pms/web/exportinfo/selectExportPassword/pv583"
TASK_PAGE_SIZE = 50
EXPORT_ROW_LIMIT = 60000


def add_export_specific(parser: argparse.ArgumentParser, kind: str) -> None:
    add_common_filters(
        parser,
        include_pagination=False,
        include_operator=kind != "summary",
        output_required=True,
    )
    parser.add_argument("--all-warehouses", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-long",
        action="store_true",
        help="Allow ranges longer than 48h; the export service may still reject them",
    )
    if kind == "goods":
        parser.add_argument(
            "--count-first",
            action="store_true",
            help="Ask the server for the estimated row count first",
        )
    if kind == "orders":
        parser.add_argument("--auto-op", action="append", default=[])
        parser.add_argument("--sui-xin-gou", choices=["", "0", "1"], default="")
        parser.add_argument("--link-flag", action="append", default=[])
        parser.add_argument("--wholesale-special", action="append", default=[])
        parser.add_argument("--poll-interval", type=float, default=3.0)
        parser.add_argument("--wait-timeout", type=int, default=900)
        parser.add_argument(
            "--raw-zip", type=Path, help="Also preserve the encrypted source zip"
        )


def ensure_output(path: Path, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise PmsError(
            f"Output already exists: {path}; pass --overwrite to replace it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)


def validate_xlsx(path: Path) -> None:
    try:
        import openpyxl
    except ImportError as exc:
        raise PmsError("Install openpyxl to validate exported workbooks.") from exc
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        if not workbook.sheetnames:
            raise PmsError("Exported workbook contains no worksheets.")
        workbook.close()
    except PmsError:
        raise
    except Exception as exc:
        raise PmsError(f"Exported file is not a readable xlsx: {exc}") from exc


def save_validated_xlsx(content: bytes, output: Path) -> None:
    with tempfile.NamedTemporaryFile(
        dir=output.parent, suffix=".xlsx", delete=False
    ) as handle:
        candidate = Path(handle.name)
        handle.write(content)
    try:
        validate_xlsx(candidate)
        candidate.replace(output)
    except Exception:
        candidate.unlink(missing_ok=True)
        raise


def order_task_id(task: dict[str, Any]) -> str:
    task_id = task.get("id")
    if task_id in (None, ""):
        raise PmsError("Order export task is missing id.")
    return str(task_id)


def select_new_order_task(
    records: list[dict[str, Any]], baseline_ids: set[str]
) -> dict[str, Any] | None:
    new_tasks = [row for row in records if order_task_id(row) not in baseline_ids]
    if len(new_tasks) > 1:
        raise PmsError(
            "Multiple new order export tasks appeared after submission; "
            "serialize exports for the same credential and retry."
        )
    return new_tasks[0] if new_tasks else None


def list_order_export_tasks(client: PmsClient, token: str) -> list[dict[str, Any]]:
    listing = client.post_json(
        TASK_LIST,
        {"current": 1, "size": TASK_PAGE_SIZE, "type": 58, "token": token},
    )
    data = listing.get("data")
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list) or not all(
        isinstance(row, dict) for row in records
    ):
        raise PmsError("Order export task response data.records must be a list.")
    task_ids = [order_task_id(row) for row in records]
    if len(task_ids) != len(set(task_ids)):
        raise PmsError("Order export task response contains duplicate ids.")
    return records


def export_payload(
    args: argparse.Namespace,
    state: dict[str, Any],
    scope: QueryScope,
    kind: str,
) -> dict[str, Any]:
    payload = base_payload(args, state, scope, kind, args.start, args.end)
    payload["current"] = 1
    payload["size"] = EXPORT_ROW_LIMIT
    if kind == "goods":
        payload["type"] = 59
    if kind == "orders":
        payload["type"] = 58
    return payload


def sync_export(
    args: argparse.Namespace,
    state: dict[str, Any],
    scope: QueryScope,
    kind: str,
) -> int:
    validate_span(args.start, args.end, allow_long=args.allow_long)
    ensure_output(args.output, args.overwrite)
    client = PmsClient(
        resolve_token(state),
        insecure=args.insecure,
        no_proxy=args.no_proxy,
        timeout=180,
    )
    payload = export_payload(args, state, scope, kind)
    if kind == "goods" and args.count_first:
        count_body = client.post_json(COUNT_GOODS, payload)
        try:
            estimated_rows = int(count_body.get("data"))
        except (TypeError, ValueError) as exc:
            raise PmsError("Goods export count response data must be an integer.") from exc
        print(f"Estimated rows: {estimated_rows}", file=sys.stderr)
        if estimated_rows > EXPORT_ROW_LIMIT:
            raise PmsError(
                f"Estimated goods export rows ({estimated_rows}) exceed the "
                f"{EXPORT_ROW_LIMIT}-row request limit. Narrow or split the range, "
                "or use another complete-data route."
            )
    response = client.post_raw(SYNC_ENDPOINTS[kind], payload, timeout=180)
    if not response.content.startswith(b"PK"):
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        raise PmsError(
            f"Export endpoint returned a non-xlsx response (code={code or 'unknown'})."
        )
    save_validated_xlsx(response.content, args.output)
    print(
        json.dumps(
            success_payload(
                kind,
                None,
                output=str(args.output),
                bytes=len(response.content),
                payTimeStart=args.start,
                payTimeEnd=args.end,
            ),
            ensure_ascii=False,
        )
    )
    return 0


def validate_order_export_args(args: argparse.Namespace) -> None:
    validate_span(args.start, args.end, allow_long=args.allow_long)
    if args.poll_interval <= 0 or args.wait_timeout <= 0:
        raise PmsError("--poll-interval and --wait-timeout must be greater than zero.")
    if args.raw_zip and args.raw_zip.resolve() == args.output.resolve():
        raise PmsError("--raw-zip and --output must use different paths.")
    ensure_output(args.output, args.overwrite)
    if args.raw_zip:
        ensure_output(args.raw_zip, args.overwrite)


def submit_order_export(
    client: PmsClient,
    payload: dict[str, Any],
) -> Any:
    submit = client.post_json(ORDER_SUBMIT, payload, retries=0)
    submit_data = submit.get("data")
    if not isinstance(submit_data, dict):
        raise PmsError("Order export submission data must be an object.")
    line_up_number = submit_data.get("lineUpNumber")
    if line_up_number is None:
        raise PmsError("Order export submission did not return lineUpNumber.")
    print(f"Export submitted: lineUpNumber={line_up_number}", file=sys.stderr)
    return line_up_number


def wait_for_order_export(
    client: PmsClient,
    token: str,
    baseline_ids: set[str],
    poll_interval: float,
    wait_timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + wait_timeout
    task_id: str | None = None
    next_progress = 0.0
    while time.monotonic() < deadline:
        records = list_order_export_tasks(client, token)
        if task_id is None:
            task = select_new_order_task(records, baseline_ids)
            if task is not None:
                task_id = order_task_id(task)
                print(
                    f"Order export task discovered: taskId={task_id}", file=sys.stderr
                )
        if task_id is not None:
            task = next((row for row in records if order_task_id(row) == task_id), None)
            if task is not None and str(task.get("fileStatus")) == "1":
                if not task.get("url"):
                    raise PmsError(
                        f"Order export task {task_id} completed without a download URL."
                    )
                return task
        now = time.monotonic()
        if now >= next_progress:
            phase = "registration" if task_id is None else f"taskId={task_id}"
            print(f"Waiting for order export {phase}...", file=sys.stderr)
            next_progress = now + 30
        time.sleep(min(poll_interval, max(0.0, deadline - now)))
    if task_id is None:
        raise PmsError("Timed out waiting for the new order export task id.")
    raise PmsError(f"Timed out waiting for order export task {task_id} to complete.")


def extract_encrypted_workbook(zip_path: Path, password: str, unpack_dir: Path) -> Path:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            workbooks = [
                item
                for item in archive.infolist()
                if not item.is_dir() and Path(item.filename).suffix.lower() == ".xlsx"
            ]
            if len(workbooks) != 1:
                raise PmsError(
                    "The export zip must contain exactly one xlsx file; "
                    f"found {len(workbooks)}."
                )
            output = unpack_dir / "encrypted.xlsx"
            with (
                archive.open(workbooks[0], pwd=password.encode("utf-8")) as source,
                output.open("wb") as destination,
            ):
                shutil.copyfileobj(source, destination)
            return output
    except PmsError:
        raise
    except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
        raise PmsError(f"Could not decrypt the export zip: {exc}") from exc


def decrypt_workbook(source: Path, password: str, output: Path) -> None:
    try:
        import msoffcrypto
    except ImportError as exc:
        raise PmsError("Install msoffcrypto-tool to decrypt order exports.") from exc
    with tempfile.NamedTemporaryFile(
        dir=output.parent, suffix=".xlsx", delete=False
    ) as handle:
        candidate = Path(handle.name)
    try:
        with source.open("rb") as source_handle, candidate.open("wb") as output_handle:
            office = msoffcrypto.OfficeFile(source_handle)
            office.load_key(password=password)
            office.decrypt(output_handle)
        validate_xlsx(candidate)
        candidate.replace(output)
    except PmsError:
        candidate.unlink(missing_ok=True)
        raise
    except Exception as exc:
        candidate.unlink(missing_ok=True)
        raise PmsError(f"Could not decrypt the order workbook: {exc}") from exc


def order_export(
    args: argparse.Namespace, state: dict[str, Any], scope: QueryScope
) -> int:
    validate_order_export_args(args)
    token = resolve_token(state)
    client = PmsClient(
        token, insecure=args.insecure, no_proxy=args.no_proxy, timeout=180
    )
    baseline_ids = {
        order_task_id(task) for task in list_order_export_tasks(client, token)
    }
    line_up_number = submit_order_export(
        client, export_payload(args, state, scope, "orders")
    )
    task = wait_for_order_export(
        client,
        token,
        baseline_ids,
        args.poll_interval,
        args.wait_timeout,
    )
    password_body = client.post_json(
        TASK_PASSWORD, {"id": task.get("id"), "token": token}
    )
    password_data = password_body.get("data")
    password = (
        password_data.get("password") if isinstance(password_data, dict) else None
    )
    if not password:
        raise PmsError("Export task did not return a password.")
    with tempfile.TemporaryDirectory(prefix="pms-order-export-") as temp_dir:
        temp_path = Path(temp_dir)
        zip_path = temp_path / "export.zip"
        client.download(str(task["url"]), zip_path)
        with zip_path.open("rb") as handle:
            signature = handle.read(2)
        if signature != b"PK":
            raise PmsError("Order export download is not a password-protected zip.")
        if args.raw_zip:
            shutil.copyfile(zip_path, args.raw_zip)
        unpack_dir = temp_path / "unpacked"
        unpack_dir.mkdir()
        source = extract_encrypted_workbook(zip_path, str(password), unpack_dir)
        decrypt_workbook(source, str(password), args.output)
    print(
        json.dumps(
            success_payload(
                "orders",
                None,
                output=str(args.output),
                bytes=args.output.stat().st_size,
                task_id=task.get("id"),
                lineUpNumber=line_up_number,
                payTimeStart=args.start,
                payTimeEnd=args.end,
            ),
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export PMS promotion-profit data")
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("summary", "goods", "orders"):
        child = sub.add_parser(kind)
        add_export_specific(child, kind)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        apply_time_defaults(args)
        state = load_runtime_state(args)
        scope = resolve_scope(args, state)
        if args.kind != "summary":
            resolve_operator_filter(args, state, scope)
        if args.kind == "orders":
            return order_export(args, state, scope)
        return sync_export(args, state, scope, args.kind)
    except (PmsError, requests.RequestException) as exc:
        print_error(exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
