#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BI 登录 · 企微扫码登录模块（单文件自包含；依赖 requests，PyQt5 可选）。

一个文件 = 一个既能独立运行、也能被任何应用 import 的登录组件。
模块只做一件事：企微扫码 → 拿到「完整用户信息」与「完整请求凭证」。

登录所需的全部连接配置（BI 地址、企微应用参数、固定请求头）已硬编码为
文件内的模块常量（BI_BASE / WECOM_BASE / OAUTH / BI_HEADERS），
**不读取任何外部配置文件，不含任何固定账号信息**，可直接整体拷走、独立运行。

=========================== 一、直接运行 ===========================
    python login_bi.py                 # 默认：弹出扫码窗，重新登录获取新凭证
    python login_bi.py --status        # 只验证已有凭证是否有效（绝不弹窗）
    python login_bi.py --reuse         # 有效则复用，失效才弹窗
    python login_bi.py --no-ui         # 无界面模式（服务器 / 守护进程）
    python login_bi.py --no-remote     # 跳过远端 validate-token 校验

    成功时把「完整凭证 JSON」打印到 stdout，可直接被上游程序解析；
    `--status` 默认只输出元信息（不含令牌明文），加 `--show-token` 才输出完整凭证。
    退出码：0 成功 / 1 业务错误 / 2 未分类错误。

=========================== 二、被其它应用调用 ===========================
    from login_bi import (
        relogin,             # 功能一：弹出扫码窗重新登录，返回全新凭证
        verify_credential,   # 功能二：只验证已有凭证是否有效（绝不弹窗）
        get_credential,      # 组合：可指定强制重登 / 复用
        is_authenticated,    # 快捷：只想知道"现在能不能用"
    )

    # 功能一：每次都重新扫码，拿全新凭证
    cred = relogin()

    # 功能二：只验证，绝不弹窗
    result = verify_credential()
    if result["authenticated"]:
        token = result["token"]

    # 快捷：只要一个布尔值
    if not is_authenticated():
        cred = relogin()

    # 组合
    cred = get_credential(force_relogin=True)   # 等价 relogin()
    cred = get_credential(force_relogin=False)  # 有效则复用，失效才弹窗

=========================== 三、返回的「完整凭证」 ===========================
    {
      "ok": true, "authenticated": true,
      "source": "local" | "qr",        // 复用还是新扫码
      "reused": bool,
      "token": "...",                  // uIdToken 明文
      "tokenSig": "...",               // uIdToken.sig 明文
      "cookies": {...},                // 可直接塞进任何 HTTP 客户端
      "cookieHeader": "uIdToken=...; uIdToken.sig=...",
      "headers": {...},                // BI 固定请求头 + Cookie，开箱即用
      "exp": 1789465698, "expireAt": "2026-09-15 13:48:16",
      "validatedAt": ..., "createdAt": ...,
      "biBase": "...", "wecomBase": "...",
      "credentialPath": "...",         // 本地明文凭证文件
    }

=========================== 四、UI 状态机（严格按需求） ===========================
    loading        → 转圈动画              + "加载中......"
    qr_ready       → 二维码                + "请企微扫码（mm:ss）......"（实时倒计时）
    scanned        → 二维码 + 黑色蒙版 + 绿色勾 + "请确认登录！"
    authenticating → 保持蒙版绿勾          + "登录中......"
    failed         → 静止加载圈（不转）    + "请点击重试！"（点击容器回到"加载中"）

    扫码后取消 / 超时 / 一直不确认 → 自动回到 loading 重新加载二维码。
    凭证获取失败（可恢复类）→ 同样回到 loading；不可恢复 → 显示"请点击重试！"。

=========================== 五、凭证仓库（唯一落盘形态） ===========================
    所有登录入口（relogin / login_and_store / CLI / GUI）落盘到同一处，
    按账号一文件，单账号 / 多账号场景零区分。

    存储：%LOCALAPPDATA%\\bi-operations-query\\accounts\\<loginId>.json
         **一个用户一个文件**；明文 JSON 直接落盘（无加解密开销）；原子写入。

    身份识别：扫码后 token 里没有账号字段，模块会自动调 `/api/user/profile`
              查出「刚才是谁扫的」（loginId / uId / 姓名 / 邮箱 / 角色）。
    规则：同一用户再次扫码 → 更新密钥；换个人扫码 → 新增一个账号。
          每个用户一个文件，彼此隔离、互不覆盖。

    from login_bi import (
        login_and_store,       # 扫码登录 + 按真实身份入库（有则更新，无则新增）
        fetch_user_info,       # 查某份凭证属于哪个用户
        list_accounts,         # 列出所有账号摘要（含真实姓名，不含 token）
        get_account,           # 取某个账号的完整凭证
        get_all_accounts,      # 取所有账号的完整凭证
        verify_account,        # 验证单个账号
        verify_all_accounts,   # 批量验证所有账号
        add_account,           # 新增账号凭证（已存在则拒绝）
        update_account,        # 更新账号凭证 / 类别
        delete_account,        # 删除账号凭证
        set_account_category,  # 设置账号类别
    )

    cred = login_and_store()                        # 扫码，按扫码人身份自动入库
    cred = login_and_store(category="财务")          # 顺便指定类别
    cred = login_and_store("zhangsan")              # 强制指定账号名（一般不传）

    cred["user"]  # {"uId":..., "loginId":"LY000123", "name":"张三", "email":..., ...}

安全说明：
    · 网络侧：使用内置的主机白名单与端点白名单，不引入任何本地服务、
      代理或第三方端点。
    · 存储侧：**全部明文存储，不做任何加解密**（统一落盘为
      accounts/<loginId>.json，按账号一文件，无其它形态），
      依赖 %LOCALAPPDATA% 目录的用户级 ACL 保护，请勿外传或纳入整机备份。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlparse

import requests
from requests import Response

# --------------------------------------------------------------------------- #
# 错误类型
# --------------------------------------------------------------------------- #
class BiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        output: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            output["details"] = self.details
        return output


# --------------------------------------------------------------------------- #
# 命令行输出工具
# --------------------------------------------------------------------------- #
def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


