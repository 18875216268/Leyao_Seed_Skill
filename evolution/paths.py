#!/usr/bin/env python3
"""用户区（.leyao-data）路径：本层唯一路径事实源 + 首次初始化（bootstrap）。

两个区（模型见 EVOLUTION.md）：
- 包内（SKILL_ROOT，只读交付物）：代码 / 文档 / 模板 / 资产与路由；
- 用户区（HOME，运行态）：memory · meta · state —— 只在这里读写，更新永不触碰。

解析优先级：LEYAO_SEED_HOME → <skill 同级>/.leyao-data（父目录可写）→ ~/.leyao-data。
首次导入即初始化（幂等）：建目录 → 落 config.json → 记忆缺则从模板播种。
不做旧版迁移 / 兼容：包内出现运行态一律视为污染，由自检 `paths_external` 拦截（绝不自行搬运）。
本模块不 import store（防循环依赖）；bootstrap 审计行按 store.audit 同一格式直写。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]


def _writable(d: Path) -> bool:
    """可写探测（mkdir + 落探针）：同级不可写时兜底 home。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve() -> Path:
    env = (os.environ.get("LEYAO_SEED_HOME") or "").strip()
    if env:
        return Path(env).expanduser().resolve()   # 规范化为绝对长路径（消 ~、相对段与 Windows 8.3 短名）
    cand = SKILL_ROOT.parent / ".leyao-data"          # 与 skill 同级（便携；更新器作用域之外）
    return cand if _writable(cand) else Path.home() / ".leyao-data"


HOME = _resolve()
CONFIG_F = HOME / "config.json"
DATA_D = HOME / "data"
STATE_D = DATA_D / "state"
MEMORY_F = DATA_D / "memory.md"
META_F = DATA_D / "meta.json"
VERSIONS_F = DATA_D / "versions.json"          # 版本记录（版本维护层；落地器唯一维护）
AUDIT_F = STATE_D / "audit.log"
PROPOSALS_D = STATE_D / "proposals"
TRIGGER_RESULTS_F = STATE_D / "trigger_results.json"
TASK_SET_RESULTS_F = STATE_D / "task_set_results.jsonl"   # 任务集回归台账（pass^k 累积；追加式，永不裁剪）

USER_AREA_README_F = DATA_D / "README.md"        # 用户区索引（用途 / 清理策略 / 落点规则）
TPL_MEMORY = SKILL_ROOT / "evolution" / "templates" / "memory.md"
TPL_META = SKILL_ROOT / "evolution" / "templates" / "meta.json"
TPL_USER_AREA = SKILL_ROOT / "evolution" / "templates" / "user-area.md"


def maintainer() -> bool:
    """本实例是否维护者：route_update / asset_write 内容类变异的放行标记。"""
    try:
        return json.loads(CONFIG_F.read_text(encoding="utf-8")).get("maintainer") is True
    except (OSError, json.JSONDecodeError):
        return False


def _audit(event: str, **fields) -> None:
    """bootstrap 审计（与 store.audit 同格式；直写仅为避免循环依赖）。"""
    STATE_D.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                        "event": event, **fields}, ensure_ascii=False)
    with AUDIT_F.open("a", encoding="utf-8") as fh:
        fh.write(entry + "\n")


def ensure() -> dict:
    """幂等初始化：返回本次动作（空列表 = 已就绪）。每次导入本模块执行一次。"""
    actions = []
    DATA_D.mkdir(parents=True, exist_ok=True)
    STATE_D.mkdir(parents=True, exist_ok=True)
    if not CONFIG_F.exists():
        CONFIG_F.write_text(json.dumps({"maintainer": False}, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        actions.append("config.init")
    if not MEMORY_F.exists() and TPL_MEMORY.exists():
        shutil.copy2(TPL_MEMORY, MEMORY_F)
        actions.append("memory.seed")
    if not USER_AREA_README_F.exists() and TPL_USER_AREA.exists():
        shutil.copy2(TPL_USER_AREA, USER_AREA_README_F)      # 用户区索引：AI/人一眼看懂各区用途与清理策略
        actions.append("readme.seed")
    if actions:
        _audit("bootstrap", actions=actions)
    return {"home": str(HOME), "actions": actions}


LAST_ENSURE = ensure()                                   # 首次导入即就绪；本次初始化动作留档（诊断/测试用）
