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

    def test_card_decoupled_from_framework(self):
        """卡子系统零框架依赖（解耦护栏）：源码不得引用框架件。"""
        src = (SKILL / "scripts" / "card.py").read_text(encoding="utf-8")
        for bad in ("import engine", "from engine", "evolution", "routes.json", "ROUTES.md", "processor/"):
            self.assertNotIn(bad, src, "card.py 不得引用框架件：%s" % bad)

    def test_home_is_env_bound(self):
        """LEYAO_KB_HOME 被真正采纳（防解析失效静默回落到 ~/.leyao-kb）。"""
        self.assertEqual(common.HOME, Path(os.environ["LEYAO_KB_HOME"]).resolve())

    def test_card_cli_status_offline(self):
        """card.py status 离线可用：新用户区必然 state=missing；且**运行期零写包**（含不落 __pycache__）。"""
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}   # 故意去掉：验证脚本自带零写包守卫
        p = subprocess.run([sys.executable, str(SKILL / "scripts" / "card.py"), "status"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=60)
        self.assertEqual(p.returncode, 0, (p.stderr or "")[:200])
        self.assertIn('"state": "missing"', p.stdout)
        for name in ("card.json", "card.md", "card.candidates.json", "card.meta.json"):
            self.assertFalse((SKILL / name).exists(), "卡产物不得落包内：%s" % name)
        self.assertEqual(list((SKILL / "scripts").glob("__pycache__/card.*")), [],
                         "card.py 运行期不得在包内生成字节码")


if __name__ == "__main__":
    unittest.main(verbosity=2)
