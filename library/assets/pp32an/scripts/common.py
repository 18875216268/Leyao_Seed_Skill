#!/usr/bin/env python3
"""共享基础件：路径/运行数据区/本地配置/JSON 读写/文本规范化/相似度/输出。

红线：本包只放规则、模板与脚本；**运行数据与配置（含秘钥）一律只落外部数据区**（不写包内）。
数据区解析优先级（**用户态一律归用户数据区，不写包内、不写 skill 同级**）：
LEYAO_KB_HOME（显式覆盖）→ **框架生态**：<用户区>/data/assets/<id>（LEYAO_SEED_HOME 可覆盖；挂载时自动判定）
→ ~/.leyao-kb（独立安装统一落家目录）→ 家目录不可写时才回落 <skill 同级>/.leyao-kb（少见兜底）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
CN = timezone(timedelta(hours=8))


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_home() -> Path:
    env = (os.environ.get("LEYAO_KB_HOME") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # 生态挂载（被"框架资产层"挂在 <某包>/library/assets/<id>/ 下）：此时的"同级"= library/assets/ ✗
    # 会落进那个包内、违反"运行态只存用户区"——改为归口到该生态的用户区：
    #   <包父级>/.leyao-data/data/assets/<id>/（LEYAO_SEED_HOME 可覆盖）
    # 独立安装（skill 目录不在 assets/ 下）时行为不变：仍为 <skill 同级>/.leyao-kb。
    if SKILL_ROOT.parent.name == "assets" and (SKILL_ROOT.parent.parent / "engine.py").exists():
        seed = (os.environ.get("LEYAO_SEED_HOME") or "").strip()
        eco = Path(seed).expanduser().resolve() if seed else SKILL_ROOT.parents[2].parent / ".leyao-data"
        cand = eco / "data" / "assets" / SKILL_ROOT.name
        if _writable(cand):
            return cand
    # 其余安装方式（独立部署 / 任意机器）：同样统一落**用户数据区**（家目录），
    # 不再写 <skill 同级>（避免落进某个项目/包内、跨机器不一致）；家目录不可写时才兜底同级。
    home = Path.home() / ".leyao-kb"
    if _writable(home):
        return home
    cand = SKILL_ROOT.parent / ".leyao-kb"
    return cand if _writable(cand) else home


HOME = _resolve_home()
CACHE_F = HOME / "cache.jsonl"
MEMORY_F = HOME / "memory.jsonl"
FEEDBACK_F = HOME / "feedback.jsonl"
REFLECT_F = HOME / "reflect.jsonl"
CONFIG_F = HOME / "config.local.json"      # 本地配置（敏感/环境参数只存本地；包内零秘钥）
LEYOU_TOKEN_F = HOME / "leyou_token.json"  # 云智库登录态（客户端 --token-file 目标；只落用户区）


def ensure_home() -> Path:
    HOME.mkdir(parents=True, exist_ok=True)
    return HOME


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_config() -> dict:
    """本地配置（用户数据区 `config.local.json`；**不入包/不入仓**）——缺失或不可解析返回 {}。

    字段：`pool.write_token`（公共池写令牌）；`leyou_firebase.*`（云智库凭证库参数——
    `database_url` / `fangwen_miyue` 必需，其余项目参数备查）。说明见 `references/leyou-cli.md`。
    """
    data = load_json(CONFIG_F, {})
    return data if isinstance(data, dict) else {}


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, limit: int = 0) -> list:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:] if limit else out


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


_PUNCT = re.compile(r"[\s，。、；：？！,.;:?!\"'“”‘’（）()\[\]【】<>《》~`]+")


def norm_text(text: str) -> str:
    """规范化：全角→半角、去标点空白、英文小写（保留中文原样）。"""
    out = []
    for ch in str(text or ""):
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:            # 全角 ASCII → 半角
            ch = chr(code - 0xFEE0)
        out.append(ch)
    s = "".join(out).lower()
    s = _PUNCT.sub("", s)
    return s.strip()


def tokens(text: str) -> set:
    """粗分词：中文 2-gram + 英文数字词（无第三方依赖）。"""
    s = norm_text(text)
    words = set(re.findall(r"[a-z0-9]{2,}", s))
    zh = re.sub(r"[a-z0-9]+", "", s)
    words |= {zh[i:i + 2] for i in range(max(0, len(zh) - 1))}
    return words


def similarity(a: str, b: str) -> float:
    """无模型依赖的相似度：[0,1]（token Jaccard 与字符 Dice 的较大者）。

    说明：设计 v2 的语义缓存默认走此实现（embedding 版为可选增强）；
    阈值经 registry.semantic_threshold 配置（默认 0.85），需按真实语料校准。
    """
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = tokens(a), tokens(b)
    jac = len(ta & tb) / max(1, len(ta | tb))
    if na in nb or nb in na:
        dice = 2 * min(len(na), len(nb)) / (len(na) + len(nb))
    else:
        common = sum(1 for i in range(min(len(na), len(nb))) if na[i] == nb[i])
        dice = 2 * common / (len(na) + len(nb))
    return round(max(jac, dice), 4)


def age_hours(iso: str) -> float:
    try:
        t = datetime.fromisoformat(str(iso))
        if t.tzinfo is None:
            t = t.replace(tzinfo=CN)
        return max(0.0, (datetime.now(CN) - t).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return 24.0 * 365


def emit(data: dict) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


def log(msg: str) -> None:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print(msg, file=sys.stderr)
