#!/usr/bin/env python3
"""触发评测（Agent Skills 官方方法）：正/负例 × N 次 → 触发率 → 阈值判定。

官方依据（agentskills.io/skill-creation/optimizing-descriptions）：
- 约 20 条 query：8–10 应触发 + 8–10 不应触发；负例以 near-miss 为主。
- 每条跑多次（3 次为合理起点），trigger_rate = 触发次数 / 总次数。
- 判定阈值 0.5：应触发须 > 阈值；不应触发须 < 阈值。
- train / validation 分离：只用 train 的失败指导改动，用 validation 判断是否泛化。
- 「技能被触发」= agent 加载了该技能的 SKILL.md（检测方式依客户端而定）。

本脚本只做两件事，不绑定任何客户端：
1. `--emit-prompt`：按当前 SKILL.md 的真实 name/description 打印探测提示词（逐条跑时原样投喂）。
2. 汇总触发记录（用户区 `.leyao-data/data/state/trigger_results.json`）：算触发率、判定通过、分 train/validation 与正/负例统计，按阈
   （注：进化层"成功路径蒸馏"的回归护栏见 `run_checks.py` 的 `distiller_success_lane`）值给退出码。

用法：
  python evolution/tests/run_trigger_eval.py                 # 汇总已有结果
  python evolution/tests/run_trigger_eval.py --emit-prompt   # 打印探测提示词
  python evolution/tests/run_trigger_eval.py --min-pass 0.9  # 通过率低于阈值 → exit 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.dont_write_bytecode = True                  # 运行期零写包（不在包内生成 __pycache__）

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                       # evolution/tests/ → 框架根

sys.path.insert(0, str(ROOT / "evolution"))
import paths  # noqa: E402  （导入即初始化用户区）

QUERIES = HERE / "trigger_queries.json"
RESULTS = paths.TRIGGER_RESULTS_F            # 用户区记录（不进交付；LEYAO_SEED_HOME 可覆盖）


def load_queries() -> dict:
    return json.loads(QUERIES.read_text(encoding="utf-8"))


def load_results() -> dict | None:
    if not RESULTS.exists():
        return None
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def skill_identity() -> tuple[str, str]:
    """从 SKILL.md 读 name / description（唯一事实源，避免提示词与描述脱节）。"""
    fm = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    name = desc = ""
    for line in fm.splitlines():
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("description:"):
            desc = line.split(":", 1)[1].strip().strip('"')
    return name, desc


PROMPT = """你是 AI agent。下面是客户端向你展示的可用技能清单（只有 name 与 description）：

<available_skills>
<skill>
<name>{name}</name>
<description>{desc}</description>
</skill>
</available_skills>

用户消息：
"{query}"

