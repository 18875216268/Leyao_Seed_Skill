#!/usr/bin/env python3
"""反馈闭环：ask-log → 采纳/否决 → 晋升/存疑；reflect 产出带证据的洞察。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_test_fb_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import cache  # noqa: E402
import feedback  # noqa: E402
import memory  # noqa: E402
import query_norm  # noqa: E402
import reflect  # noqa: E402
import resolve  # noqa: E402
import source_loader  # noqa: E402


def _ask(problem: str) -> dict:
    return resolve.ask(problem, need_type="caliber", no_cache=True)


def _mock_pool(items):
    source_loader.pool_search = lambda *a, **k: {"ok": True, "items": items, "ms": 3}


class TestFeedback(unittest.TestCase):
    def test_unknown_query_id(self):
        r = feedback.submit("q_none", "adopt")
        self.assertFalse(r["ok"])

    def test_adopt_promotes_to_semantic(self):
        _mock_pool([{"answer": "口径：X=Y/Z", "title": "X", "source": "pool", "trust": "authority",
                     "confidence": 0.9, "version": 1, "freshness": 1, "evidence": ["pool#x"],
                     "tags": ["caliber"], "score": 0.9}])
        r = _ask("X 口径怎么算")
        self.assertTrue(r["ok"])
        qid = r["query_id"]
        for _ in range(3):
            got = feedback.submit(qid, "adopt", "确认无误")
        self.assertTrue(got["ok"])
        mems = [m for m in memory.all_active() if m.get("adopt", 0) >= 3]
        self.assertTrue(mems, "adopt≥3 应写入本地记忆计数")
        self.assertEqual(mems[0].get("tier"), "semantic", "adopt 达阈值应晋升 candidate→semantic")

    def test_reject_increments_fail(self):
        _mock_pool([{"answer": "口径：A=B+C", "title": "A", "source": "pool", "trust": "authority",
                     "confidence": 0.9, "version": 1, "freshness": 1, "evidence": ["pool#y"],
                     "tags": ["caliber"], "score": 0.9}])
        r = _ask("A 口径怎么算")
        feedback.submit(r["query_id"], "reject", "口径已过期")
        mems = [m for m in memory.all_active() if m.get("fail", 0) >= 1]
        self.assertTrue(mems, "否决应累计 fail")

    def test_reject_invalidates_cache(self):
        _mock_pool([{"answer": "口径：C=D+E", "title": "C", "source": "pool", "trust": "authority",
                     "confidence": 0.9, "version": 1, "freshness": 1, "evidence": ["pool#z"],
                     "tags": ["caliber"], "score": 0.9}])
        r = _ask("C 口径怎么算")
        norm = query_norm.normalize("C 口径怎么算")["norm"]
        self.assertTrue(cache.get(norm, "caliber").get("entry"), "ask 命中后应写入缓存")
        out = feedback.submit(r["query_id"], "reject", "口径已过期")
        self.assertTrue(out["cache_invalidated"]["exact"] >= 1, "拒答应删精确缓存键")
        self.assertFalse(cache.get(norm, "caliber").get("entry"), "拒答后缓存不得再命中（防错答复利）")

    def test_reflect_reports_miss_hotspot(self):
        _mock_pool([])
        source_loader.leyou_search = lambda *a, **k: {"ok": False, "items": [], "ms": 0,
                                                      "reason": "LOGIN_REQUIRED"}
        for _ in range(2):
            _ask("查不到的主题ZZZ")
        out = reflect.reflect(window=50)
        kinds = {i["kind"] for i in out["insights"]}
        self.assertIn("miss_hotspot", kinds, "反复未命中应产出 miss_hotspot 洞察")
        hit = [i for i in out["insights"] if i["kind"] == "miss_hotspot"][0]
        self.assertTrue(hit["evidence"], "洞察必须带证据指针（query_id）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
