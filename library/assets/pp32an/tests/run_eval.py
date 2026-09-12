#!/usr/bin/env python3
"""金标评测（需网络）：Recall@k 近似（answer 命中 keywords）+ 延迟分位 + 缓存命中率 + 拒答正确性。

用法：python tests/run_eval.py --online [--limit 20]
设计：两段分测（LongMemEval）——本脚本测"检索段近似 Recall"与端到端状态；
rubric 精确评分由人工/AI 承担（本脚本不假装能判语义正确 ✗）。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))


def main() -> int:
    ap = argparse.ArgumentParser(description="金标评测（默认离线提示）")
    ap.add_argument("--online", action="store_true", help="确认联网执行")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    cases = json.loads((HERE / "eval_cases.json").read_text(encoding="utf-8"))["cases"]
    if not args.online:
        print(json.dumps({"ok": False, "hint": "默认不联网：确认后加 --online（会真实请求公共池/云智库）",
                          "cases": len(cases)}, ensure_ascii=False))
        return 0

    os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_eval_")   # 评测用独立数据区，不污染真实记忆
    import resolve  # noqa: E402

    rows, lats = [], []
    gold_hits = gold_n = expl_hits = expl_n = abstain_ok = abstain_n = 0
    for c in cases:
        r = resolve.ask(c["problem"], need_type=c.get("need_type"), limit=args.limit)
        ans = " ".join(str(p.get("answer") or "") for p in r.get("possibilities") or [])
        kw = [k for k in (c.get("keywords") or []) if k in ans]
        exp = c.get("expect_resolved")
        # 三档分开统计：gold=存在性确定的术语/口径题（判据）；explore=探索题（信息项，不作判据）；
        # abstain=拒答题（判据）——避免把"探索题的关键词猜测"混进金标 Recall 造成失真
        tier = "gold" if (exp is True and c.get("keywords")) else ("abstain" if exp is False else "explore")
        if tier == "gold":
            gold_n += 1
            gold_hits += int(bool(kw))
        elif tier == "explore":
            expl_n += 1
            expl_hits += int(bool(kw))
        else:
            abstain_n += 1
            abstain_ok += int(not r["ok"])
        lats.append(r["elapsed_ms"])
        rows.append({"id": c["id"], "tier": tier, "ok": r["ok"], "kw": kw, "expected": exp,
                     "level_ok": (exp is None) or (bool(r["ok"]) == bool(exp)),
                     "ms": r["elapsed_ms"], "via": (r.get("best") or {}).get("source", "")})
    lats.sort()
    p50 = lats[len(lats) // 2] if lats else 0
    p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))] if lats else 0
    out = {"ok": True, "cases": len(cases),
           "gold_recall（存在性确定的术语/口径题）": round(gold_hits / gold_n, 4) if gold_n else None,
           "gold_cases": gold_n,
           "explore_hits（探索题命中；信息项，不作判据）": "%d/%d" % (expl_hits, expl_n),
           "拒答正确率": round(abstain_ok / abstain_n, 4) if abstain_n else None,
           "level_ok_rate": round(sum(1 for r in rows if r["level_ok"]) / len(rows), 4),
           "latency_ms": {"p50": p50, "p95": p95, "max": max(lats) if lats else 0},
           "rows": rows}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