def emit(value: dict[str, Any]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")


# --------------------------------------------------------------------------- #
# 连接配置（仅登录必需项，硬编码；不含任何账号信息）
# --------------------------------------------------------------------------- #
BI_BASE = "https://bi.leyopharm.com"
WECOM_BASE = "https://login.work.weixin.qq.com"
WECOM_VERSION = "2.3.2"

# 企微 OAuth 扫码参数（corpId / agentId 是公司企微应用标识，扫码登录必需）
OAUTH: dict[str, Any] = {
    "corpId": "wx8c9fab123dec4357",
    "agentId": "1000305",
    "redirectUri": "https://bi.leyopharm.com/?provider=wechatwork&agentId=1000305&corpId=wx8c9fab123dec4357&domain=guanbi",
    "domain": "guanbi",
    "state": "loginState",
    "pollIntervalSeconds": 1.5,
    "qrTimeoutSeconds": 300,
}

# BI 接口固定请求头（不含任何账号信息；请求者身份完全由 Cookie 决定）
BI_HEADERS: dict[str, str] = {
    "x-dom-id": "Z3VhbmJp",
    "raw-backend-response": "TRUE",
    "cache-control": "no-cache",
    "Accept": "application/json",
}

CONNECT_TIMEOUT = 10.0  # 秒
READ_TIMEOUT = 130.0    # 秒


# --------------------------------------------------------------------------- #
# HTTP 传输层（固定官方主机 + 端点白名单）
# --------------------------------------------------------------------------- #
_WECOM_GET_PATHS = frozenset(
    {
        "/wwlogin/sso/login",
        "/wwlogin/sso/qrcode",
    }
)
_WECOM_POST_PATHS = frozenset(
    {
        "/wwlogin/monoApi/sso/login/getWebQrCodeStatus",
    }
)
_BI_EXACT_GET_PATHS = frozenset({"/api/validate-token", "/api/user/profile"})


class Transport:
    """登录专用 HTTP 传输层：固定官方主机、端点白名单、不走系统代理。"""

    def __init__(self, credentials: dict[str, Any] | None = None) -> None:
        self.bi_base = BI_BASE
        self.wecom_base = WECOM_BASE
        self.host = urlparse(self.bi_base).hostname or ""
        self.timeout = (CONNECT_TIMEOUT, READ_TIMEOUT)
        ca_bundle = os.environ.get("BI_CA_BUNDLE", "").strip()
        if ca_bundle:
            ca_path = Path(ca_bundle).expanduser().resolve()
            if not ca_path.is_file():
                raise BiError(
                    "TLS_CA_NOT_FOUND",
                    "BI_CA_BUNDLE 指向的证书文件不存在。",
                    details={"path": str(ca_path)},
                )
            self.verify: bool | str = str(ca_path)
        else:
            self.verify = True
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        if credentials:
            self._install_credentials(credentials)

    def _install_credentials(self, credentials: dict[str, Any]) -> None:
        """把 token / tokenSig 预置为 BI 会话 Cookie（复用本地凭证时使用）。"""
        token = str(credentials.get("token") or "")
        token_sig = str(credentials.get("tokenSig") or "")
        if token:
            self.session.cookies.set("uIdToken", token, domain=self.host, path="/")
        if token_sig:
            self.session.cookies.set("uIdToken.sig", token_sig, domain=self.host, path="/")

    def close(self) -> None:
        self.session.close()

    def cookie_value(self, name: str) -> str:
        matches = [
            cookie.value or ""
            for cookie in self.session.cookies
            if cookie.name == name
            and (not cookie.domain or self.host.endswith(cookie.domain.lstrip(".")))
        ]
        return matches[-1] if matches else ""

    def wecom_get(self, path: str, **kwargs: Any) -> Response:
        if path not in _WECOM_GET_PATHS:
            raise BiError("ENDPOINT_NOT_ALLOWED", "企微请求端点不在白名单中。")
        return self._request("GET", self.wecom_base, path, bi=False, **kwargs)

    def wecom_post(
        self,
        path: str,
        *,
        params: Any = None,
        json_body: Any,
        headers: dict[str, str],
    ) -> Response:
        if path not in _WECOM_POST_PATHS:
            raise BiError("ENDPOINT_NOT_ALLOWED", "企微请求端点不在白名单中。")
        return self._request(
            "POST", self.wecom_base, path, params=params, json=json_body, headers=headers, bi=False
        )

    def bi_get(self, path: str, **kwargs: Any) -> Response:
        allowed = path in _BI_EXACT_GET_PATHS
        if not allowed:
            raise BiError("ENDPOINT_NOT_ALLOWED", "BI GET 端点不在白名单中。")
        return self._request("GET", self.bi_base, path, bi=True, **kwargs)

    def bi_callback(self, auth_code: str) -> Response:
        """访问 BI 的企微回调首页，以 code/state 建立 BI 会话。"""
        return self._request(
            "GET",
            self.bi_base,
            "/",
            bi=True,
            params={
                "provider": "wechatwork",
                "agentId": OAUTH["agentId"],
                "corpId": OAUTH["corpId"],
                "domain": OAUTH.get("domain") or "guanbi",
                "code": auth_code,
                "state": OAUTH.get("state") or "loginState",
            },
            allow_redirects=True,
        )

    def _request(
        self,
        method: str,
        base: str,
        path: str,
        *,
        bi: bool,
        **kwargs: Any,
    ) -> Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if bi:
            headers.update(BI_HEADERS)
            headers.setdefault("Origin", self.bi_base)
            headers.setdefault("Referer", f"{self.bi_base}/")
        url = f"{base}{path}"
        allow_redirects = bool(kwargs.pop("allow_redirects", False))
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
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


# --------------------------------------------------------------------------- #
# 本地凭证存储（单账号 + 多账号仓库，全部明文 JSON）
# --------------------------------------------------------------------------- #
def _data_home() -> Path:
    override = os.environ.get("BI_OPERATIONS_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "bi-operations-query"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share") / "bi-operations-query"


def decode_jwt_payload(token: str) -> dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
        return {}


def decode_jwt_exp(token: str) -> int:
    try:
        return int(decode_jwt_payload(token).get("exp") or 0)
    except (ValueError, TypeError):
        return 0





def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """原子 JSON 写入：临时文件 + fsync + `os.replace` 原子替换，权限 600。

    单账号与多账号场景共用，保证磁盘上的文件永远是完整写入的。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def local_expired(credentials: dict[str, Any], skew_seconds: int = 60) -> bool:
    exp = int(credentials.get("exp") or 0)
    return bool(exp and exp <= int(time.time()) + skew_seconds)


__all__ = [
    "get_credential",
    "verify_credential",
    "relogin",
    "is_authenticated",
    "create_login_dialog",
    "run_login_dialog",
    "LoginFlow",
    "build_credential",
    # 凭证仓库（唯一落盘形态）
    "AccountStore",
    "account_store",
    "login_and_store",
    "fetch_user_info",
    "USER_PROFILE_PATH",
    "list_accounts",
    "get_account",
    "get_all_accounts",
    "verify_account",
    "verify_all_accounts",
    "add_account",
    "update_account",
    "delete_account",
    "set_account_category",
    "DEFAULT_CATEGORY",
    "BiError",
    "STAGE_LOADING",
    "STAGE_QR_READY",
    "STAGE_SCANNED",
    "STAGE_AUTHENTICATING",
    "STAGE_FAILED",
    "STATUS_LOADING",
    "STATUS_SCAN",
    "STATUS_CONFIRM",
    "STATUS_LOGGING_IN",
    "STATUS_RETRY",
]

# --------------------------------------------------------------------------- #
# 阶段与文案
# --------------------------------------------------------------------------- #
STAGE_LOADING = "loading"
STAGE_QR_READY = "qr_ready"
STAGE_SCANNED = "scanned"
STAGE_AUTHENTICATING = "authenticating"
STAGE_FAILED = "failed"

STATUS_LOADING = "加载中......"
STATUS_SCAN = "请企微扫码......"
STATUS_CONFIRM = "请确认登录！"
STATUS_LOGGING_IN = "登录中......"
STATUS_RETRY = "请点击重试！"

# 二维码就绪时的倒计时文案模板，{0} 为剩余时间（mm:ss）
STATUS_SCAN_COUNTDOWN = "请企微扫码（{0}）......"

# 界面配色（十六进制，避免与 Qt 产生耦合）
_FONT_FAMILY = "Microsoft YaHei UI"
_HEX_MUTED = "#86909C"
_HEX_GREEN = "#2ECC71"
_HEX_BORDER = "#E5E9F2"

# 内置程序图标（SVG，蓝色圆角磁贴 + 二维码样式的白色方块）。
# 用于原生弹窗左上角的图标，无需任何外部文件。
_APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#1878FF"/>
  <g fill="#FFFFFF">
    <rect x="14" y="14" width="15" height="15" rx="3"/>
    <rect x="35" y="14" width="15" height="15" rx="3"/>
    <rect x="14" y="35" width="15" height="15" rx="3"/>
    <rect x="35" y="35" width="7" height="7"/>
    <rect x="46" y="35" width="7" height="7"/>
    <rect x="35" y="46" width="7" height="7"/>
    <rect x="46" y="46" width="7" height="7"/>
  </g>
</svg>"""

# 内置刷新/重试图标（SVG，阿里图标库 refresh 双箭头环形图标）。
# 二维码容器加载/失败态居中显示，并支持旋转。
_REFRESH_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <path d="M137.143 322.488L271.798 512h-88.94c0 181.78 147.361 329.143 329.142 329.143 85.84 0 166.355-32.954 227.102-90.899l5.637-5.505 64.65 64.65C731.055 887.723 625.02 932.57 512 932.57c-229.952 0-416.801-184.549-420.515-413.616L91.429 512H2.488l134.655-189.512zM512 182.858c-85.84 0-166.355 32.953-227.102 90.898l-5.637 5.505-64.65-64.65C292.945 136.277 398.98 91.43 512 91.43c229.952 0 416.801 184.549 420.515 413.616l0.056 6.955h88.941L886.857 731.291 752.202 512h88.94c0-181.78-147.361-329.143-329.142-329.143z" fill="#86909C"/>
</svg>"""

# 这些错误说明「本地凭证不可用」，才允许弹窗重新登录。
# 网络、限流、权限、参数类错误一律不触发登录界面。
_RECOVERABLE_CODES = frozenset(
    {"AUTH_REQUIRED", "AUTH_EXPIRED", "CREDENTIALS_INVALID"}
)

# 扫码成功但换取/校验凭证失败时，可以靠「重新加载二维码」自救的错误。
_RELOADABLE_AFTER_SCAN = frozenset(
    {"AUTH_EXPIRED", "CREDENTIALS_INVALID", "AUTH_TOKEN_MISSING", "REDIRECT_NOT_ALLOWED"}
)

# 当前登录用户的身份信息端点（返回 loginId / uId / name / email / role 等）。
# 扫码登录后调它，才能知道「刚才是谁扫的」——token 本身不含账号字段。
USER_PROFILE_PATH = "/api/user/profile"

# /api/user/profile 返回体很大（含 features、字体、导出限制等），
# 只保留身份相关字段存进凭证，避免仓库膨胀。
_USER_INFO_FIELDS = ("uId", "loginId", "name", "email", "domId", "domainName", "role")

_QR_PATTERN = re.compile(r"wwlogin/sso/qrcode\?key=([a-f0-9]{16})")

_RELOAD = object()  # _poll 的返回值哨兵：表示需要重新加载二维码


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _extract_qr_key(html: str) -> str:
    match = _QR_PATTERN.search(html or "")
    if not match:
        raise BiError("QR_CREATE_FAILED", "企微登录页中没有找到二维码 key。")
    return match.group(1)


def _interruptible_sleep(seconds: float, is_aborted: Callable[[], bool]) -> bool:
    """可中断的 sleep；被打断返回 False。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while True:
        if is_aborted():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.1, remaining))


