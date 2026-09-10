"""Load and validate the non-secret BI connection profile (板块锚点，不含任何账号/凭证)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import BiError


_EXPECTED_HOSTS = {
    "biBase": "bi.leyopharm.com",
}


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_profile(root: Path | None = None) -> dict[str, Any]:
    path = (root or skill_root()) / "resources" / "profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BiError(
            "PROFILE_INVALID",
            "无法读取 BI 连接配置。",
            details={"path": str(path)},
        ) from exc

    required = (
        "profileId",
        "biBase",
        "pageId",
        "cardId",
        "datasetId",
        "headers",
    )
    missing = [key for key in required if not profile.get(key)]
    if missing:
        raise BiError(
            "PROFILE_INVALID",
            "BI 连接配置缺少必填项。",
            details={"missing": missing},
        )

    for key in ("biBase",):
        parsed = urlparse(str(profile[key]))
        if (
            parsed.scheme != "https"
            or parsed.hostname != _EXPECTED_HOSTS[key]
            or parsed.port is not None
            or parsed.path not in ("", "/")
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise BiError(
                "PROFILE_INVALID",
                f"{key} 必须是固定的官方 HTTPS 主机根地址。",
            )
        profile[key] = str(profile[key]).rstrip("/")

    ca_bundle = os.environ.get("BI_CA_BUNDLE", "").strip()
    if ca_bundle:
        ca_path = Path(ca_bundle).expanduser().resolve()
        if not ca_path.is_file():
            raise BiError(
                "TLS_CA_NOT_FOUND",
                "BI_CA_BUNDLE 指向的证书文件不存在。",
                details={"path": str(ca_path)},
            )
        profile["caBundle"] = str(ca_path)
    return profile
