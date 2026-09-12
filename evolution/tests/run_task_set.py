#!/usr/bin/env python3
"""任务集回归（常驻 · 零联网）：对工作区产物按任务层判据判定档位，并校准判定器本身。

设计（为什么这样才诚实）：
- 判定器输出**四档**：`pass`（结构合规 + 含 §6 推进控制）· `legacy`（结构合规但缺 §6，历史合理）·
  `doing`（进行态：结构合规、无交付件）· `fail`（结构不合规）。
  把"缺新机制"与"结构性错误"分开，避免误杀历史产物、同时保住对结构问题的把关。
- 合成用例带**期望档位** → 判定器与实际比对，输出**判定准确率**（测判定器本身，而不是测 AI）。
- 阈值档（`--profile`）：`strict`（§6 也算硬）· `balanced`（默认，推荐）· `loose`（只查结构最小集）。
  三档跑同一批用例，用数据选"最佳平衡"。

诚实边界：本回归**不度量 AI 行为**；pass^k 由 `--record` 在真实使用中逐次累积（写用户区 `.leyao-data/data/state/task_set_results.jsonl`）。

用法：
  python evolution/tests/run_task_set.py                   # balanced（默认）
  python evolution/tests/run_task_set.py --profile strict  # 严格档（对比用）
  python evolution/tests/run_task_set.py --json            # 机器可读
  python evolution/tests/run_task_set.py --record --json   # 追加一条 pass^k 台账（写用户区 .leyao-data/data/state/task_set_results.jsonl）
输出：逐例明细 + 汇总（pass/legacy/doing/fail · 判定准确率），退出码 0/1（准确率 100% 为通过）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # leyao-seed-core/
sys.dont_write_bytecode = True                      # 运行期零写包（不在包内生成 __pycache__）
sys.path.insert(0, str(ROOT / "evolution"))
import paths  # noqa: E402  （用户区路径唯一事实源；导入即初始化）
WORKSPACE_HOME = ROOT.parent                        # 工作区所在目录（与框架同级）
SET_FILE = Path(__file__).with_name("task_set.json")
ZONES = ("01-原始材料区", "02-任务执行区", "03-结果交付区", "04-归档区")
REC = "02-任务执行区/过程记录.md"
DELIV_DIR = "03-结果交付区"
SEC6 = ("## 6. 执行循环与回退", "循环：", "停滞：", "回退：", "成本与红线")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def corpus(p: Path) -> str:
    d = p / DELIV_DIR
    deliv = "\n".join(read(f) for f in d.rglob("*") if f.is_file()) if d.is_dir() else ""
    return read(p / REC) + "\n" + deliv


# ---------------- 合成用例（临时目录内构建，跑完即删） ----------------
def build_synthetic(case_id: str, root: Path) -> Path:
    p = root / case_id
    for z in ZONES:
        (p / z).mkdir(parents=True, exist_ok=True)
    if case_id == "syn-empty":
        (p / "README.md").write_text("# 空任务\n", encoding="utf-8")
        return p
    tpl = read(ROOT / "processor" / "templates" / "过程记录.md")
    rec = tpl.replace("## 6. 执行循环与回退（长任务；判据见 `processor/control.md`）",
                      "## 6. 执行循环与回退（长任务；判据见 `processor/control.md`）")
    if case_id in ("syn-legacy",):
        rec = rec.split("## 6.")[0]                       # 去掉 §6 = 机制前产物
    if case_id == "syn-partial":
        head = rec.split("## 6.")[0]
        rec = head + "## 6. 执行循环与回退\n\n- 循环：第 1 次调用 → 校验通过 → 停止 ｜ 停止理由：判据满足\n"
    (p / REC).write_text(rec, encoding="utf-8")
    if case_id == "syn-bad-root":
        (p / "散落文件.txt").write_text("根散落\n", encoding="utf-8")
    if case_id in ("syn-full", "syn-legacy"):
        (p / DELIV_DIR / "交付说明.md").write_text(
            "# 交付说明\n\n口径：含税、2026-09-01~09-12（详见过程记录 KI）。\n"
            "红线：不可逆未确认 = 0（逐项核对）。\n成本：循环 2 次 · 回退 1 次（上限 6/2）。\n"
            "备注：本文件为合成用例产物，用于判定器校准；内容仅为满足判据的最小集。\n",
            encoding="utf-8")
    return p


# ---------------- 判定 ----------------
def judge(p: Path, stage: str, profile: str) -> dict:
    rec, body = read(p / REC), corpus(p)
    structural = {
        "四区齐备": all((p / z).is_dir() for z in ZONES),
        "根无散落": not [x for x in p.iterdir() if x.name not in ZONES + ("README.md",)],
        "过程记录在场": (p / REC).is_file(),
        "交付件非空": any(f.is_file() and f.stat().st_size > 200 for f in (p / DELIV_DIR).rglob("*")),
    }
    if profile == "loose":
        fails = [k for k in ("四区齐备", "过程记录在场") if not structural[k]]
    else:
        need = ["四区齐备", "根无散落", "过程记录在场"] + (["交付件非空"] if stage == "done" else [])
        fails = [k for k in need if not structural[k]]
    if fails:
        return {"level": "fail", "why": "结构不合规：" + "、".join(fails)}
    if stage != "done":
        return {"level": "doing", "why": "进行态：结构合规，未要求交付件与 §6"}
    has6 = all(k in rec for k in SEC6)
    if profile == "strict" and not has6:
        return {"level": "fail", "why": "严格档：缺 §6 推进控制记录"}
    if not has6:
        return {"level": "legacy", "why": "结构合规但缺 §6 字段（机制前产物）"}
    if "口径" not in body:
        return {"level": "pass", "why": "§6 齐备（口径缺——软提示）"} if profile == "loose" \
            else {"level": "legacy", "why": "§6 齐备但交付物缺口径标注"}
    return {"level": "pass", "why": "结构合规 + §6 齐备 + 口径在场"}


def main() -> int:
    ap = argparse.ArgumentParser(description="任务集回归（判定器校准 + pass^k 台账）")
    ap.add_argument("--profile", choices=["strict", "balanced", "loose"], default="balanced")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", action="store_true", help="把本次结果追加到用户区 data/state/task_set_results.jsonl")
    args = ap.parse_args()

    spec = json.loads(SET_FILE.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="task_set_"))
    rows, ok_cnt = [], 0
    try:
        for case in spec["cases"]:
            p = (WORKSPACE_HOME / case["path"]) if case["kind"] == "real" else build_synthetic(case["id"], tmp)
            got = judge(p, case.get("stage", "done"), args.profile)
            hit = got["level"] == case["expect"]
            ok_cnt += int(hit)
            rows.append({**case, "got": got["level"], "why": got["why"], "expected_ok": hit})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(rows)
    acc = ok_cnt / max(1, total)
    summary = {"ok": acc >= 1.0, "profile": args.profile, "cases": total,
               "matched": ok_cnt, "accuracy": round(acc, 4),
               "levels": {lv: sum(1 for r in rows if r["got"] == lv) for lv in ("pass", "legacy", "doing", "fail")}}
    if args.json:
        print(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=1))
    else:
        for r in rows:
            print("%-18s %-7s got=%-6s expect=%-7s %s %s"
                  % (r["id"], r["kind"], r["got"], r["expect"], "OK " if r["expected_ok"] else "MISS",
                     "" if r["expected_ok"] else "→ " + r["why"]))
        print(json.dumps(summary, ensure_ascii=False))

    if args.record:
        # 台账只写**用户区**（红线：运行时数据不进包内；包内只读）；追加式 JSONL，永不裁剪
        entry = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "profile": args.profile,
                 "accuracy": round(acc, 4), "levels": summary["levels"]}
        paths.TASK_SET_RESULTS_F.parent.mkdir(parents=True, exist_ok=True)
        with paths.TASK_SET_RESULTS_F.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print("台账已追加：%s" % paths.TASK_SET_RESULTS_F)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