def _format_timestamp(value: int) -> str:
    if not value:
        return ""
    return dt.datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")


def _validate_token_remote(transport: Transport) -> None:
    data = transport.json(transport.bi_get("/api/validate-token"))
    if data.get("response") != "success":
        raise BiError("AUTH_EXPIRED", "BI 凭证验证失败，请重新扫码登录。")


def fetch_user_info(credential: dict[str, Any]) -> dict[str, Any]:
    """查询某份凭证**实际属于哪个用户**（BI `/api/user/profile`）。

    扫码后拿到的 token 里没有账号字段，只有调这个接口才能知道「刚才是谁扫的」。
    按真实客户端的方式调用：POST 空 JSON 体，并用 `token` 请求头携带 JWT。

    参数：
        credential  登录得到的完整凭证（需含 token）

    返回：身份信息字典，例如
        {"uId": "xf727d90…", "loginId": "LY000123", "name": "张三",
         "email": "…@company.com", "domId": "guanbi", "role": [...]}

    抛出：BiError —— 凭证失效（AUTH_EXPIRED）或接口返回异常（USER_INFO_FAILED）
    """
    transport = Transport(credential)
    try:
        data = transport.json(transport.bi_get(USER_PROFILE_PATH))
    finally:
        transport.close()
    # 带 raw-backend-response 时是信封 {"result":"ok","response":{...}}，拆一层；
    # 否则直接就是用户对象。两种形态都兼容。
    if isinstance(data, dict) and isinstance(data.get("response"), dict):
        data = data["response"]
    if not isinstance(data, dict) or not data:
        raise BiError("USER_INFO_FAILED", "未能获取用户信息，请重新登录。")
    return data


def _slim_user(user_info: dict[str, Any]) -> dict[str, Any]:
    """从 /api/user/profile 的完整响应里只挑身份字段（避免把 features/字体等存进来）。"""
    slim: dict[str, Any] = {}
    for key in _USER_INFO_FIELDS:
        value = user_info.get(key)
        if value in (None, "", [], {}):
            continue
        slim[key] = value
    return slim


def _identity_of(user_info: dict[str, Any]) -> str:
    """取账号标识：优先 loginId（可读、与 BI 账号一致），退化到 uId / id。"""
    for key in ("loginId", "uId", "id"):
        value = str(user_info.get(key) or "").strip()
        if value:
            return value
    raise BiError("ACCOUNT_INVALID", "用户信息中没有可用的账号标识。")


def _check_credential(
    credentials: dict[str, Any] | None,
    *,
    validate_remote: bool,
) -> tuple[str | None, BiError | None]:
    """检查一份凭证是否可用；返回 (reason, error)，两者皆 None 表示有效。

    单账号（verify_credential）与多账号（verify_account）共用同一套判定。
    """
    if not credentials:
        return "missing", BiError("AUTH_REQUIRED", "尚未登录 BI，请先执行登录。")
    if local_expired(credentials):
        return "expired", BiError("AUTH_EXPIRED", "本地 BI 凭证已过期，请重新扫码登录。")
    if validate_remote:
        transport = Transport(credentials)
        try:
            _validate_token_remote(transport)
        except BiError as exc:
            return "not_validated", exc
        finally:
            transport.close()
    return None, None


# --------------------------------------------------------------------------- #
# 凭证打包
# --------------------------------------------------------------------------- #
def build_credential(
    credentials: dict[str, Any],
    *,
    source: str,
    reused: bool,
) -> dict[str, Any]:
    """把本地或新获取的凭证打包成「完整凭证」，任何应用都能直接消费。

    参数：
        credentials  凭证本体，至少含 token；可选 tokenSig / exp / createdAt / validatedAt
        source       "local"（复用本地凭证）或 "qr"（本次新扫码）
        reused       本次结果是否为复用旧凭证

    返回：完整凭证字典（字段见模块文档「三、返回的完整凭证」）。

    说明：一般不必直接调用，verify_credential() / relogin() 已返回同样结构。
    """
    token = str(credentials.get("token") or "")
    token_sig = str(credentials.get("tokenSig") or "")
    cookies: dict[str, str] = {"uIdToken": token}
    if token_sig:
        cookies["uIdToken.sig"] = token_sig

    headers = dict(BI_HEADERS)
    headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
    headers.setdefault("Origin", BI_BASE)
    headers.setdefault("Referer", f"{BI_BASE}/")

    exp = int(credentials.get("exp") or decode_jwt_exp(token) or 0)
    return {
        "ok": True,
        "authenticated": True,
        "source": source,
        "reused": reused,
        "token": token,
        "tokenSig": token_sig,
        "cookies": cookies,
        "cookieHeader": headers["Cookie"],
        "headers": headers,
        "exp": exp,
        "expireAt": _format_timestamp(exp),
        "createdAt": int(credentials.get("createdAt") or 0),
        "validatedAt": int(credentials.get("validatedAt") or 0),
        "biBase": BI_BASE,
        "wecomBase": WECOM_BASE,
        "credentialPath": str(credentials.get("credentialPath") or ""),
    }


