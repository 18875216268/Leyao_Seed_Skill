#!/usr/bin/env python3
"""术语快问评测（卡机制的效果分母）：题库 → 两臂（带卡 / 无卡）→ 汇总判定。

方法依据（`参考/智能记忆调研/06-不足点调研与实测.md` §一#2）：Golden Set + 分层指标 + LLM-as-Judge 校准 + A/B 消融。
本脚本只做三件事，**不绑定任何客户端**（与 `run_trigger_eval.py` 同范式）：
1. `--coverage`：离线核对——题库结构合法 · 每题 gold 指针可回溯到候选 · 卡对"卡内题"的覆盖率；
2. `--emit-prompt --arm card|base`：打印两臂探测提示词（带卡臂附 `card.md`；基线臂禁资料）；
3. 汇总用户区结果 `term_eval_results.json`：按 臂 × 题组 统计 正确 / 弃答 / 编造，按阈值给退出码。

用法：
  python tests/run_term_eval.py --coverage
  python tests/run_term_eval.py --emit-prompt --arm card
  python tests/run_term_eval.py                  # 汇总已有结果（--min-pass 0.8）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
QUESTIONS = HERE / "eval_terms.json"
sys.path.insert(0, str(SKILL / "scripts"))

import common  # noqa: E402

RESULTS = common.HOME / "term_eval_results.json"     # 用户区（不进交付）
GROUPS = ("identify", "holdout", "route", "update", "refuse")


def load_questions() -> dict:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))


def cmd_coverage() -> int:
    """离线核对：结构 / gold 指针回验（对候选）/ 卡覆盖（对 card.json）。"""
    spec = load_questions()
    qs = spec["questions"]
    problems = []
    for q in qs:
        if q.get("group") not in GROUPS:
            problems.append("%s: 未知题组 %s" % (q.get("id"), q.get("group")))
        if not q.get("q"):
            problems.append("%s: 缺 q" % q.get("id"))
        if q.get("group") in ("identify", "holdout") and not q.get("gold_pointer"):
            problems.append("%s: %s 类题必须带 gold_pointer" % (q.get("id"), q.get("group")))
    cand = common.load_json(common.HOME / "card.candidates.json", {}) or {}
    cand_ids = {str(x.get("id")) for x in (cand.get("items") or [])}
    card = common.load_json(common.HOME / "card.json", {}) or {}
    card_names = {common.norm_text(it.get("name") or "") for it in (card.get("items") or [])}

    def gid(q):
        p = q.get("gold_pointer") or ""
        return p.split("#", 1)[1] if p.startswith("pool#") else ""

    checkable = [q for q in qs if gid(q)]
    missing_cand = [q["id"] for q in checkable if cand_ids and gid(q) not in cand_ids] if cand_ids else []
    ident = [q for q in qs if q["group"] == "identify"]
    hold = [q for q in qs if q["group"] == "holdout"]
    in_card_ident = [q["id"] for q in ident if common.norm_text(q.get("term") or "") in card_names]
    in_card_hold = [q["id"] for q in hold if common.norm_text(q.get("term") or "") in card_names]
    print("=== 术语快问 · 覆盖核对（离线）===")
    print("  题库 %d 题 ｜ 分组：%s" % (len(qs), {g: sum(1 for q in qs if q["group"] == g) for g in GROUPS}))
    print("  gold 指针可回溯候选：%s" % ("无法核对（无 candidates；先 card.py fetch）"
                                        if not cand_ids else ("%d/%d" % (len(checkable) - len(missing_cand), len(checkable)))))
    if cand_ids and not card_names:
        print("  卡覆盖：无法核对（无 card.json；先蒸馏生成）")
    elif card_names:
        print("  identify 组在卡内：%d/%d ｜ holdout 组误入卡：%d（应为 0——留出集不得被卡收录）"
              % (len(in_card_ident), len(ident), len(in_card_hold)))
    if problems:
        print("--- 结构问题 ---")
        for p in problems[:12]:
            print("  ✗ " + p)
    if missing_cand:
        print("--- gold 指针不在候选内（%d）---" % len(missing_cand))
        print("  " + ", ".join(missing_cand[:10]))
    ok = not problems and not missing_cand
    print("  => %s" % ("通过 ✓" if ok else "存在问题 ✗"))
    return 0 if ok else 1


PROMPT_CARD = """你在做「业务术语快问」。**只允许读一份速查卡**（内容如下），不得读其它资料、不得联网：
<card>
{card}
</card>

