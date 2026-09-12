"""沉淀写路径单测（零网络）：payload 映射 / 本地质量闸 / dry-run 零写 / 采纳上报静默 / 口径限定注入库。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_write_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import contribute  # noqa: E402
import feedback  # noqa: E402
import memory  # noqa: E402
import resolve  # noqa: E402
from sources import pool  # noqa: E402


class TestPayloadAndGate(unittest.TestCase):
    def test_payload_mapping_caliber(self):
        p = contribute.to_payload({"q": "成本优势率怎么算", "a": "低于合理P4*0.99购进的订单金额/总订单金额（更新版）",
                                   "need_type": "caliber", "adopt": 3, "fail": 0})
        self.assertEqual(p["category"], "caliber")
        self.assertEqual(p["distill_type"], "lesson")
        self.assertEqual(p["trust"], "reference")
        self.assertEqual(p["kind"], "fact")
        self.assertGreaterEqual(p["quality_score"], 0.5)

    def test_payload_mapping_search_to_procedure(self):
        p = contribute.to_payload({"q": "怎么做周报", "a": "1. 取数 → 2. 清洗 → 3. 填充（步骤齐全可执行）",
                                   "need_type": "search", "adopt": 0, "fail": 0})
        self.assertEqual(p["distill_type"], "workflow")
        self.assertEqual(p["kind"], "procedure")
        self.assertEqual(p["category"], "experience")

    def test_gate_rejects(self):
        good = {"tier": "semantic", "fail": 0, "a": "x" * 40, "status": "active"}
        self.assertTrue(contribute.gate(good)[0])
        self.assertFalse(contribute.gate({**good, "tier": "candidate"})[0])
        self.assertFalse(contribute.gate({**good, "fail": 1})[0])
        self.assertFalse(contribute.gate({**good, "a": "太短"})[0])
        self.assertFalse(contribute.gate({**good, "status": "cold"})[0])


class TestSubmitPaths(unittest.TestCase):
    def _semantic_memory(self):
        m = memory.add("成本优势率怎么算（写测）", "低于合理P4*0.99购进的订单金额/总订单金额口径说明（写测）", "caliber",
                       source="pool", trust="authority")
        for _ in range(3):
            memory.record_feedback(m["id"], "adopt")
        return m["id"]

    def test_dry_run_zero_write(self):
        mid = self._semantic_memory()
        with mock.patch.object(pool, "_post") as mpost:
            out = contribute.submit_memory(mid, dry_run=True)
        self.assertTrue(out["ok"] and out["dry_run"])
        mpost.assert_not_called()
        self.assertEqual(out["payload"]["category"], "caliber")

    def test_submit_uses_token_and_protocol(self):
        mid = self._semantic_memory()
        with mock.patch.object(pool, "_post", return_value={"ok": True, "id": "p-9"}) as mpost:
            out = contribute.submit_memory(mid)
        self.assertTrue(out["ok"])
        mpost.assert_called_once()
        self.assertEqual(mpost.call_args.args[1], "")             # 写池路径 = POST /
        self.assertEqual(mpost.call_args.kwargs["token"], "brf-pool-2026-shared")
        body = mpost.call_args.args[2]
        for k in ("title", "content", "category", "distill_type", "trust", "quality_score",
                  "contributor", "kind"):
            self.assertIn(k, body)

    def test_inject_dry_run(self):
        with mock.patch.object(pool, "_post") as mpost:
            out = contribute.inject("标题", "内容内容内容内容内容内容内容内容内容", dry_run=True)
        self.assertTrue(out["ok"] and out["dry_run"])
        mpost.assert_not_called()


class TestAdoptReporting(unittest.TestCase):
    def test_adopt_reports_pool_id(self):
        feedback.log_ask("q_write_1", "成本优势率怎么算", "caliber", True, ["pool"], [], pool_id="p-123")
        with mock.patch.object(pool, "_post", return_value={"ok": True}) as mpost:
            out = feedback.submit("q_write_1", "adopt")
        self.assertTrue(out["ok"])
        self.assertTrue(out["adopt_reported"]["ok"])
        self.assertEqual(mpost.call_args.args[1], "/adopt")
        self.assertEqual(mpost.call_args.args[2]["id"], "p-123")

    def test_adopt_report_failure_silent(self):
        feedback.log_ask("q_write_2", "成本优势率怎么算", "caliber", True, ["pool"], [], pool_id="p-124")
        with mock.patch.object(pool, "_post", side_effect=RuntimeError("boom")):
            out = feedback.submit("q_write_2", "adopt")
        self.assertTrue(out["ok"], "价值信号失败不得阻塞反馈")
        self.assertFalse(out["adopt_reported"]["ok"])


class TestCaliberTierAndKind(unittest.TestCase):
    def _ask(self, problem, need_type, **kw):
        with mock.patch.object(resolve.source_loader, "pool_search",
                               return_value={"items": [], "ms": 1, "tried": []}) as mps, \
             mock.patch.object(resolve.source_loader, "leyou_search",
                               return_value={"items": [], "ms": 1, "reason": "LOGIN_REQUIRED"}):
            resolve.ask(problem, need_type=need_type, **kw)
        return mps

    def test_caliber_only_inject_tier(self):
        mps = self._ask("成本优势率怎么算", "caliber")
        self.assertEqual(mps.call_args.args[4], "inject")

    def test_term_no_tier(self):
        mps = self._ask("缺货率是什么", "term")
        self.assertIsNone(mps.call_args.args[4])

    def test_search_kind_passthrough(self):
        mps = self._ask("怎么做周报", "search", kind="procedure")
        self.assertEqual(mps.call_args.args[6], "procedure")


if __name__ == "__main__":
    unittest.main(verbosity=2)
