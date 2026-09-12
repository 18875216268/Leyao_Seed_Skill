#!/usr/bin/env python3
"""优先链与早停（核心语义）：公共池命中 → 云智库必被 skipped；公共池空 → 云智库被调用；--expand 两库都取。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_test_priority_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import resolve  # noqa: E402
import source_loader  # noqa: E402

POOL_ITEM = {"answer": "缺货率：缺货品种数/考核品种数", "title": "术语：缺货率", "source": "pool",
             "trust": "authority", "confidence": 0.9, "version": 1, "freshness": 1,
             "evidence": ["pool#1"], "tags": ["term"], "score": 0.9}
LEYOU_ITEM = {"answer": "云智库：毛利分析课程要点……", "title": "毛利分析", "source": "leyou",
              "trust": "reference", "confidence": 0.6, "evidence": ["leyou#x"], "tags": [], "score": 0.0}
CALLS = {"pool": 0, "leyou": 0}


class TestPriority(unittest.TestCase):
    def setUp(self):
        CALLS["pool"] = CALLS["leyou"] = 0
        source_loader.pool_search = self._pool
        source_loader.leyou_search = self._leyou
        self.pool_result = {"ok": True, "items": [dict(POOL_ITEM)], "ms": 12}
        self.leyou_result = {"ok": True, "items": [dict(LEYOU_ITEM)], "ms": 30}

    def _pool(self, *a, **k):
        CALLS["pool"] += 1
        return dict(self.pool_result)

    def _leyou(self, *a, **k):
        CALLS["leyou"] += 1
        return dict(self.leyou_result)

    def test_pool_hit_skips_leyou(self):
        r = resolve.ask("缺货率是什么", need_type="term", no_cache=True)
        self.assertTrue(r["ok"])
        self.assertEqual(CALLS["leyou"], 0, "公共池命中时不得调用云智库（早停语义）")
        self.assertTrue(r["early_stop"])
        ley = [l for l in r["path"] if l["layer"] == "leyou"][0]
        self.assertTrue(ley.get("skipped"))

    def test_pool_empty_then_leyou(self):
        self.pool_result = {"ok": True, "items": [], "ms": 8}
        r = resolve.ask("毛利分析课程", need_type="course", no_cache=True)
        self.assertTrue(r["ok"])
        self.assertEqual(CALLS["leyou"], 1, "公共池空时必须走云智库兜底")
        self.assertEqual((r["best"] or {}).get("source"), "leyou")
        self.assertFalse(r["early_stop"])

    def test_expand_calls_both(self):
        r = resolve.ask("缺货率是什么", need_type="term", expand=True, no_cache=True)
        self.assertEqual(CALLS["leyou"], 1, "--expand 时不早停，两库都取")
        srcs = {p["source"] for p in r["possibilities"]}
        self.assertTrue({"pool", "leyou"} <= srcs, "两库结果都应出现（可另有本地记忆）")
        self.assertFalse(r["early_stop"])

    def test_exact_cache_hit_skips_sources(self):
        resolve.ask("缺货率是什么", need_type="term", no_cache=True)   # 先写缓存
        baseline = CALLS["pool"]
        r = resolve.ask("缺货率是什么！！", need_type="term")            # 规范化后同键
        self.assertTrue(r["ok"])
        self.assertEqual(CALLS["pool"], baseline, "精确缓存命中不得再打源")
        self.assertEqual(r["possibilities"][0].get("cache"), "exact")

    def test_no_cache_skips_cache_layer(self):
        resolve.ask("缺货率是什么", need_type="term", no_cache=True)
        r = resolve.ask("缺货率是什么", need_type="term", no_cache=True)
        self.assertFalse([l for l in r["path"] if l["layer"].startswith("cache")], "no-cache 时不应出现缓存层")


if __name__ == "__main__":
    unittest.main(verbosity=2)
