"""凭证窗口：本板不实现登录，只消费凭证（可独立于任何父 skill 移植使用）。

来源优先级（对齐 Pms 子 skill 模式）：

1. 环境变量：``BI_UID_TOKEN`` + ``BI_UID_TOKEN_SIG``（可选 ``BI_UID_EXP``，unix 秒）；
2. 凭证文件：环境变量 ``BI_CREDENTIAL_FILE`` 指向的 JSON，或默认
   ``<skill_root>/resources/credential.local.json``，格式
   ``{"token": "...", "tokenSig": "...", "exp": 1789...}``；
3. 宿主父 skill 登录仓回退（可选）：仅当向上存在 ``scripts/login_bi.py``
   时使用其 ``verify_credential``；不存在则静默跳过，不影响独立部署。

三级全空时抛 ``AUTH_REQUIRED`` 并引导用户传入凭证。本模块绝不弹窗、不发起登录。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .errors import BiError


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _from_env() -> dict[str, Any] | None:
    token = os.environ.get("BI_UID_TOKEN", "").strip()
    if not token:
        return None
    try:
        exp = int(os.environ.get("BI_UID_EXP") or 0)
    except ValueError:
        exp = 0
    return {
        "token": token,
        "tokenSig": os.environ.get("BI_UID_TOKEN_SIG", "").strip(),
        "exp": exp,
    }


def _from_file() -> dict[str, Any] | None:
    configured = os.environ.get("BI_CREDENTIAL_FILE", "").strip()
    path = Path(configured).expanduser() if configured else _skill_root() / "resources" / "credential.local.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BiError("CREDENTIAL_INVALID", f"凭证文件解析失败：{path}") from exc
    token = str(data.get("token") or data.get("uIdToken") or "").strip()
    if not token:
        raise BiError("CREDENTIAL_INVALID", f"凭证文件缺少 token：{path}")
    try:
        exp = int(data.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    return {
        "token": token,
        "tokenSig": str(data.get("tokenSig") or data.get("uIdToken.sig") or ""),
        "exp": exp,
    }


def _from_parent() -> dict[str, Any] | None:
    login_bi: Path | None = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "scripts" / "login_bi.py"
        if candidate.is_file():
            login_bi = candidate
            break
    if login_bi is None:
        return None
    login_dir = login_bi.parent
    if str(login_dir) not in sys.path:
        sys.path.insert(0, str(login_dir))
    try:
        import login_bi as lb  # noqa: PLC0415  可选宿主回退，延迟导入
    except Exception:
        return None
    try:
        result = lb.verify_credential(validate_remote=False)
    except Exception:
        return None
    if not result.get("authenticated"):
        return None
    user = result.get("user") if isinstance(result.get("user"), dict) else {}
    return {
        "token": str(result.get("token") or ""),
        "tokenSig": str(result.get("tokenSig") or ""),
        "exp": int(result.get("exp") or 0),
        "loginId": str(user.get("loginId") or ""),
    }


def load_credential() -> dict[str, Any]:
    """按优先级读取凭证；三级全空抛 AUTH_REQUIRED（绝不弹窗、绝不登录）。"""
    for source in (_from_env, _from_file, _from_parent):
        credential = source()
        if credential and credential.get("token"):
            return credential
    raise BiError(
        "AUTH_REQUIRED",
        "无可用凭证。请传入凭证：设置 BI_UID_TOKEN / BI_UID_TOKEN_SIG 环境变量，"
        "或提供 resources/credential.local.json（或 BI_CREDENTIAL_FILE 指向的 JSON）；"
        "在宿主父 skill 内也可由其登录器（scripts/bi_login.py）扫码后自动供给。",
    )


def local_expired(credentials: dict[str, Any], skew_seconds: int = 60) -> bool:
    """本地过期判断（exp 为 unix 秒；缺省 0 视为不过期，交由服务端裁决）。"""
    exp = int(credentials.get("exp") or 0)
    return bool(exp and exp <= int(time.time()) + skew_seconds)