# --------------------------------------------------------------------------- #
# 登录流程内核（不依赖 Qt，可在无界面环境运行）
# --------------------------------------------------------------------------- #
class LoginFlow:
    """企微扫码登录的完整流程，通过回调把阶段抛给上层（UI 或 CLI）。

    本类不依赖 Qt，可在服务器 / 守护进程 / 任何无界面环境中运行。

    参数（构造）：
        max_auto_reload  二维码失效时最多自动重载几次（默认 3）；超出则抛 QR_EXPIRED

    回调（构造，均可省略，省略后为空实现）：
        on_stage(stage, message)  UI 阶段切换
        on_qr(png_bytes)          二维码图片字节
        on_notice(text)           非阻塞提示（例如"二维码已失效，正在重新加载"）
        is_aborted()              返回 True 立即中止（用于取消 / 关窗）

    方法：
        run()  执行一次完整登录，返回完整凭证字典；失败抛 BiError。
               内部会在二维码失效 / 被取消 / 超时时自动重新加载。

    属性：
        last_error  最近一次错误（BiError 或 None）

    典型用法（后台线程 + 回调）：
        flow = LoginFlow(on_stage=..., on_qr=..., is_aborted=...)
        cred = flow.run()   # 阻塞直到拿到凭证或被中止
    """

    def __init__(
        self,
        *,
        on_stage: Callable[[str, str], None] | None = None,
        on_qr: Callable[[bytes], None] | None = None,
        on_notice: Callable[[str], None] | None = None,
        is_aborted: Callable[[], bool] | None = None,
        max_auto_reload: int = 3,
    ) -> None:
        self.on_stage = on_stage or (lambda *_: None)
        self.on_qr = on_qr or (lambda _: None)
        self.on_notice = on_notice or (lambda _: None)
        self.is_aborted = is_aborted or (lambda: False)
        self.max_auto_reload = max(0, int(max_auto_reload))
        self.last_error: BiError | None = None
        self._open_data_sid: str | None = None

    # --------------------------- 主循环 --------------------------- #
    def run(self) -> dict[str, Any]:
        """执行一次登录；内部会在二维码失效/被取消时自动重新加载。"""
        transport = Transport()
        reloads = 0
        try:
            while True:
                if self.is_aborted():
                    raise self._abort_error()

                # ---- 第一步：加载二维码 ---- #
                self.on_stage(STAGE_LOADING, STATUS_LOADING)
                try:
                    qr_key, png, sso_url = self._create_qr(transport)
                except BiError as exc:
                    raise self._fail(exc)

                self.on_qr(png)
                self.on_stage(STAGE_QR_READY, STATUS_SCAN)

                # ---- 第二步：轮询扫码与确认 ---- #
                outcome = self._poll(transport, qr_key, sso_url)
                if outcome is _RELOAD:
                    reloads += 1
                    if reloads > self.max_auto_reload:
                        raise self._fail(
                            BiError("QR_EXPIRED", "二维码多次失效，请点击重试。")
                        )
                    self.on_notice("二维码已失效，正在重新加载")
                    continue
                if self.is_aborted():
                    raise self._abort_error()

                # ---- 第三步：换取并校验凭证 ---- #
                self.on_stage(STAGE_AUTHENTICATING, STATUS_LOGGING_IN)
                try:
                    return self._finish(transport, str(outcome))
                except BiError as exc:
                    reloads += 1
                    if exc.code not in _RELOADABLE_AFTER_SCAN:
                        raise self._fail(exc)
                    if reloads > self.max_auto_reload:
                        raise self._fail(exc)
                    self.on_notice(f"{exc.message} 正在重新加载二维码")
        finally:
            transport.close()

    # --------------------------- 各环节 --------------------------- #
    def _create_qr(self, transport: Transport) -> tuple[str, bytes, str]:
        oauth = OAUTH
        response = transport.wecom_get(
            "/wwlogin/sso/login",
            params={
                "login_type": "CorpApp",
                "appid": oauth["corpId"],
                "agentid": oauth["agentId"],
                "redirect_uri": oauth["redirectUri"],
                "state": oauth.get("state") or "loginState",
                "redirect_type": "top",
                "panel_size": "small",
                "lang": "zh",
                "version": WECOM_VERSION,
            },
        )
        qr_key = _extract_qr_key(response.text)
        image = transport.wecom_get("/wwlogin/sso/qrcode", params={"key": qr_key})
        png = image.content
        if not png:
            raise BiError("QR_CREATE_FAILED", "二维码图片为空。")
        return qr_key, png, response.url

    def _poll(self, transport: Transport, qr_key: str, sso_url: str) -> Any:
        """轮询扫码状态（wwlogin monoApi）。

        返回 auth_code（已确认）、_RELOAD（需重新加载二维码），或抛出异常。
        """
        oauth = OAUTH
        interval = float(oauth.get("pollIntervalSeconds") or 1.5)
        deadline = time.monotonic() + float(oauth.get("qrTimeoutSeconds") or 300)
        # 企微状态机要求首拍 lastStatus 为固定初始值，服务端据此推进状态
        last_status = "QRCODE_SCAN_NEVER"
        confirmed = False

        while time.monotonic() < deadline:
            if self.is_aborted():
                raise self._abort_error()
            poll_headers = {
                "content-type": "application/json",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "referer": sso_url,
                "x-wecom-client": f"ww-sso-login:{int(time.time() * 1000)}:master",
            }
            try:
                response = transport.wecom_post(
                    "/wwlogin/monoApi/sso/login/getWebQrCodeStatus",
                    params={
                        "lang": "zh_CN",
                        "ajax": "1",
                        "f": "json",
                        "random": str(time.time()),
                    },
                    json_body={
                        "webKey": qr_key,
                        "lastStatus": last_status,
                        "openDataSid": self._open_data_sid,
                    },
                    headers=poll_headers,
                )
                body = transport.json(response)
            except BiError as exc:
                if not exc.retryable:
                    raise
                self._sleep_or_abort(interval)
                continue

            payload = body.get("data")
            data = payload if isinstance(payload, dict) else {}
            status = str(body.get("status") or data.get("status") or "")
            auth_code = str(body.get("auth_code") or data.get("auth_code") or "")
            open_data_sid = body.get("openDataSid") or data.get("openDataSid")
            if open_data_sid:
                self._open_data_sid = open_data_sid

            # 已扫码、等待手机端确认
            if status == "QRCODE_SCAN_ING" or (
                status == "QRCODE_SCAN_SUCC" and not auth_code
            ):
                if not confirmed:
                    confirmed = True
                    self.on_stage(STAGE_SCANNED, STATUS_CONFIRM)
                self._sleep_or_abort(interval)
                continue

            # 已确认
            if status == "QRCODE_SCAN_SUCC" and auth_code:
                if not confirmed:
                    self.on_stage(STAGE_SCANNED, STATUS_CONFIRM)
                return auth_code

            # 取消 / 失效 / 超时 → 回到第一步重新加载二维码
            if status in {"QRCODE_SCAN_ERR", "QRCODE_SCAN_TIMEOUT", "QRCODE_SCAN_CANCEL"}:
                return _RELOAD

            if status:
                last_status = status
            self._sleep_or_abort(interval)

        return _RELOAD  # 轮询整体超时 → 重新加载

    def _finish(
        self,
        transport: Transport,
        auth_code: str,
    ) -> dict[str, Any]:
        # BI 成功链：先带 code/state 访问回调首页，服务器会通过 Set-Cookie 直接下发
        # uIdToken 与 uIdToken.sig。无需再调用 /api/user/token（本账号对该端点 1004 无权限）。
        transport.bi_callback(auth_code)
        token = transport.cookie_value("uIdToken")
        token_sig = transport.cookie_value("uIdToken.sig")
        if not token:
            raise BiError("AUTH_TOKEN_MISSING", "扫码成功，但 BI 回调未返回 Token。")
        _validate_token_remote(transport)

        return build_credential(
            {
                "biBase": BI_BASE,
                "token": token,
                "tokenSig": token_sig,
                "exp": decode_jwt_exp(token),
                "createdAt": int(time.time()),
                "validatedAt": int(time.time()),
            },
            source="qr",
            reused=False,
        )

    # --------------------------- 错误收口 --------------------------- #
    def _sleep_or_abort(self, seconds: float) -> bool:
        if not _interruptible_sleep(seconds, self.is_aborted):
            raise self._abort_error()
        return True

    def _abort_error(self) -> BiError:
        exc = BiError("LOGIN_CANCELLED", "登录已取消。")
        self.last_error = exc
        return exc

    def _fail(self, exc: BiError) -> BiError:
        """进入失败态（静止加载圈 + 请点击重试），并记录错误。"""
        self.last_error = exc
        self.on_stage(STAGE_FAILED, STATUS_RETRY)
        return exc


# --------------------------------------------------------------------------- #
# 功能二：读取本地凭证并验证是否有效（绝不弹窗）
# --------------------------------------------------------------------------- #
def verify_credential(
    *,
    validate_remote: bool = True,
    raise_on_invalid: bool = False,
) -> dict[str, Any]:
    """验证**最近一次登录账号**的凭证（与多账号仓库同一份落盘）。**绝不会弹窗。**

    参数：
        validate_remote  True  → 额外请求 /api/validate-token 做远端校验（默认）
                        False → 只做本地检查（过期时间），不发网络请求
        raise_on_invalid True  → 凭证无效时直接抛出 BiError
                        False → 不抛异常，返回 ok=False 的结果字典（默认）

    返回：
        有效 → 完整凭证（authenticated=True，source="local"）
        无效 → {"ok": False, "authenticated": False, "reason": ..., "error": {...}}

    reason 取值：missing（本地没有任何账号）/ expired（已过期）
                / invalid（凭证损坏或字段缺失）/ not_validated（远端校验未通过）
    """
    store = account_store()

    def _invalid(reason: str, exc: BiError) -> dict[str, Any]:
        if raise_on_invalid:
            raise exc
        return {
            "ok": False,
            "authenticated": False,
            "source": "local",
            "reused": True,
            "reason": reason,
            "error": exc.to_dict(),
            "credentialPath": str(store.dir),
        }

    latest_account = store.latest_account()
    if latest_account is None:
        return _invalid("missing", BiError("AUTH_REQUIRED", "尚未登录 BI，请先执行登录。"))

    try:
        credentials = store.get(latest_account)
    except BiError as exc:
        return _invalid("invalid", exc)

    reason, error = _check_credential(credentials, validate_remote=validate_remote)
    if reason or credentials is None:
        return _invalid(
            reason or "invalid",
            error or BiError("AUTH_REQUIRED", "本地凭证不可用。"),
        )

    credentials["validatedAt"] = int(time.time())
    store.upsert(latest_account, credentials)
    result = dict(credentials)
    result["source"] = "local"
    result["reused"] = True
    return result


def is_authenticated(validate_remote: bool = True) -> bool:
    """快捷判断：本地凭证当前是否可用（等价于 verify_credential()["authenticated"]）。

    参数：
        validate_remote  True  → 额外做远端 /api/validate-token 校验（默认）
                        False → 只做本地检查，不发网络请求

    返回：True 可用 / False 不可用（绝不会弹窗、绝不会抛异常）。
    """
    return bool(verify_credential(validate_remote=validate_remote)["authenticated"])


# --------------------------------------------------------------------------- #
# 功能一：重新登录获取凭证 + 组合入口
# --------------------------------------------------------------------------- #
def get_credential(
    *,
    force_relogin: bool = True,
    validate_remote: bool = True,
    interactive: bool = True,
    parent: Any = None,
) -> dict[str, Any]:
    """拿到一份可用凭证。

    默认（force_relogin=True）与 relogin() 等价：每次都弹窗重新扫码，
    保证拿到全新凭证。需要"优先复用本地凭证、失效才弹窗"时传 force_relogin=False。

    参数：
        force_relogin   True  → 总是弹窗重新扫码（默认）
                        False → 优先复用本地有效凭证，失效才弹窗
        validate_remote True  → 复用时额外请求 /api/validate-token（默认）
                        False → 复用时只做本地检查，不发网络请求
        interactive     True  → 允许弹窗扫码（默认）
                        False → 无界面模式；凭证不可用时不弹窗，直接抛 BiError
        parent          PyQt5 父窗口（可选）

    返回：完整凭证字典（同 build_credential()）。

    抛出：
        BiError —— 无界面模式下凭证不可用，或遇到网络/权限类不可恢复错误。
    """
    if not force_relogin:
        result = verify_credential(validate_remote=validate_remote)
        if result["authenticated"]:
            return result
        error = result.get("error") or {}
        code = str(error.get("code") or "")
        message = str(error.get("message") or "本地凭证不可用。")
        # 只有「凭证本身不可用」才允许弹窗；网络/限流/权限错误直接抛出。
        if not interactive or code not in _RECOVERABLE_CODES:
            raise BiError(code or "AUTH_REQUIRED", message)

    if not interactive:
        raise BiError(
            "AUTH_REQUIRED", "本地没有可用凭证，且当前为无界面模式，无法扫码登录。"
        )
    # 重新扫码必须走 login_and_store：取扫码人身份并落入 accounts/<loginId>.json。
    # 直接调用 run_login_dialog 只会拿到内存凭证，不会落盘（--status 会一直失败）。
    return login_and_store(parent=parent)


