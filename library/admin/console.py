#!/usr/bin/env python3
"""资产管理台唯一启动入口：起后端 + 打开浏览器。零依赖。

用法（在 leyao-seed-core/ 目录下执行）：
    python library/admin/console.py                # 起后端并自动打开浏览器
    python library/admin/console.py --no-browser   # 只起后端（脚本 / 无界面环境）
"""
import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.dont_write_bytecode = True          # 运行期零写包（不在包内生成 __pycache__）

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402


def _port_in_use(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="资产管理台（可视化）")
    ap.add_argument("--no-browser", action="store_true", help="只起后端，不打开浏览器")
    args = ap.parse_args()

    port = server.PORT
    if _port_in_use(port):
        print(f"[admin] 端口 {port} 已被占用（管理台可能已在运行）。")
        print("[admin] 请先关闭旧的黑色 python 窗口，再重新启动。")
        sys.exit(1)
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(1)
    url = f"http://127.0.0.1:{port}/"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"[admin] 管理台已启动：{url}")
    print("[admin] 关闭本窗口或按 Ctrl+C 退出。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[admin] 已退出。")


if __name__ == "__main__":
    main()
