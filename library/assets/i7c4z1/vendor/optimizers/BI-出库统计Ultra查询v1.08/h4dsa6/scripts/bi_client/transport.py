"""Restricted direct HTTPS transport for BI business APIs (Cookie 鉴权).

本板不实现登录：凭证由父 skill 登录器（scripts/login_bi.py）提供并注入；
因此这里只有 BI 业务端点白名单（元数据 / 取数 / 导出链），没有任何企微/登录端点。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from .errors import BiError

try:
    import requests
    from requests import Response
except ImportError as exc:  # pragma: no cover - exercised in a subprocess
    requests = None  # type: ignore[assignment]
    Response = Any  # type: ignore[misc,assignment]
    _REQUESTS_IMPORT_ERROR: ImportError | None = exc
else:
    _REQUESTS_IMPORT_ERROR = None


def require_requests() -> None:
    if requests is not None:
        return
    raise BiError(
        "DEPENDENCY_MISSING",
        "缺少 requests，请先执行 python -m pip install -r requirements.txt。",
    ) from _REQUESTS_IMPORT_ERROR


_BI_EXACT_GET_PATHS = frozenset({"/api/validate-token"})
_TASK_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# 观远业务 id 为 base62 风格 24 位（如 v37695c…/oe30aa7b…/a063f278…），并非纯 hex
_SELECTOR_ID_PATTERN = re.compile(r"^[0-9A-Za-z]{24}$")


class DirectTransport:
    def __init__(self, profile: dict[str, Any], credentials: dict[str, Any] | None = None) -> None:
        require_requests()
        self.profile = profile
        self.bi_base = str(profile["biBase"])
        timeout_cfg = profile.get("timeouts") or {}
        self.timeout = (
            float(timeout_cfg.get("connectSeconds") or 10),
            float(timeout_cfg.get("readSeconds") or 130),
        )
        self.verify: bool | str = profile.get("caBundle") or True
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        # 会话头合并策略：板块租户头兜底（profile），凭证会话头优先（真实登录会话，含
        # x-dom-id 等；Cookie 键排除——由上方 cookies 统一管理）。
        self.credential_headers: dict[str, str] = {}
        if credentials:
            self.install_credentials(credentials)

    def install_credentials(self, credentials: dict[str, Any]) -> None:
        host = urlparse(self.bi_base).hostname or ""
        token = str(credentials.get("token") or "")
        token_sig = str(credentials.get("tokenSig") or "")
        if token:
            self.session.cookies.set("uIdToken", token, domain=host, path="/")
        if token_sig:
            self.session.cookies.set("uIdToken.sig", token_sig, domain=host, path="/")
        raw = credentials.get("headers") if isinstance(credentials.get("headers"), dict) else {}
        self.credential_headers = {
            str(k): str(v) for k, v in raw.items() if str(k).lower() != "cookie"
        }

    def close(self) -> None:
        self.session.close()

    def cookie_value(self, name: str) -> str:
        host = urlparse(self.bi_base).hostname or ""
        matches = [
            cookie.value
            for cookie in self.session.cookies
            if cookie.name == name and (not cookie.domain or host.endswith(cookie.domain.lstrip(".")))
        ]
        return matches[-1] if matches else ""

    def bi_get(self, path: str, **kwargs: Any) -> Response:
        allowed = path in _BI_EXACT_GET_PATHS or path == f"/api/page/{self.profile['pageId']}"
        if not allowed:
            raise BiError("ENDPOINT_NOT_ALLOWED", "BI GET 端点不在白名单中。")
        return self._request("GET", self.bi_base, path, bi=True, **kwargs)

    def bi_post(self, path: str, **kwargs: Any) -> Response:
        allowed = path == f"/api/card/{self.profile['cardId']}/data"
        if not allowed:
            raise BiError("ENDPOINT_NOT_ALLOWED", "BI POST 端点不在白名单中。")
        return self._request("POST", self.bi_base, path, bi=True, **kwargs)

    # ---- 导出链端点（UI 采集同款三步链，路径在本方法内构造，taskId 经 UUID 校验） ----

    def export_submit(self, payload: dict[str, Any]) -> Response:
        """提交导出任务：POST /api/write/file/{cardId}?typeOp=EXCEL，请求体 = 取数请求体。"""
        return self._request(
            "POST", self.bi_base, f"/api/write/file/{self.profile['cardId']}",
            params={"typeOp": "EXCEL"}, json=payload, bi=True,
        )

    def task_status(self, task_id: str) -> Response:
        """轮询导出任务状态：GET /api/task/{taskId}。"""
        if not _TASK_ID_PATTERN.match(task_id):
            raise BiError("EXPORT_BAD_TASK", "taskId 格式非法。")
        return self._request("GET", self.bi_base, f"/api/task/{task_id}", bi=True)

    def export_download(self, task_id: str, body: dict[str, Any]) -> Response:
        """下载导出文件：POST /api/export/file/common/{taskId}，响应为 xlsx 二进制流。"""
        if not _TASK_ID_PATTERN.match(task_id):
            raise BiError("EXPORT_BAD_TASK", "taskId 格式非法。")
        return self._request(
            "POST", self.bi_base, f"/api/export/file/common/{task_id}",
            json=body, bi=True, stream=True, timeout=(10, 300),
        )

    def export_task_history(self, limit: int = 20) -> Response:
        """导出中心任务列表：GET /api/task-offline/guandata/history（找回 taskId 用）。"""
        return self._request(
            "GET", self.bi_base, "/api/task-offline/guandata/history",
            params={"offset": 0, "limit": max(1, limit), "exportAsyncResourceType": "CARD"},
            bi=True,
        )

    def selector_candidates(self, selector_id: str) -> Response:
        """下拉筛选器候选值：POST /api/selector/{selectorCardId}/data（空体全量返回）。"""
        if not _SELECTOR_ID_PATTERN.match(selector_id):
            raise BiError("ENDPOINT_NOT_ALLOWED", "筛选器 id 格式非法。")
        return self._request(
            "POST", self.bi_base, f"/api/selector/{selector_id}/data", json={}, bi=True,
        )

    def tree_candidates(self, selector_id: str, payload: dict[str, Any] | None = None) -> Response:
        """树筛选器候选：POST /api/treeSelector/{id}/data。

        空体返回会被截断（count=1000, exceedLimit=true）；带条件按需返回完整子树——
        实测有效形态：{"search": "<关键字>"} 或
        {"filters": [{"name": "...", "filterType": "IN", "filterValue": [...]}]}。
        """
        if not _SELECTOR_ID_PATTERN.match(selector_id):
            raise BiError("ENDPOINT_NOT_ALLOWED", "筛选器 id 格式非法。")
        return self._request(
            "POST", self.bi_base, f"/api/treeSelector/{selector_id}/data",
            json=payload or {}, bi=True, timeout=(10, 300),
        )

    def _request(
        self,
        method: str,
        base: str,
        path: str,
        *,
        bi: bool,
        allow_redirects: bool = False,
        permit_redirect_response: bool = False,
        timeout: tuple[float, float] | None = None,
        **kwargs: Any,
    ) -> Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if bi:
            headers.update({str(k): str(v) for k, v in (self.profile.get("headers") or {}).items()})
            headers.update(self.credential_headers)  # 凭证会话头优先
            headers.setdefault("Origin", self.bi_base)
            headers.setdefault("Referer", f"{self.bi_base}/")
        url = f"{base}{path}"
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=timeout or self.timeout,
                verify=self.verify,
                allow_redirects=allow_redirects,
                **kwargs,
            )
        except requests.exceptions.SSLError as exc:
            raise BiError("TLS_VALIDATION_FAILED", "HTTPS 证书验证失败，请检查公司 CA 配置。") from exc
        except requests.exceptions.Timeout as exc:
            raise BiError("UPSTREAM_TIMEOUT", "BI 或企微请求超时。", retryable=True) from exc
        except requests.exceptions.ConnectionError as exc:
            raise BiError(
                "BI_NETWORK_UNREACHABLE",
                "无法连接 BI 或企微，请确认公司内网、VPN 和 DNS。",
                retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise BiError("NETWORK_ERROR", "BI 网络请求失败。", retryable=True) from exc

        expected_host = urlparse(base).hostname
        final_host = urlparse(response.url).hostname
        if final_host != expected_host or any(
            urlparse(item.url).hostname != expected_host for item in response.history
        ):
            raise BiError("REDIRECT_NOT_ALLOWED", "请求离开了固定的官方主机。")
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location") or ""
            redirect_host = urlparse(urljoin(response.url, location)).hostname
            if redirect_host != expected_host:
                raise BiError("REDIRECT_NOT_ALLOWED", "官方接口返回了未允许的重定向。")
            if not permit_redirect_response:
                if bi:
                    raise BiError("AUTH_EXPIRED", "BI 登录已过期，请重新扫码登录。")
                raise BiError("REDIRECT_NOT_ALLOWED", "官方接口返回了未允许的重定向。")

        if response.status_code == 401:
            raise BiError("AUTH_EXPIRED", "BI 登录已过期，请重新扫码登录。")
        if response.status_code == 403:
            raise BiError("AUTH_FORBIDDEN", "当前 BI 账号无权执行该请求。")
        if response.status_code == 429:
            raise BiError("RATE_LIMITED", "BI 请求过于频繁。", retryable=True)
        if response.status_code in (502, 503, 504):
            raise BiError(
                "UPSTREAM_UNAVAILABLE",
                f"BI 上游暂时不可用（HTTP {response.status_code}）。",
                retryable=True,
            )
        if response.status_code >= 400:
            raise BiError(
                "HTTP_ERROR",
                f"BI 请求失败（HTTP {response.status_code}）。",
                details={"status": response.status_code},
            )
        return response

    @staticmethod
    def json(response: Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise BiError("RESPONSE_INVALID_JSON", "BI 返回了无效 JSON。") from exc
        if not isinstance(data, dict):
            raise BiError("RESPONSE_SCHEMA_CHANGED", "BI 返回结构不是 JSON 对象。")
        return data