def relogin(*, parent: Any = None) -> dict[str, Any]:
    """功能一：无论本地是否已有凭证，都弹出扫码窗重新登录，返回全新凭证。

    登录成功后按扫码人身份落入统一凭证仓库（accounts/<loginId>.json）。

    参数：
        parent   PyQt5 父窗口（可选）

    返回：完整凭证字典（source="qr"、reused=False）。

    抛出：BiError —— 用户取消（LOGIN_CANCELLED）、二维码多次失效或网络类错误。
    """
    return login_and_store(parent=parent)


# --------------------------------------------------------------------------- #
# 凭证仓库（唯一落盘形态：按账号名存取完整凭证）
# --------------------------------------------------------------------------- #
DEFAULT_CATEGORY = "默认"
_ACCOUNTS_DIRNAME = "accounts"


class AccountStore:
    """按「账号名」存取多份**完整凭证**的仓库，**一个账号一个文件**。

    - 存储位置：`%LOCALAPPDATA%\\bi-operations-query\\accounts\\<loginId>.json`
    - 存储格式：**明文 JSON**（不做加解密，省去 DPAPI 开销）
    - 写入：临时文件 + `os.replace` 原子替换，权限 600
    - 账号名：登录后由 `/api/user/profile` 查到的 `loginId`，也可调用时显式指定

    一个账号一个文件的好处：
        · 故障隔离——单个文件损坏只影响该账号，不牵连其它账号
        · 更新只重写该文件，不搬运其它账号的数据
        · 可单独备份 / 查看 / 删除某个账号
        · 这是本模块唯一的落盘形态，单账号/多账号场景零区分

    同账号再次登录 → 更新；新账号 → 新增，永不静默覆盖其它账号。

    ⚠️ 明文存储意味着拿到该目录文件即可直接使用这些 BI 凭证。
       目录位于当前用户 %LOCALAPPDATA% 下，受 NTFS 用户级 ACL 保护，
       但请自行确认磁盘 / 备份策略符合你们的安全要求。
    """

    VERSION = 1

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = Path(directory) if directory else (_data_home() / _ACCOUNTS_DIRNAME)

    # ---------------- 底层：路径与单文件读写 ---------------- #
    @staticmethod
    def _safe_name(account: str) -> str:
        """把账号名转成安全文件名。

        规则：
            1. 名字本身只含字母数字与 `-` `_`（如 `LY000123`）→ **原样作为文件名**，
               保持 `LY000123.json` 这种直观命名
            2. 含非法字符（`/` `\\` `.` `@` 等）→ 先替换成 `_` 杜绝路径穿越，
               再追加 8 位哈希后缀，避免不同名字归一化后撞文件
               （如 "a/b" 与 "ab" 都曾会变成 "ab"）
            3. 因此邮箱形式的 loginId（user@company.com）也能安全落盘
        """
        name = str(account).strip()
        if not name:
            raise BiError("ACCOUNT_INVALID", "账号名不能为空。")
        safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in name)
        if safe == name:
            return name
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        return f"{safe}-{digest}"

    def path_for(self, account: str) -> Path:
        """某个账号的凭证文件路径。"""
        return self.dir / f"{self._safe_name(account)}.json"

    def _read_entry(self, account: str) -> dict[str, Any] | None:
        path = self.path_for(account)
        if not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BiError(
                "CREDENTIALS_INVALID",
                f"账号凭证文件损坏，请删除后重新登录：{account}",
                details={"path": str(path)},
            ) from exc
        if not isinstance(entry, dict):
            raise BiError(
                "CREDENTIALS_INVALID",
                f"账号凭证文件格式无效：{account}",
                details={"path": str(path)},
            )
        return entry

    @staticmethod
    def _credential_of(entry: dict[str, Any]) -> dict[str, Any]:
        """取出记录里的完整凭证（明文存储，无加解密开销）。"""
        credential = entry.get("credential")
        if not isinstance(credential, dict) or not credential:
            raise BiError("CREDENTIALS_INVALID", "账号凭证缺失或格式无效，请重新登录。")
        return credential

    @staticmethod
    def _summary_of(entry: dict[str, Any]) -> dict[str, Any]:
        """把一条记录转成摘要（**不含 token**，可安全打印）。"""
        return {
            "account": entry.get("account") or "",
            "displayName": entry.get("displayName") or "",
            "loginId": entry.get("loginId") or "",
            "uId": entry.get("uId") or "",
            "category": entry.get("category") or DEFAULT_CATEGORY,
            "biBase": entry.get("biBase") or "",
            "exp": int(entry.get("exp") or 0),
            "expireAt": _format_timestamp(int(entry.get("exp") or 0)),
            "createdAt": int(entry.get("createdAt") or 0),
            "updatedAt": int(entry.get("updatedAt") or 0),
            "validatedAt": int(entry.get("validatedAt") or 0),
            "path": entry.get("path") or "",
        }

    # ---------------- 对外：读 ---------------- #
    def _iter_entries(self) -> Iterator[dict[str, Any]]:
        """扫描目录一次，逐个产出账号记录。

        损坏 / 非法的文件直接跳过，不牵连其它账号。
        `names()` / `summary()` / `all_credentials()` 共用它，
        避免「先扫一遍拿名字、再按名字逐个重读」的重复 I/O。
        """
        if not self.dir.is_dir():
            return
        for path in sorted(self.dir.glob("*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(entry, dict) and str(entry.get("account") or "").strip():
                yield entry

    def names(self) -> list[str]:
        """所有已存账号名（按名称排序）。"""
        return sorted(str(entry["account"]) for entry in self._iter_entries())

    def get(self, account: str) -> dict[str, Any] | None:
        """取某个账号的完整凭证；不存在返回 None。"""
        entry = self._read_entry(account)
        return self._credential_of(entry) if entry else None

    def all_credentials(self) -> dict[str, dict[str, Any]]:
        """取所有账号的完整凭证：{账号名: 完整凭证}。"""
        return {
            str(entry["account"]): entry["credential"]
            for entry in self._iter_entries()
            if isinstance(entry.get("credential"), dict)
        }

    def summary(self) -> list[dict[str, Any]]:
        """所有账号的摘要（**不含 token**，可安全打印/展示）。"""
        return [self._summary_of(entry) for entry in self._iter_entries()]

    # ---------------- 对外：写 ---------------- #
    def upsert(
        self,
        account: str,
        credential: dict[str, Any],
        *,
        category: str | None = None,
    ) -> dict[str, Any]:
        """新增或更新一个账号的完整凭证（同账号自动覆盖更新）。"""
        name = str(account).strip()
        if not name:
            raise BiError("ACCOUNT_INVALID", "账号名不能为空。")
        if not str(credential.get("token") or ""):
            raise BiError("AUTH_TOKEN_MISSING", "不能保存空的 BI Token。")

        path = self.path_for(name)
        existing = self._read_entry(name)
        now = int(time.time())
        user = credential.get("user") or {}
        credential["credentialPath"] = str(path)
        entry = {
            "version": self.VERSION,
            "account": name,
            "displayName": str(user.get("name") or ""),
            "loginId": str(user.get("loginId") or ""),
            "uId": str(user.get("uId") or ""),
            "category": category or (existing or {}).get("category") or DEFAULT_CATEGORY,
            "biBase": str(credential.get("biBase") or ""),
            "exp": int(credential.get("exp") or 0),
            "createdAt": int((existing or {}).get("createdAt") or now),
            "updatedAt": now,
            "seq": max((int(e.get("seq") or 0) for e in self._iter_entries()), default=0) + 1,
            "validatedAt": int(credential.get("validatedAt") or now),
            "path": str(path),
            "credential": credential,
        }
        _atomic_write_json(path, entry)
        return credential

    def delete(self, account: str) -> bool:
        """删除一个账号的凭证文件；返回是否真的删掉了。"""
        path = self.path_for(account)
        if not path.is_file():
            return False
        try:
            path.unlink()
        except OSError as exc:
            raise BiError(
                "CREDENTIALS_INVALID",
                f"账号凭证文件删除失败：{account}",
                details={"path": str(path)},
            ) from exc
        return True

    def set_category(self, account: str, category: str) -> dict[str, Any]:
        """设置账号类别；账号不存在时抛 ACCOUNT_NOT_FOUND。"""
        entry = self._read_entry(account)
        if entry is None:
            raise BiError("ACCOUNT_NOT_FOUND", f"账号不存在：{account}")
        entry["category"] = str(category)
        entry["updatedAt"] = int(time.time())
        _atomic_write_json(self.path_for(account), entry)
        return self._summary_of(entry)

    def exists(self, account: str) -> bool:
        return self.path_for(account).is_file()

    def latest_account(self) -> str | None:
        """最近一次写入凭证的账号名（按 seq 序号判定）；仓库为空返回 None。"""
        best: str | None = None
        best_key = (-1, -1)
        for entry in self._iter_entries():
            key = (int(entry.get("seq") or 0), int(entry.get("updatedAt") or 0))
            if key > best_key:
                best, best_key = str(entry["account"]), key
        return best


def account_store() -> AccountStore:
    """返回凭证仓库实例（唯一落盘形态：每账号一个文件，目录 accounts/）。"""
    return AccountStore()


def login_and_store(
    account: str | None = None,
    *,
    category: str | None = None,
    parent: Any = None,
) -> dict[str, Any]:
    """调起扫码登录，按**扫码人真实身份**把完整凭证存入仓库。

    登录后会自动调 `/api/user/profile` 查出「刚才是谁扫的」（token 里没有账号字段），
    再以该用户的 `loginId` 作为账号键：

        同一用户再次扫码 → 更新密钥；换个人扫码 → 新增一个账号。
        每个用户一个文件，彼此隔离、互不覆盖。

    参数：
        account   强制指定账号名（一般不用）；None → 用查到的 loginId
        category  类别标签；None → 已存账号保留原类别，新账号用 DEFAULT_CATEGORY
        parent    PyQt5 父窗口（可选）

    返回：本次登录拿到的完整凭证（额外含 `user` 字段：该用户身份信息）。

    抛出：BiError —— 登录失败，或查不到用户信息（USER_INFO_FAILED）
    """
    credential = run_login_dialog(parent=parent)
    user_info = fetch_user_info(credential)
    credential["user"] = _slim_user(user_info)
    name = account or _identity_of(user_info)
    account_store().upsert(name, credential, category=category)
    return credential


def list_accounts() -> list[dict[str, Any]]:
    """列出所有已存账号的摘要（**不含 token**，可安全打印）。"""
    return account_store().summary()


def get_account(account: str) -> dict[str, Any] | None:
    """取某个账号的完整凭证。

    参数：
        account  账号名

    返回：完整凭证字典；账号不存在返回 None。
    """
    return account_store().get(account)


def get_all_accounts() -> dict[str, dict[str, Any]]:
    """取所有账号的完整凭证。

    返回：{账号名: 完整凭证}；仓库为空时返回空字典。
    """
    return account_store().all_credentials()


def verify_account(
    account: str, *, validate_remote: bool = True
) -> dict[str, Any]:
    """验证单个账号的凭证是否有效（**绝不弹窗**）。

    参数：
        account          账号名
        validate_remote  True  → 额外请求 /api/validate-token（默认）
                        False → 只做本地检查（过期时间），不发网络请求

    返回：{"account": 账号名, "authenticated": bool, "reason": str|None, "error": dict|None}

    副作用：验证通过时会自动刷新该账号的 validatedAt。
    reason 取值同 verify_credential()：missing / expired / not_validated。
    """
    store = account_store()
    credential = store.get(account)
    reason, error = _check_credential(credential, validate_remote=validate_remote)
    result: dict[str, Any] = {
        "account": str(account),
        "authenticated": reason is None,
        "reason": reason,
        "error": error.to_dict() if error else None,
    }
    if reason is None and credential is not None:
        credential["validatedAt"] = int(time.time())
        store.upsert(str(account), credential)
    return result


def verify_all_accounts(
    *, validate_remote: bool = True
) -> list[dict[str, Any]]:
    """批量验证所有账号的凭证。

    参数：
        validate_remote  True  → 每个账号都做远端校验（默认）
                        False → 只做本地检查，不发网络请求

    返回：每个账号一条结果，结构同 verify_account()；仓库为空时返回空列表。
    """
    return [
        verify_account(name, validate_remote=validate_remote)
        for name in account_store().names()
    ]


def add_account(
    account: str, credential: dict[str, Any], *, category: str | None = None
) -> dict[str, Any]:
    """新增一个账号凭证。

    参数：
        account     账号名（不能为空）
        credential  完整凭证，至少含 token
        category    类别；None → DEFAULT_CATEGORY

    返回：存入的完整凭证。

    抛出：BiError —— ACCOUNT_EXISTS（账号已存在，想覆盖请用 update_account）
                    / AUTH_TOKEN_MISSING（credential 里没有 token）
    """
    store = account_store()
    if store.exists(account):
        raise BiError("ACCOUNT_EXISTS", f"账号已存在：{account}（如需覆盖请用 update_account）")
    return store.upsert(account, credential, category=category)


def update_account(
    account: str,
    credential: dict[str, Any] | None = None,
    *,
    category: str | None = None,
) -> dict[str, Any]:
    """更新账号凭证和/或类别。

    参数：
        account     账号名
        credential  新的完整凭证；None → 不改动凭证
        category    新类别；None → 保留原类别

    传参组合：
        只传 credential → 仅换凭证，保留原类别
        只传 category   → 仅改类别，凭证不变
        都传           → 都改
        都不传         → 抛 ACCOUNT_INVALID

    返回：更新后的完整凭证。

    抛出：BiError —— ACCOUNT_NOT_FOUND（账号不存在）
                    / ACCOUNT_INVALID（两个都没传）
    """
    store = account_store()
    if not store.exists(account):
        raise BiError("ACCOUNT_NOT_FOUND", f"账号不存在：{account}")
    if credential is None and category is None:
        raise BiError("ACCOUNT_INVALID", "credential 与 category 至少要传一个。")
    current = store.get(account)
    merged = {**(current or {}), **(credential or {})}
    return store.upsert(account, merged, category=category)


def delete_account(account: str) -> bool:
    """删除一个账号的凭证。

    参数：
        account  账号名

    返回：True 表示确实删掉了；False 表示该账号本就不存在。
    """
    return account_store().delete(account)


def set_account_category(account: str, category: str) -> dict[str, Any]:
    """设置账号类别（如「财务」「仓库」「默认」）。

    参数：
        account   账号名
        category  类别字符串（自由文本，不做取值校验）

    返回：更新后的账号条目（摘要字段）。

    抛出：BiError —— ACCOUNT_NOT_FOUND（账号不存在）
    """
    return account_store().set_category(account, category)


# --------------------------------------------------------------------------- #
# PyQt5 界面（惰性加载：没装 PyQt5 也能 import 本模块并使用无界面功能）
# --------------------------------------------------------------------------- #
_ACTIVE_THREADS: set = set()
_APP: Any = None  # keep QApplication alive between create_login_dialog and use


def _track_thread(thread: Any) -> None:
    _ACTIVE_THREADS.add(thread)
    thread.finished.connect(lambda: _ACTIVE_THREADS.discard(thread))


def _svg_to_pixmap(svg: str, size: int) -> Any:
    """把内置 SVG 渲染成透明背景的 QPixmap（图标、刷新图共用）。"""
    from PyQt5.QtCore import QByteArray, QRectF
    from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap
    from PyQt5.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return QPixmap.fromImage(image)


def _load_qt() -> dict[str, Any]:
    """导入 PyQt5；缺失时给出可执行的中文提示。"""
    try:
        from PyQt5.QtCore import QPointF, QRectF, Qt, QThread, QTimer
        from PyQt5.QtCore import pyqtSignal
        from PyQt5.QtGui import (
            QColor,
            QFont,
            QIcon,
            QPainter,
            QPainterPath,
            QPen,
            QPixmap,
            QPolygonF,
        )
        from PyQt5.QtWidgets import (
            QApplication,
            QDialog,
            QLabel,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise BiError(
            "DEPENDENCY_MISSING",
            "缺少 PyQt5，请先执行 python -m pip install PyQt5。",
        ) from exc

    return {
        "Qt": Qt,
        "QThread": QThread,
        "QTimer": QTimer,
        "pyqtSignal": pyqtSignal,
        "QPointF": QPointF,
        "QRectF": QRectF,
        "QColor": QColor,
        "QFont": QFont,
        "QPainter": QPainter,
        "QPainterPath": QPainterPath,
        "QPen": QPen,
        "QPixmap": QPixmap,
        "QPolygonF": QPolygonF,
        "QIcon": QIcon,
        "QApplication": QApplication,
        "QDialog": QDialog,
        "QLabel": QLabel,
        "QVBoxLayout": QVBoxLayout,
        "QWidget": QWidget,
    }


_GUI: Any = None


def _ensure_qapp() -> dict[str, Any]:
    """确保存在 QApplication（在定义任何 pyqtSignal 类之前必须调用）。

    PyQt5 的信号注册要求 QCoreApplication.instance() 在类定义时已非 None，
    否则会打印 "QWidget: Must construct a QApplication before a QWidget" 并静默失败。
    实例同时保存在模块级，避免被 GC 回收后构造弹窗时卡死。
    """
    global _APP
    qt = _load_qt()
    QApplication = qt["QApplication"]
    existing = QApplication.instance()
    if existing is not None:
        _APP = existing
        return qt
    try:
        QApplication.setAttribute(qt["Qt"].AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(qt["Qt"].AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass
    _APP = QApplication(sys.argv[:1])
    return qt


def _gui_classes() -> Any:
    """惰性构建并缓存 LoginDialog 类，保证无 PyQt5 环境也能 import 本模块。"""
    global _GUI
    if _GUI is not None:
        return _GUI
    qt = _ensure_qapp()  # 必须在定义 pyqtSignal 类之前

    Qt = qt["Qt"]
    QWidget = qt["QWidget"]
    QDialog = qt["QDialog"]
    QThread = qt["QThread"]
    pyqtSignal = qt["pyqtSignal"]
    QTimer = qt["QTimer"]
    QPointF = qt["QPointF"]
    QRectF = qt["QRectF"]
    QColor = qt["QColor"]
    QFont = qt["QFont"]
    QPainter = qt["QPainter"]
    QPainterPath = qt["QPainterPath"]
    QPen = qt["QPen"]
    QPixmap = qt["QPixmap"]
    QPolygonF = qt["QPolygonF"]
    QIcon = qt["QIcon"]
    QApplication = qt["QApplication"]
    QVBoxLayout = qt["QVBoxLayout"]
    QLabel = qt["QLabel"]

    # ------------------------------------------------------------------ #
    def _make_app_icon() -> Any:
        """从内置 SVG 渲染程序图标（用于原生弹窗左上角）。"""
        return QIcon(_svg_to_pixmap(_APP_ICON_SVG, 48))

    # ------------------------------------------------------------------ #
    class QrCanvas(QWidget):
        """二维码容器：转圈 / 二维码 / 蒙版绿勾 / 静止加载圈，并支持点击重试。"""

        retryRequested = pyqtSignal()
        _TICK_MS = 40
        _STEP_DEG = 10
        _SPINNER_SIZE = 80

        def __init__(self, size: int = 240, parent: Any = None) -> None:
            super().__init__(parent)
            self._stage = STAGE_LOADING
            self._pixmap = QPixmap()
            self._spinner = _svg_to_pixmap(_REFRESH_ICON_SVG, self._SPINNER_SIZE)
            self._angle = 0
            self._timer = QTimer(self)
            self._timer.setInterval(self._TICK_MS)
            self._timer.timeout.connect(self._on_tick)
            self.setFixedSize(size, size)
            self.set_stage(STAGE_LOADING)

        # ---------------- 对外接口 ---------------- #
        def set_pixmap_bytes(self, data: bytes) -> None:
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                return
            self._pixmap = pixmap
            self.update()

        def set_stage(self, stage: str) -> None:
            self._stage = stage
            if stage in (STAGE_LOADING, STAGE_FAILED):
                self._pixmap = QPixmap()  # 回到加载态时清掉旧二维码
            spinning = stage == STAGE_LOADING
            if spinning and not self._timer.isActive():
                self._timer.start()
            elif not spinning and self._timer.isActive():
                self._timer.stop()
            if not spinning:
                self._angle = 0
            self.setCursor(
                Qt.PointingHandCursor if stage == STAGE_FAILED else Qt.ArrowCursor
            )
            self.update()

        # ---------------- 绘制 ---------------- #
        def _on_tick(self) -> None:
            self._angle = (self._angle + self._STEP_DEG) % 360
            self.update()

        def paintEvent(self, event: Any) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            frame = QPainterPath()
            frame.addRoundedRect(rect, 14, 14)
            painter.setClipPath(frame)
            painter.fillPath(frame, QColor(255, 255, 255))

            show_qr = not self._pixmap.isNull() and self._stage in (
                STAGE_QR_READY,
                STAGE_SCANNED,
                STAGE_AUTHENTICATING,
            )
            if show_qr:
                inner = rect.adjusted(8, 8, -8, -8)
                scaled = self._pixmap.scaled(
                    inner.size().toSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                painter.drawPixmap(
                    int(inner.x() + (inner.width() - scaled.width()) / 2),
                    int(inner.y() + (inner.height() - scaled.height()) / 2),
                    scaled,
                )
                if self._stage in (STAGE_SCANNED, STAGE_AUTHENTICATING):
                    painter.fillPath(frame, QColor(0, 0, 0, 172))  # 黑色蒙版
                    self._draw_check(painter, rect)
            else:
                self._draw_svg_spinner(painter, rect)

            painter.setClipping(False)
            painter.setPen(QPen(QColor(_HEX_BORDER), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(frame)

        def _draw_svg_spinner(self, painter: Any, rect: Any) -> None:
            """绘制居中的 SVG 刷新图标，加载态时旋转。"""
            center = rect.center()
            painter.save()
            painter.translate(center.x(), center.y())
            painter.rotate(self._angle)
            offset = -self._SPINNER_SIZE / 2
            painter.translate(offset, offset)
            painter.drawPixmap(0, 0, self._spinner)
            painter.restore()

        @staticmethod
        def _draw_check(painter: Any, rect: Any) -> None:
            center = rect.center()
            radius = min(rect.width(), rect.height()) * 0.16
            a = QPointF(center.x() - radius * 0.95, center.y() + radius * 0.05)
            b = QPointF(center.x() - radius * 0.20, center.y() + radius * 0.80)
            c = QPointF(center.x() + radius * 1.00, center.y() - radius * 0.78)
            # 先描白边，保证在任何二维码底色上都清晰
            outline = QPen(QColor(255, 255, 255, 235), radius * 0.62)
            outline.setCapStyle(Qt.RoundCap)
            outline.setJoinStyle(Qt.RoundJoin)
            painter.setPen(outline)
            painter.drawPolyline(QPolygonF([a, b, c]))
            check = QPen(QColor(_HEX_GREEN), radius * 0.36)
            check.setCapStyle(Qt.RoundCap)
            check.setJoinStyle(Qt.RoundJoin)
            painter.setPen(check)
            painter.drawPolyline(QPolygonF([a, b, c]))

        # ---------------- 交互 ---------------- #
        def mousePressEvent(self, event: Any) -> None:  # noqa: N802
            if self._stage == STAGE_FAILED and event.button() == Qt.LeftButton:
                event.accept()
                self.retryRequested.emit()
                return
            super().mousePressEvent(event)

    # ------------------------------------------------------------------ #
    class LoginWorker(QThread):
        """后台跑 LoginFlow，通过信号把阶段抛回 UI 线程。

        注意：这里没有 on_notice 桥接。二维码失效自动重载等提示由弹窗的
        倒计时与状态文字表达，无需额外信号；on_notice 是 LoginFlow 对外
        部（非 UI）调用方的公开回调，UI 路径用不到。
        """

        stageChanged = pyqtSignal(str, str)
        qrLoaded = pyqtSignal(object)
        credentialReady = pyqtSignal(object)
        loginFailed = pyqtSignal(str, str)

        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            self._aborted = False

        def abort(self) -> None:
            self._aborted = True
            self.requestInterruption()

        def _is_aborted(self) -> bool:
            return self._aborted or self.isInterruptionRequested()

        def run(self) -> None:  # noqa: D102
            flow = LoginFlow(
                on_stage=lambda stage, message: self.stageChanged.emit(stage, message),
                on_qr=lambda data: self.qrLoaded.emit(data),
                is_aborted=self._is_aborted,
            )
            try:
                credential = flow.run()
            except BiError as exc:
                if not self._is_aborted():
                    self.loginFailed.emit(exc.code, exc.message)
                return
            except Exception:  # noqa: BLE001
                if not self._is_aborted():
                    self.loginFailed.emit("INTERNAL_ERROR", "登录发生未分类错误。")
                return
            if not self._is_aborted():
                self.credentialReady.emit(credential)

    # ------------------------------------------------------------------ #
    class LoginDialog(QDialog):
        """标准 Windows 风格登录弹窗。"""

        QR_SIZE = 240
        PADDING = 24
        BOTTOM_MARGIN = 16
        WINDOW_WIDTH = QR_SIZE + 2 * PADDING

        def __init__(self, parent: Any = None) -> None:
            super().__init__(parent)
            self._worker: Any = None
            self.credential: dict[str, Any] | None = None
            self.last_error: BiError | None = None
            self._qr_deadline = 0.0
            self._countdown = QTimer(self)
            self._countdown.setInterval(1000)
            self._countdown.timeout.connect(self._on_countdown_tick)

            self.setWindowTitle("登录观远BI")
            self.setWindowFlags(Qt.Window)
            self.setModal(True)
            self.setWindowIcon(_make_app_icon())
            self._build_ui()
            self.setFixedSize(self.WINDOW_WIDTH, self.sizeHint().height())
            self._center_on_screen()
            self._start_flow()

        # ---------------- 构建界面 ---------------- #
        def _build_ui(self) -> None:
            root = QVBoxLayout(self)
            root.setContentsMargins(
                self.PADDING, self.PADDING, self.PADDING, self.BOTTOM_MARGIN
            )
            root.setSpacing(0)

            self._canvas = QrCanvas(self.QR_SIZE, self)
            self._canvas.retryRequested.connect(self._start_flow)
            root.addWidget(self._canvas, alignment=Qt.AlignCenter)

            root.addSpacing(12)

            self._detail = QLabel(STATUS_LOADING, self)
            self._detail.setAlignment(Qt.AlignCenter)
            self._detail.setWordWrap(True)
            self._detail.setFont(QFont(_FONT_FAMILY, 10))
            self._detail.setStyleSheet(f"color:{_HEX_MUTED};background:transparent;")
            root.addWidget(self._detail)

        def _center_on_screen(self) -> None:
            screen = QApplication.primaryScreen()
            if not screen:
                return
            area = screen.availableGeometry()
            frame = self.frameGeometry()
            frame.moveCenter(area.center())
            self.move(frame.topLeft())

        # ---------------- 流程控制 ---------------- #
        def _start_flow(self) -> None:
            self._stop_worker()
            self._countdown.stop()
            self._canvas.set_stage(STAGE_LOADING)
            self._detail.setText(STATUS_LOADING)

            worker = LoginWorker(self)
            worker.stageChanged.connect(self._on_stage)
            worker.qrLoaded.connect(self._on_qr)
            worker.credentialReady.connect(self._on_credential)
            worker.loginFailed.connect(self._on_failed)
            self._worker = worker
            _track_thread(worker)
            worker.start()

        def _stop_worker(self) -> None:
            self._countdown.stop()
            worker, self._worker = self._worker, None
            if worker is None:
                return
            try:
                worker.stageChanged.disconnect(self._on_stage)
                worker.qrLoaded.disconnect(self._on_qr)
                worker.credentialReady.disconnect(self._on_credential)
                worker.loginFailed.disconnect(self._on_failed)
            except (RuntimeError, TypeError):
                pass
            worker.abort()
            worker.quit()
            worker.wait(2000)

        # ---------------- 信号槽 ---------------- #
        def _on_qr(self, data: Any) -> None:
            self._canvas.set_pixmap_bytes(bytes(data))

        def _on_stage(self, stage: str, message: str) -> None:
            self._canvas.set_stage(stage)
            if stage == STAGE_QR_READY:
                timeout = float(OAUTH.get("qrTimeoutSeconds") or 300)
                self._qr_deadline = time.monotonic() + timeout
                self._countdown.start()
                self._refresh_countdown()
                return
            self._countdown.stop()
            self._detail.setText(message)

        def _on_countdown_tick(self) -> None:
            self._refresh_countdown()

        def _refresh_countdown(self) -> None:
            remaining = int(self._qr_deadline - time.monotonic())
            if remaining <= 0:
                self._countdown.stop()
                return
            mm, ss = divmod(remaining, 60)
            self._detail.setText(STATUS_SCAN_COUNTDOWN.format(f"{mm}:{ss:02d}"))

        def _on_credential(self, credential: Any) -> None:
            self.credential = dict(credential)
            self._stop_worker()
            self.accept()

        def _on_failed(self, code: str, message: str) -> None:
            self.last_error = BiError(code or "LOGIN_FAILED", message)
            self._countdown.stop()
            self._canvas.set_stage(STAGE_FAILED)
            self._detail.setText(STATUS_RETRY)

        # ---------------- 窗口行为 ---------------- #
        def closeEvent(self, event: Any) -> None:  # noqa: N802
            self._stop_worker()
            super().closeEvent(event)

        def reject(self) -> None:  # noqa: D102
            self._stop_worker()
            super().reject()

    _GUI = LoginDialog
    return _GUI


def create_login_dialog(parent: Any = None) -> Any:
    """创建（但尚未显示）登录弹窗实例，可嵌入已有 PyQt5 程序（异步用法）。

    参数：
        parent   PyQt5 父窗口（可选）

    返回：LoginDialog 实例。构造后即已在后台线程启动扫码流程（UI 不卡顿）。
          调用方自行 show()（非阻塞）或 exec_()（阻塞），并通过下列属性 / 信号取结果：
            dialog.credential  成功后填充的完整凭证字典
            dialog.last_error  失败后的 BiError
            dialog.accepted    Qt 信号：登录成功时触发，此时读 credential
            dialog.rejected    Qt 信号：用户取消 / 关窗时触发，此时读 last_error

    依赖：需要 PyQt5；未安装时抛 BiError(DEPENDENCY_MISSING)。
    """
    return _gui_classes()(parent)


def run_login_dialog(
    *,
    parent: Any = None,
) -> dict[str, Any]:
    """弹出登录窗口并等待完成（同步用法，内部跑在后台线程，UI 不卡顿）。

    参数：
        parent   PyQt5 父窗口（可选）

    返回：成功 → 完整凭证字典（source="qr"、reused=False）。
    抛出：BiError —— LOGIN_CANCELLED（用户取消）/ QR_EXPIRED（二维码多次失效）
                    / 网络、限流、权限等不可恢复错误；未装 PyQt5 时抛 DEPENDENCY_MISSING。

    已有 QApplication 时复用，没有则自动创建，可安全嵌入任何 PyQt5 程序。
    """
    dialog_cls = _gui_classes()  # 同时保证 QApplication 已存在

    dialog = dialog_cls(parent)
    result = dialog.exec_()

    if result == dialog_cls.Accepted and dialog.credential:
        return dialog.credential
    raise dialog.last_error or BiError("LOGIN_CANCELLED", "用户取消了登录。")


# --------------------------------------------------------------------------- #
# 命令行入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="BI 登录 · 企微扫码登录（默认弹出扫码窗重新登录；也可只验证已有凭证）"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="功能二：只验证已有凭证是否有效，绝不弹窗（默认只输出元信息，不含令牌明文）",
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="配合 --status：输出完整凭证（含 token / Cookie 明文），默认隐藏",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="优先复用本地有效凭证，仅在失效时才弹窗（默认总是重新扫码）",
    )
    parser.add_argument("--no-ui", action="store_true", help="无界面模式，不弹窗")
    parser.add_argument("--no-remote", action="store_true", help="跳过远端 validate-token")
    args = parser.parse_args(argv)

    configure_stdio()
    try:
        if args.status:
            result = verify_credential(validate_remote=not args.no_remote)
            print(
                "[bi-login] "
                + ("凭证有效" if result["authenticated"] else f"凭证无效：{result.get('reason')}"),
                file=sys.stderr,
            )
            if args.show_token:
                emit(result)
            else:
                user = result.get("user") if isinstance(result.get("user"), dict) else {}
                emit(
                    {
                        "ok": result.get("ok"),
                        "authenticated": result.get("authenticated"),
                        "source": result.get("source"),
                        "exp": result.get("exp"),
                        "expireAt": result.get("expireAt"),
                        "validatedAt": result.get("validatedAt"),
                        "biBase": result.get("biBase"),
                        "credentialPath": result.get("credentialPath"),
                        "user": {
                            k: user.get(k)
                            for k in ("uId", "loginId", "name", "email", "domId", "role")
                            if user.get(k) is not None
                        },
                    }
                )
                print("[bi-login] 令牌明文已隐藏；需要完整凭证时加 --show-token", file=sys.stderr)
            return 0 if result["authenticated"] else 1

        # 默认：无论本地是否已有凭证，都弹窗重新扫码获取新凭证。
        credential = get_credential(
            force_relogin=not args.reuse,
            validate_remote=not args.no_remote,
            interactive=not args.no_ui,
        )
    except BiError as exc:
        emit({"ok": False, "authenticated": False, "error": exc.to_dict()})
        return 1
    except Exception:  # noqa: BLE001
        emit(
            {
                "ok": False,
                "authenticated": False,
                "error": BiError("INTERNAL_ERROR", "BI 登录发生未分类错误。").to_dict(),
            }
        )
        return 2

    print(
        f"[bi-login] 登录成功（{credential['source']}），有效期至 {credential['expireAt']}",
        file=sys.stderr,
    )
    emit(credential)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())