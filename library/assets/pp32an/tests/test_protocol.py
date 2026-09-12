#!/usr/bin/env python3
"""协议 1.0：字段齐备与类型稳定（成功 / 未命中两种形态）+ CLI 离线可用（status / --help）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_test_protocol_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import resolve  # noqa: E402
import source_loader  # noqa: E402

SUCCESS_KEYS = ("ok", "plugin", "protocol", "problem", "need_type", "answer", "best",
                "possibilities", "path", "resolved", "early_stop", "elapsed_ms", "query_id",
                "has_more", "next_offset", "total_count", "suggestions")
FAIL_KEYS = ("ok", "problem", "need_type", "possibilities", "path", "resolved", "reason",
             "suggestions", "query_id", "elapsed_ms")


class TestProtocol(unittest.TestCase):
    def test_success_shape(self):
        source_loader.pool_search = lambda *a, **k: {
            "ok": True, "ms": 5, "items": [{"answer": "口径：……", "title": "成本优势率",
                                            "source": "pool", "trust": "authority", "confidence": 0.9,
                                            "version": 2, "freshness": 1, "evidence": ["pool#a"],
                                            "tags": ["caliber"], "score": 0.9}]}
        r = resolve.ask("成本优势率怎么算", no_cache=True)
        for k in SUCCESS_KEYS:
            self.assertIn(k, r, "缺字段 %s" % k)
        self.assertIsInstance(r["possibilities"], list)
        self.assertIsInstance(r["resolved"], bool)
        self.assertIsInstance(r["elapsed_ms"], int)
        self.assertTrue(r["query_id"].startswith("q_"))
        self.assertLessEqual(len(r["best"].get("answer", "")), 300, "默认应返回摘要（--full 才展开）")

    def test_fail_shape(self):
        source_loader.pool_search = lambda *a, **k: {"ok": True, "items": [], "ms": 3}
        source_loader.leyou_search = lambda *a, **k: {"ok": False, "items": [], "ms": 0,
                                                      "reason": "LOGIN_REQUIRED"}
        r = resolve.ask("不存在的知识xxyy", no_cache=True)
        for k in FAIL_KEYS:
            self.assertIn(k, r, "缺字段 %s" % k)
        self.assertFalse(r["ok"])
        self.assertEqual(r["possibilities"], [])

    def test_cli_offline(self):
        for args in (["status"], ["--help"]):
            p = subprocess.run([sys.executable, str(SKILL / "scripts" / "hub.py"), *args],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ}, timeout=60)
            self.assertEqual(p.returncode, 0, "CLI %s 失败：%s" % (args, (p.stderr or "")[:200]))
            if args == ["status"]:
                data = json.loads(p.stdout[p.stdout.find("{"): p.stdout.rfind("}") + 1])
                self.assertTrue(data["ok"])
                self.assertEqual(data["version"], "1.0.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
