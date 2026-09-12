"""数据区约束（回归护栏）：配置与登录态只落用户区；桥接必须显式指定 token-file。

背景：客户端默认把登录态写在**包内**同目录 ✗ → 本 skill 统一改由全局参数 `--token-file`
指向用户数据区；本测试锁住"包内零凭据落点"这条不变量（任何路径调整若破坏它，立刻红）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["LEYAO_KB_HOME"] = tempfile.mkdtemp(prefix="kb_data_area_")
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(SKILL / "scripts" / "sources"))

import common  # noqa: E402
import leyou_bridge  # noqa: E402


class TestDataArea(unittest.TestCase):
    def test_paths_outside_package(self):
        for f in (common.CONFIG_F, common.LEYOU_TOKEN_F):
            self.assertNotIn(SKILL, f.parents, "%s 不得落在包内" % f)
            self.assertEqual(f.parent, common.HOME)

    def test_client_token_target(self):
        sys.path.insert(0, str(SKILL / "scripts" / "sources" / "leyou"))
        import leyou_firebase_login as fb
        self.assertEqual(fb.TOKEN_FILE, common.LEYOU_TOKEN_F)

    def test_bridge_passes_token_file_before_subcmd(self):
        captured = {}

        class _R:
            returncode = 0
            stdout = '{"ok": true, "logged_in": false}'
            stderr = ""

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return _R()

        orig = leyou_bridge.subprocess.run
        leyou_bridge.subprocess.run = fake_run
        try:
            leyou_bridge.status()
        finally:
            leyou_bridge.subprocess.run = orig
        argv = captured["argv"]
        i = argv.index("--token-file")
        self.assertEqual(argv[i + 1], str(common.LEYOU_TOKEN_F))
        self.assertLess(i, argv.index("status"), "--token-file 须为子命令前的全局参数")


if __name__ == "__main__":
    unittest.main(verbosity=2)
