#!/usr/bin/env python3
"""失败不静默：源报错→path 带 error；云智库未登录→LOGIN_REQUIRED + 建议；预算不足时不再尝试后续层。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_test_budget_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import resolve  # noqa: E402
import source_loader  # noqa: E402


class TestBudgetAndFailure(unittest.TestCase):
    def setUp(self):
        source_loader.pool_search = lambda *a, **k: {"ok": False, "items": [], "ms": 6000,
                                                    "error": "URLError: timed out"}
        source_loader.leyou_search = lambda *a, **k: {"ok": False, "items": [], "ms": 0,
                                                      "reason": "LOGIN_REQUIRED",
                                                      "error": "未登录", "next": "在云智库目录手动登录"}

    def test_all_failed_not_silent(self):
        r = resolve.ask("一个查不到的问题", need_type="policy", no_cache=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "no_match")
        pool = [l for l in r["path"] if l["layer"] == "pool"][0]
        ley = [l for l in r["path"] if l["layer"] == "leyou"][0]
        self.assertTrue(pool.get("error"), "公共池失败必须在 path 中带 error")
        self.assertEqual(ley.get("reason"), "LOGIN_REQUIRED", "云智库未登录必须如实标注")
        self.assertTrue(any(("手动" in s or "人工" in s) and "登录" in s for s in r["suggestions"]),
                        "必须给出手动/人工登录指引")
        self.assertTrue(r["suggestions"], "未命中必须给建议（不静默失败）")

    def test_login_required_no_popup_contract(self):
        """契约：本 skill 不提供扫码入口（文案只指向人工手动登录）。"""
        r = resolve.ask("制度怎么规定", need_type="policy", no_cache=True)
        text = " ".join(r["suggestions"])
        self.assertIn("手动", text)
        self.assertNotIn("--scan", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
