"""槽位同步器：把集团能力包（基础包 + 各优化包）原样落到 vendor/ 对应槽位（仅同步，零解析）。

设计原则 —— 对集团包「完完全全动态」，不预设它叫什么、从哪来：
- 本脚本【只】负责：按 sync_config.json 把基础包落到 vendor/<base.vendor_dir>、
  把每个优化包落到 vendor/<opt.vendor_dir>，整包原样、零解析。
- 下载源与槽位目录名**全部外置于 sync_config.json**：
    base_package: { src_url, vendor_dir }
    optimizers:   [ { name, src_url, vendor_dir }, ... ]
  我们只知道「去配置的链接下载包、落到配置的槽位」，至于集团包叫什么名字、
  以后换成什么，本脚本一概不假设——换包只改配置，不改代码。
- 本脚本【绝不】解析 / 提取 / 清洗任何接口路径、参数、文档内容。
  接口的"理解"交给 AI（读 vendor 原样文档，按 SUBSKILL_ROUTING.md 路由），不做任何规则假设。
- 集团包原样不动：既保留 AI 可随时回退到原文的能力，也避免"提取器猜错格式"的脆弱性。
- 版本判据：主用包内 SKILL.md 的 `version:` frontmatter 字段；兜底用整包内容 hash。
  只有"版本变了"或"本地为空"才重拉；一致则跳过（零流量、零处理）。

失联降级链（与 SKILL.md §1.2 一致）：
    1) 自动重试 —— 网络抖动时按退避重试 `SYNC_RETRIES` 次；
    2) 换源恢复 —— 用 `--src-url <新地址>` 指定新下载源（持久化到 base_package.src_url）；
    3) 人工介入 —— 仍失败时输出可直接执行的换源指引，框架保持可用
       （已落地的 vendor/ 照常工作）。

用法：
    python scripts/pms_sync.py                  # 版本门控同步全部包（默认）
    python scripts/pms_sync.py --check          # 仅检查，不写入，返回差异
    python scripts/pms_sync.py --force          # 忽略版本，强制重拉整包
    python scripts/pms_sync.py --src-url <url>  # 原地址失联时换源（并持久化到基础包）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

# ----------------------------------------------------------------------------
# 同步配置：下载地址、槽位目录名、host 域名**全部**外置于 sync_config.json。
# 本脚本不内置任何下载地址——我们只知道「去配置的链接下载包、落到配置的
# 槽位」；集团换包、换地址、换域名，都只改配置，不改代码。
# ----------------------------------------------------------------------------
SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = SKILL_DIR / "sync_config.json"
SYNC_RETRIES = 3          # 单次同步的下载重试次数（网络抖动自愈）
SYNC_BACKOFF_SECONDS = (2, 6)  # 第 1、2 次重试前的等待秒数


def slot_name_from_url(url: str) -> str:
    """从下载 URL 推导槽位目录名：`.../foo.zip?t=1` → `foo`。

    仅用于 vendor_dir 未显式给出的兜底场景；推导不出返回空串。
    """
    path = urlparse(url).path.rstrip("/")
    stem = PurePosixPath(path).stem
    if stem and re.fullmatch(r"[A-Za-z0-9._\-]+", stem):
        return stem
    return ""


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[WARN] 同步配置无法读取：{exc}", file=sys.stderr)
    return {}


def package_list(cfg: dict) -> list[dict]:
    """从配置解析出待同步包清单（基础包 + 各优化包）。"""
    pkgs: list[dict] = []
    base = cfg.get("base_package") or {}
    if str(base.get("src_url") or "").strip():
        pkgs.append({
            "label": "base",
            "name": "集团基础 skill",
            "src_url": str(base["src_url"]).strip(),
            "vendor_dir": str(
                base.get("vendor_dir") or slot_name_from_url(str(base["src_url"])) or "leyo-sys"
            ).strip(),
        })
    for opt in (cfg.get("optimizers") or []):
        src = str(opt.get("src_url") or "").strip()
        if not src:
            print(f"[WARN] 优化包 {opt.get('name', '?')} 未配置 src_url，跳过")
            continue
        pkgs.append({
            "label": "optimizer",
            "name": opt.get("name") or opt.get("vendor_dir") or "?",
            "src_url": src,
            "vendor_dir": str(opt.get("vendor_dir") or slot_name_from_url(src)).strip(),
        })
    if not pkgs:
        raise SystemExit(
            "[ERROR] 未配置任何下载源：请在 sync_config.json 配置 "
            "base_package.src_url 或 optimizers[].src_url"
        )
    return pkgs


def _http_get(url: str, timeout: int = 60) -> bytes:
    """下载整包；失败按退避重试 SYNC_RETRIES 次（失联降级链第 1 步）。"""
    try:
        import requests  # 仅同步器用，便于干净环境缺失时给出明确提示
    except ImportError:
        raise SystemExit("缺少依赖 requests：请先 `pip install -r scripts/requirements.txt`")
    sep = "&" if "?" in url else "?"
    last_error = ""
    for attempt in range(SYNC_RETRIES):
        try:
            r = requests.get(f"{url}{sep}t={int(time.time() * 1000)}", timeout=timeout)
            r.raise_for_status()
            return r.content
        except Exception as exc:  # noqa: BLE001 —— 网络类异常统一按重试处理
            last_error = str(exc)
            if attempt < SYNC_RETRIES - 1:
                wait = SYNC_BACKOFF_SECONDS[min(attempt, len(SYNC_BACKOFF_SECONDS) - 1)]
                print(f"      下载失败（第 {attempt + 1} 次），{wait}s 后重试：{exc}")
                time.sleep(wait)
    raise RuntimeError(f"下载失败（已重试 {SYNC_RETRIES} 次）：{last_error}")


def _read_version_from_zip(z: zipfile.ZipFile) -> str | None:
    """从集团包 SKILL.md 的 frontmatter 读 version 字段。"""
    for name in z.namelist():
        if name.replace("\\", "/").endswith("SKILL.md"):
            txt = z.read(name).decode("utf-8", "ignore")
            m = re.search(r'(?m)^\s*version:\s*["\']?([^"\'\s]+)["\']?', txt)
            if m:
                return m.group(1).strip()
    return None


def _package_hash(z: zipfile.ZipFile) -> str:
    h = hashlib.sha256()
    for name in sorted(z.namelist()):
        h.update(name.encode("utf-8"))
        h.update(z.read(name))
    return h.hexdigest()


def _unzip_verbatim(z: zipfile.ZipFile, dest: Path) -> None:
    """整包原样落盘，不剥离任何前缀、不做任何改写。"""
    dest.mkdir(parents=True, exist_ok=True)
    for name in z.namelist():
        rel = name.replace("\\", "/")
        if rel.endswith("/"):
            continue
        data = z.read(name)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)


def _sync_one(pkg: dict, force: bool, check_only: bool, src_override: str) -> int:
    """同步单个包（基础或优化）。返回进程退出码。"""
    src_url = src_override if (pkg["label"] == "base" and src_override) else pkg["src_url"]
    vendor_dir = SKILL_DIR / "vendor" / pkg["vendor_dir"]
    version_file = vendor_dir / ".version"
    hash_file = vendor_dir / ".package_hash"

    print(f"[1/4] 下载 {pkg['name']} 在线包 {src_url}")
    data = _http_get(src_url)
    z = zipfile.ZipFile(__import__("io").BytesIO(data))
    remote_ver = _read_version_from_zip(z)
    remote_hash = _package_hash(z)
    print(f"      在线版本={remote_ver}  包大小={len(data)}B  包hash={remote_hash[:12]}…")

    local_ver = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None
    local_hash = hash_file.read_text(encoding="utf-8").strip() if hash_file.exists() else None

    if not (version_file.exists() or hash_file.exists()):
        print("[2/4] 本地槽位为空 → 需要拉取")
        need = True
    elif force:
        print("[2/4] --force → 强制重拉")
        need = True
    elif remote_ver is not None and local_ver is not None and remote_ver == local_ver:
        print(f"[2/4] 版本一致（{local_ver}=={remote_ver}）→ 跳过，零重写")
        return 0
    elif remote_ver is None and local_hash is not None and local_hash == remote_hash:
        print("[2/4] 无 version 字段，但包 hash 一致 → 跳过")
        return 0
    else:
        reason = "版本变化" if (remote_ver and local_ver and remote_ver != local_ver) else "包 hash 变化/版本缺失"
        print(f"[2/4] {reason} → 需要重拉")
        need = True

    if check_only:
        print("[3/4] --check 模式：仅报告差异，不写入")
        print(f"      本地版本={local_ver}  在线版本={remote_ver}  需更新={need}")
        return 0 if not need else 1

    print(f"[3/4] 原样落盘到 vendor/{pkg['vendor_dir']}/（不解析、不清洗、不提取）")
    _unzip_verbatim(z, vendor_dir)
    vendor_dir.mkdir(parents=True, exist_ok=True)
    if remote_ver is not None:
        version_file.write_text(remote_ver, encoding="utf-8")
    hash_file.write_text(remote_hash, encoding="utf-8")
    print(f"[4/4] 完成：{pkg['name']} 版本={remote_ver}")
    return 0


def sync(force: bool = False, check_only: bool = False, src_url: str = "") -> int:
    cfg = load_config()
    # 换源持久化：仅作用于基础包
    if src_url and cfg.get("base_package"):
        cfg["base_package"]["src_url"] = src_url
        try:
            CONFIG_FILE.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"[0/4] 已换源并写入 {CONFIG_FILE}")
        except OSError as exc:
            print(f"[WARN] 新地址未能写入配置文件（本次仍生效）：{exc}", file=sys.stderr)

    pkgs = package_list(cfg)
    rc = 0
    for pkg in pkgs:
        try:
            r = _sync_one(pkg, force=force, check_only=check_only, src_override=src_url)
            rc = rc or r
        except Exception as exc:  # 单包失败不影响其它包，框架保持可用
            print(f"[ERROR] 同步 {pkg['name']} 失败：{exc}", file=sys.stderr)
            rc = 2
    print("同步完成。下一步：AI 按 vendor/SUBSKILL_ROUTING.md 读 vendor 原样文档取数。")
    return rc


def main() -> int:
    from pms_common import configure_stdio
    configure_stdio()
    ap = argparse.ArgumentParser(
        description="集团包槽位同步器（仅同步，零解析；源与槽位全部外置于 sync_config.json）"
    )
    ap.add_argument("--check", action="store_true", help="仅检查版本差异，不写入")
    ap.add_argument("--force", action="store_true", help="忽略版本门控，强制重拉整包")
    ap.add_argument(
        "--src-url",
        metavar="URL",
        default="",
        help="原地址失联时换用新下载源；该地址会写入 sync_config.json 的 base_package.src_url 供后续复用",
    )
    args = ap.parse_args()
    try:
        return sync(force=args.force, check_only=args.check, src_url=args.src_url)
    except Exception as exc:  # 同步失败绝不让框架崩：提示清晰，退出非零
        print(f"[ERROR] 同步失败：{exc}", file=sys.stderr)
        print(
            "        可换源重试：python scripts/pms_sync.py --src-url <新的整包 zip 地址>",
            file=sys.stderr,
        )
        print("        框架仍可工作：已落地的 vendor/ 照常可用。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
