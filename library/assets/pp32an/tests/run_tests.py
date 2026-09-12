#!/usr/bin/env python3
"""离线测试入口：跑 tests/ 下全部单测（零网络），输出逐文件结果 + 汇总 JSON。

用法：python tests/run_tests.py
退出码：0 全过 / 1 有失败。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
FILES = sorted(p.name for p in HERE.glob("test_*.py"))

env = {**__import__("os").environ, "LEYAO_KB_HOME": tempfile.mkdtemp(prefix="kb_run_"),
       "PYTHONDONTWRITEBYTECODE": "1",         # 测试也不许在包里落字节码
       "PYTHONIOENCODING": "utf-8"}            # 子进程输出统一 UTF-8（避免控制台乱码）
results, failed = [], []
for f in FILES:
    p = subprocess.run([sys.executable, str(HERE / f)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=300)
    ok = p.returncode == 0
    if ok:
        results.append(f)
    else:
        failed.append(f)
    print("%s  %s" % ("PASS" if ok else "FAIL", f))
    if not ok:
        print((p.stderr or p.stdout or "")[-600:])
summary = {"ok": not failed, "files": len(FILES), "passed": len(FILES) - len(failed), "failed": failed}
print(json.dumps(summary, ensure_ascii=False))
sys.exit(0 if summary["ok"] else 1)
