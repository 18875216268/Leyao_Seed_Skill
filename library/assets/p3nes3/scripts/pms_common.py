"""Shared transport, credential lookup, and CLI helpers for the Pms_智能取数_login skill."""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

# 以下三个常量是当前集团客户端的已知请求头经验值（APP_VERSION / MODULE /
# USER_AGENT）；集团包更新导致失效时，以随包文档/抓包为准调整。
APP_VERSION = "14.30.1"
MODULE = "promo_profit_monitor"
DEFAULT_TIMEOUT = 90
USER_AGENT = "pms-cxml/1.08"
LOGIN_HINT = "Run scripts/pms_login.py to sign in (default opens the WeCom QR dialog)."


def configured_base(host_var: str, fallback: str) -> str:
    """按执行器 hostname 变量名取请求基址：sync_config.json 的 host_endpoints 优先，
    fallback 仅作登录链路兜底（集团换域名只改配置，不改代码）。"""
    try:
        cfg_file = Path(__file__).resolve().parent.parent / "sync_config.json"
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        value = str((cfg.get("host_endpoints") or {}).get(host_var) or "").strip()
        if value:
            return value
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return fallback


PMS_BASE = configured_base("pmsHost", "https://pms.ysbang.cn")
ORIGIN = PMS_BASE


class PmsError(RuntimeError):
    """A user-actionable PMS/API error."""


def configure_stdio() -> None:
    """强制 stdin/stdout/stderr 为 UTF-8，保证中文输出跨进程管道不乱码。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def stored_token() -> str:
    """从自有登录组件的凭证仓库取最近登录账号的 token。

    只做本地检查（不弹窗、不发网络请求）；仓库为空或凭证不可用时报
    LOGIN_HINT，由调用方引导用户先运行 pms_login.py 登录。
    """
    from pms_login import verify_credential  # 惰性导入自有登录组件
    result = verify_credential(validate_remote=False)
    if result.get("authenticated") and result.get("token"):
        return str(result["token"])
    raise PmsError(LOGIN_HINT)


def unix_seconds() -> str:
    return str(int(time.time()))

def trace_id() -> str:
    return uuid.uuid4().hex


class PmsClient:
    def __init__(
        self,
        token: str,
        *,
        insecure: bool = False,
        no_proxy: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
        origin: str | None = None,
    ):
        self.token = token
        self.verify = not insecure
        self.timeout = timeout
        # origin 跟随目标 host（datacenter 接口的 origin 与主站不同）；
        # 未指定时用配置解析出的 PMS 主站。
        self.origin = (origin or ORIGIN).rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = not no_proxy

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "appversion": APP_VERSION,
            "platform": "web",
            "module": MODULE,
            "origin": self.origin,
            "referer": f"{self.origin}/",
            "token": self.token,
            "timestamp": unix_seconds(),
            "traceid": trace_id(),
            "user-agent": USER_AGENT,
            "content-type": "application/json",
        }

    def _send(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        content_type: str,
        timeout: int | None = None,
    ) -> requests.Response:
        """单次请求；content_type 决定用 JSON 还是表单请求体（与集团执行器一致）。"""
        headers = self._headers()
        kwargs: dict[str, Any] = {
            "headers": headers,
            "verify": self.verify,
            "timeout": timeout or self.timeout,
        }
        if "x-www-form-urlencoded" in content_type:
            headers["content-type"] = content_type
            kwargs["data"] = payload
        else:
            kwargs["json"] = payload
        response = self.session.post(url, **kwargs)
        if response.status_code >= 400:
            raise PmsError(f"HTTP {response.status_code} from PMS endpoint")
        return response

    def post_json(
        self, url: str, payload: dict[str, Any], *, retries: int = 2
    ) -> dict[str, Any]:
        for attempt in range(retries + 1):
            response = self._send(url, payload, content_type="application/json")
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
        response = self._send(
            url, payload, content_type="application/x-www-form-urlencoded"
        )
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

    def post_export(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        content_type: str,
        destination: Path,
    ) -> dict[str, Any]:
        """导出类接口落盘，覆盖集团两种导出形态（见 vendor 随包说明）：

        · stream —— 响应体本身是 Excel 文件流（失败时退化成 application/json 业务错误）
        · url    —— 响应是 JSON，下载链接在 data 里，需要二次下载

        返回 {"mode", "saved_to", "bytes", "body"}。
        """
        response = self._send(url, payload, content_type=content_type)
        target = Path(destination).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)

        if "json" in (response.headers.get("content-type") or "").lower():
            try:
                body = response.json()
            except ValueError as exc:
                raise PmsError(f"Expected JSON from {url}.") from exc
            if not isinstance(body, dict):
                raise PmsError(f"Expected a JSON object from {url}.")
            link = export_download_url(body)
            if not link:
                code = str(body.get("code", ""))
                if code == "401":
                    raise PmsError(f"PMS token expired or invalid (401). {LOGIN_HINT}")
                if code and code != "40001":
                    raise PmsError(
                        f"PMS error {code}: {body.get('message') or body.get('msg')}"
                    )
                raise PmsError(
                    "Export response carries no downloadable HTTPS URL. "
                    "If this is an async export, poll the task list and download when ready."
                )
            return {
                "mode": "url",
                "saved_to": str(target),
                "bytes": self.download(link, target),
                "body": body,
            }

        written = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
                    written += len(chunk)
        return {"mode": "stream", "saved_to": str(target), "bytes": written, "body": None}


_DOWNLOAD_URL_KEYS = ("url", "fileUrl", "downloadUrl", "filePath")


def export_download_url(body: dict[str, Any]) -> str:
    """从导出响应里挑出可下载的 HTTPS 链接；没有则返回空串。"""
    candidates: list[str] = []
    data = body.get("data")
    if isinstance(data, str):
        candidates.append(data)
    elif isinstance(data, dict):
        candidates.extend(
            str(data[key]) for key in _DOWNLOAD_URL_KEYS if isinstance(data.get(key), str)
        )
    for item in candidates:
        if item.lower().startswith("https://"):
            return item
    return ""

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

