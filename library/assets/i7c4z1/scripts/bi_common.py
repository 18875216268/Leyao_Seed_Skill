"""BI 登录凭证适配、HTTP 传输和通用 CLI 工具。

凭证完全来自同目录 login_bi；本模块不保存、不修改账号或 token。
传输层只按调用方指定的 method、path 和 JSON body 发请求并转换 HTTP 错误，
不解释 BI 业务响应、不解析字段，也不承载业务工作流。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_TIMEOUT = 90
LOGIN_HINT = "Run scripts/bi_login.py to sign in (default opens the WeCom QR dialog)."


class BiError(RuntimeError):
    """A user-actionable BI/API error with a machine-readable ``code``."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def configure_stdio() -> None:
    """强制 stdin/stdout/stderr 为 UTF-8，保证中文输出跨进程管道不乱码。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def get_credential(
    *,
    force_relogin: bool = False,
    interactive: bool = True,
    validate_remote: bool = True,
) -> dict[str, Any]:
    """从同目录 login_bi 动态取完整凭证（token/headers/biBase/user）。

    绝不弹窗：``interactive=False`` 且凭证不可用时抛 ``BiError``，由调用方引导先登录。
    """
    from bi_login import get_credential as _gc, BiError as _LoginError

    try:
        return _gc(
            force_relogin=force_relogin,
            interactive=interactive,
            validate_remote=validate_remote,
        )
    except _LoginError as exc:
        code = str(getattr(exc, "code", "") or "AUTH_REQUIRED")
        raise BiError(code, f"{LOGIN_HINT} ({code})", retryable=False) from exc


class BiClient:
    """Thin BI transport built on a login_bi credential.

    ``headers`` 取自凭证（已含 uIdToken Cookie + BI 固定头），本类不再注入任何鉴权字段。
    """

    def __init__(
        self,
        credential: dict[str, Any],
        *,
        insecure: bool = False,
        no_proxy: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        base = str(credential.get("biBase") or "").rstrip("/")
        if not base:
            raise BiError("CONFIG_ERROR", "凭证缺少 biBase（登录模块未返回主机地址）")
        self.base = base
        self.headers = dict(credential.get("headers") or {})
        self.cred = credential
        self.verify = not insecure
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = not no_proxy

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        retries: int = 2,
    ) -> dict[str, Any]:
        url = str(path) if str(path).startswith("http") else f"{self.base}{path}"
        kwargs: dict[str, Any] = {
            "headers": self.headers,
            "verify": self.verify,
            "timeout": self.timeout,
        }
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                if method == "GET":
                    resp = self.session.get(url, **kwargs)
                else:
                    kwargs["json"] = payload if payload is not None else {}
                    resp = self.session.post(url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if isinstance(exc, (requests.Timeout, requests.ConnectionError)) and attempt < retries:
                    time.sleep(min(15, 2 ** attempt * 2))
                    continue
                raise self._wrap(exc) from exc
            return self._parse(resp)
        raise self._wrap(last_exc)

    def _parse(self, resp: requests.Response) -> dict[str, Any]:
        if resp.status_code == 429:
            raise BiError("RATE_LIMITED", "BI 限流(429)，请稍后重试", retryable=True)
        if resp.status_code == 401:
            raise BiError("AUTH_EXPIRED", "BI 返回 401，登录过期，需重新企微扫码", retryable=True)
        if resp.status_code == 403:
            raise BiError("AUTH_FORBIDDEN", "BI 返回 403，无权限，换有权账号", retryable=False)
        if resp.status_code >= 400:
            code, msg = self._bi_error(resp)
            retryable = code in ("40002", "14001", "502", "503", "504")
            raise BiError(code, msg, retryable=retryable)
        ct = (resp.headers.get("content-type") or "").lower()
        if "json" in ct:
            try:
                return resp.json()
            except ValueError:
                return {"_text": resp.text[:4000]}
        return {"_text": resp.text[:4000]}

    @staticmethod
    def _bi_error(resp: requests.Response) -> tuple[str, str]:
        """提取 BI 业务错误码：兼容顶层 error_code 与信封内 error.status 两种形态。

        实测 BI 失败响应为 {"result":"fail","error":{"status":14001,"message":"..."}}，
        只取顶层 error_code 会把真实码吞掉变成笼统的「业务错误」，导致无法按码恢复。
        """
        try:
            body = resp.json()
        except ValueError:
            return "HTTP_ERROR", f"HTTP {resp.status_code}: {resp.text[:300]}"
        if not isinstance(body, dict):
            return "HTTP_ERROR", f"HTTP {resp.status_code}: {str(body)[:200]}"
        inner = body.get("error") if isinstance(body.get("error"), dict) else {}
        code = body.get("error_code") or inner.get("status") or "HTTP_ERROR"
        message = (
            body.get("error_message")
            or inner.get("message")
            or body.get("message")
            or "业务错误"
        )
        return str(code), str(message)

    @staticmethod
    def _wrap(exc: Exception) -> BiError:
        if isinstance(exc, requests.Timeout):
            return BiError("TIMEOUT", f"请求超时：{exc}", retryable=True)
        if isinstance(exc, requests.SSLError):
            return BiError("TLS_ERROR", f"证书校验失败：{exc}", retryable=False)
        if isinstance(exc, requests.ProxyError):
            return BiError("PROXY_ERROR", f"代理错误：{exc}", retryable=False)
        return BiError("NETWORK_ERROR", f"网络错误：{exc}", retryable=True)


def error_payload(exc: BaseException) -> dict[str, Any]:
    """结构化错误 + 脱敏（token/账号/密码绝不出现在输出里）。"""
    message = re.sub(
        r"(?i)\b(token|uidtoken|account|password|auth_code)=([^\s&,]+)",
        r"\1=[redacted]",
        str(exc),
    )
    message = re.sub(r"(?i)(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", message)
    code = getattr(exc, "code", None) if isinstance(exc, BiError) else None
    if not code:
        if isinstance(exc, requests.exceptions.ProxyError):
            code = "PROXY_ERROR"
        elif isinstance(exc, requests.exceptions.Timeout):
            code = "TIMEOUT"
        elif isinstance(exc, requests.exceptions.SSLError):
            code = "TLS_ERROR"
        elif "401" in message:
            code = "AUTH_EXPIRED"
        elif "1017" in message:
            code = "SSO_KICKED"
        elif "40002" in message:
            code = "QUERY_TIMEOUT"
        elif "5001" in message:
            code = "BAD_REQUEST_SHAPE"
        else:
            code = "CLIENT_ERROR"
    actions = {
        "AUTH_EXPIRED": LOGIN_HINT,
        "SSO_KICKED": "单点登录被其他设备顶掉(1017)，重新企微扫码即可",
        "QUERY_TIMEOUT": "查询超时(40002)：降维 / 加日期过滤 / 缩小 limit",
        "BAD_REQUEST_SHAPE": "请求体键名错误(5001)：读错误体缺失路径反推正确键名",
        "RATE_LIMITED": "稍后重试",
        "AUTH_FORBIDDEN": "换有权账号",
        "PROXY_ERROR": "重试同上命令加 --no-proxy；勿改系统代理设置",
        "TLS_ERROR": "修复本地 CA 配置；仅受控环境用 --insecure",
        "TIMEOUT": "检查网络/服务状态后重试",
        "NETWORK_ERROR": "检查网络后重试",
    }
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "action": actions.get(code, "检查命令参数与依赖"),
        },
    }