问题（逐条答，每条 ≤60 字；卡里没有的必须原样写「卡中未收录」，不得猜测 ✗）：
{qs}"""

PROMPT_BASE = """你在做「业务术语快问」。**不得读任何资料、不得联网**，只凭已有常识作答：
没有把握必须原样写「不知道」（诚实优先，不得编造具体口径/公式 ✗）。

问题（逐条答，每条 ≤60 字）：
{qs}"""


def cmd_emit_prompt(arm: str) -> int:
    spec = load_questions()
    qs = "\n".join("%d. %s" % (i, q["q"]) for i, q in enumerate(spec["questions"], 1))
    if arm == "card":
        card = common.load_json(common.HOME / "card.json", {}) or {}
        if not card:
            print("[term] 无 card.json（先 card.py fetch → 蒸馏 → check）")
            return 1
        card_md = common.HOME / "card.md"
        text = card_md.read_text(encoding="utf-8") if card_md.is_file() else json.dumps(card, ensure_ascii=False)
        print(PROMPT_CARD.format(card=text, qs=qs))
    else:
        print(PROMPT_BASE.format(qs=qs))
    print("\n[term] 结果写回 %s：{\"runs\":[{\"id\":\"t01\",\"arm\":\"%s\",\"verdict\":\"correct|wrong|abstain|fabricated\",\"note\":\"…\"}]}"
          % (RESULTS, arm))
    return 0


def summarize(min_pass: float) -> int:
    spec = load_questions()
    res = common.load_json(RESULTS, None)
    if res is None:
        print("[term] 尚无结果文件 %s——先 --emit-prompt 两臂各跑一轮，再写回该文件" % RESULTS)
        return 2
    by_id = {q["id"]: q for q in spec["questions"]}
    rows = {}
    for r in res.get("runs", []):
        q, arm, v = by_id.get(r.get("id")), r.get("arm"), r.get("verdict")
        if q and arm in ("card", "base") and v in ("correct", "wrong", "abstain", "fabricated"):
            rows.setdefault((arm, q["group"]), []).append(v)

    print("=== 术语快问 · 汇总（题库 %d 题）===" % len(spec["questions"]))
    lines, overall = [], []
    for arm in ("card", "base"):
        total = c = a = f = w = 0
        for g in GROUPS:
            vs = rows.get((arm, g), [])
            if not vs:
                continue
            c += vs.count("correct"); a += vs.count("abstain"); f += vs.count("fabricated"); w += vs.count("wrong")
            total += len(vs)
            print("  [%s/%s] 正确 %d/%d ｜ 弃答 %d ｜ 编造 %d" %
                  (arm, g, vs.count("correct"), len(vs), vs.count("abstain"), vs.count("fabricated")))
        ra = (c + a) / total if total else 0.0          # 正确或如实弃答 = 可信行为
        print("  [%s] 合计 %d 题 ｜ 正确 %d ｜ 弃答 %d ｜ 编造 %d ｜ 可信率 %.2f" % (arm, total, c, a, f, ra))
        overall.append(ra)
    card_rate = overall[0] if overall else 0.0
    ok = card_rate >= min_pass
    print("--- 判定 ---\n  带卡可信率 %.3f ｜ 门槛 %.2f → %s" % (card_rate, min_pass, "达标 ✓" if ok else "未达标 ✗"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="术语快问评测（两臂消融；汇总器不绑定客户端）")
    ap.add_argument("--coverage", action="store_true", help="离线核对题库/指针/卡覆盖")
    ap.add_argument("--emit-prompt", action="store_true", help="打印探测提示词")
    ap.add_argument("--arm", choices=["card", "base"], default="card", help="--emit-prompt 的臂")
    ap.add_argument("--min-pass", type=float, default=0.8)
    args = ap.parse_args()
    if args.coverage:
        return cmd_coverage()
    if args.emit_prompt:
        return cmd_emit_prompt(args.arm)
    return summarize(args.min_pass)


if __name__ == "__main__":
    sys.exit(main())
