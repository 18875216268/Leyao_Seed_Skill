#!/usr/bin/env python3
"""记忆与三因子精排：相关性 / 近因（0.995^h 衰减）/ 重要性 各自生效；冷存不删除。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_test_mem_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import memory  # noqa: E402
import rank  # noqa: E402
from common import CN, MEMORY_F, append_jsonl  # noqa: E402


def put(mid, q, a, *, hours_ago=0.0, importance=5, need_type="caliber", status="active"):
    ts = (datetime.now(CN) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    append_jsonl(MEMORY_F, {"id": mid, "q": q, "a": a, "need_type": need_type,
                            "created_at": ts, "last_access_at": ts, "access_count": 0,
                            "importance": importance, "source": "", "trust": "",
                            "version": None, "freshness": None, "adopt": 0, "fail": 0,
                            "evidence": [], "tags": [], "tier": "candidate", "status": status})


class TestMemoryRank(unittest.TestCase):
    def test_relevance_dominates(self):
        put("m_rel", "成本优势率怎么算", "成本优势率=低于合理P4*0.99购进的订单金额/总订单金额")
        put("m_irr", "今天天气怎么样", "晴天")
        out = rank.score_memories(memory.all_active(), "成本优势率怎么算")
        self.assertEqual(out[0]["id"], "m_rel")

    def test_recency_effect(self):
        put("m_new", "毛利口径", "毛利=收入-成本", hours_ago=0)
        put("m_old", "毛利口径", "毛利=收入-成本", hours_ago=500)
        out = rank.score_memories(memory.all_active(), "毛利口径")
        self.assertEqual(out[0]["id"], "m_new", "同相关性下更近访问应排前")

    def test_importance_effect(self):
        put("m_hi", "缺货定义", "缺货=库存<起配量", hours_ago=0, importance=9)
        put("m_lo", "缺货定义", "缺货=库存<起配量", hours_ago=0, importance=1)
        out = rank.score_memories(memory.all_active(), "缺货定义")
        self.assertGreaterEqual(out[0]["score_parts"]["importance"], out[1]["score_parts"]["importance"])

    def test_cold_keeps_record(self):
        put("m_cold", "过时问题", "旧答案")
        memory.cold("m_cold")
        ids = [m["id"] for m in memory.all_active()]
        self.assertNotIn("m_cold", ids, "冷存后不应出现在 active")
        self.assertIsNotNone(memory.get("m_cold"), "冷存≠删除（记录仍在，可追溯）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