判断：面对这条消息，你会不会去查阅上面的技能（即触发它）？
只回一行，格式二选一（不要解释）：
SKILL: {name}
NONE"""


def cmd_emit_prompt() -> int:
    name, desc = skill_identity()
    if not name or not desc:
        print("[trigger] SKILL.md 缺 name/description，无法生成提示词")
        return 1
    print(PROMPT.format(name=name, desc=desc, query="<把待测 query 原样放这里>"))
    print("\n[trigger] 判定口径：回复含 SKILL: %s → 视为触发；含 NONE → 未触发。" % name)
    print("[trigger] 每条 query 跑 %d 次，结果逐条写入 %s" % (load_queries()["runs_per_query"], RESULTS))
    return 0


def summarize(min_pass: float) -> int:
    spec = load_queries()
    res = load_results()
    if res is None:
        print("[trigger] 尚无结果文件 %s——先用 --emit-prompt 逐条跑，再把结果写回该文件" % RESULTS)
        return 2

    name, _ = skill_identity()
    thr = float(spec["threshold"])
    runs_req = int(spec["runs_per_query"])
    by_id = {q["id"]: q for q in spec["queries"]}
    tally: dict[str, list[int]] = {q["id"]: [] for q in spec["queries"]}
    for inv in res.get("invocations", []):
        if inv.get("id") in tally and isinstance(inv.get("triggered"), bool):
            tally[inv["id"]].append(1 if inv["triggered"] else 0)

    rows, failures, incomplete = [], [], []
    for qid, hits in tally.items():
        q = by_id[qid]
        runs = len(hits)
        if runs < runs_req:
            incomplete.append("%s(%d/%d)" % (qid, runs, runs_req))
        rate = (sum(hits) / runs) if runs else 0.0
        passed = (rate > thr) if q["should_trigger"] else (rate < thr)
        rows.append({"id": qid, "split": q["split"], "should_trigger": q["should_trigger"],
                     "near_miss": q.get("near_miss", False), "runs": runs,
                     "triggers": sum(hits), "rate": round(rate, 3), "pass": passed})
        if runs and not passed:
            failures.append(rows[-1])

    def rate_of(sel) -> tuple[int, int]:
        sub = [r for r in rows if sel(r) and r["runs"]]
        return sum(1 for r in sub if r["pass"]), len(sub)

    pos_ok, pos_n = rate_of(lambda r: r["should_trigger"])
    neg_ok, neg_n = rate_of(lambda r: not r["should_trigger"])
    nm_ok, nm_n = rate_of(lambda r: r["near_miss"])
    tr_ok, tr_n = rate_of(lambda r: r["split"] == "train")
    va_ok, va_n = rate_of(lambda r: r["split"] == "validation")
    all_ok, all_n = rate_of(lambda r: True)

    print("=== 触发评测汇总（skill=%s · 阈值=%s · 每条 %d 次）===" % (name, thr, runs_req))
    for r in rows:
        print("  %-4s %-10s 期望=%-5s 触发=%d/%d 率=%.2f  %s" %
              (r["id"], r["split"], "触发" if r["should_trigger"] else "不触发",
               r["triggers"], r["runs"], r["rate"], "PASS" if r["pass"] else "FAIL"))
    print("--- 分类 ---")
    print("  应触发 %d/%d ｜ 不应触发 %d/%d ｜ near-miss 负例 %d/%d" %
          (pos_ok, pos_n, neg_ok, neg_n, nm_ok, nm_n))
    print("  train %d/%d ｜ validation %d/%d ｜ 合计 %d/%d" % (tr_ok, tr_n, va_ok, va_n, all_ok, all_n))
    if failures:
        print("--- 失败（只用 train 的失败指导改动；validation 失败说明泛化不足）---")
        for r in failures:
            print("  %s[%s] 期望=%s 实际率=%.2f" %
                  (r["id"], r["split"], "触发" if r["should_trigger"] else "不触发", r["rate"]))
    if incomplete:
        print("--- 次数不足（每条需 %d 次）---\n  %s" % (runs_req, " ".join(incomplete)))

    overall = (all_ok / all_n) if all_n else 0.0
    val = (va_ok / va_n) if va_n else 0.0
    print("--- 判定 ---")
    print("  合计通过率 %.3f ｜ validation 通过率 %.3f ｜ 参照目标 %.2f" % (overall, val, min_pass))
    if failures:
        print("  未通过用例 %d 个（官方停止条件 = train 全部通过；有一条未过就继续改 description 重跑）" % len(failures))
    ok = overall >= min_pass and val >= min_pass and not incomplete and not failures
    print("  => %s" % ("达标" if ok else "未达标（按官方循环：改 description 后重跑；5 轮通常收敛）"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent Skills 官方触发评测（汇总器，不绑定客户端）")
    ap.add_argument("--emit-prompt", action="store_true", help="打印探测提示词（用当前 SKILL.md 的 name/description）")
    ap.add_argument("--min-pass", type=float, default=0.9, help="通过率门槛（默认 0.9，官方参照值）")
    args = ap.parse_args()
    return cmd_emit_prompt() if args.emit_prompt else summarize(args.min_pass)


if __name__ == "__main__":
    sys.exit(main())
