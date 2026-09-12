"""BI 登录薄封装：原样复用同目录 login_bi.py（绝不重写登录逻辑）。

本文件只做两件事，本 skill 内零登录实现、零认证硬编码：
1. 重导出 login_bi 的全部公开 API，供通道工具 / bi_common 使用；
2. 作为 CLI 运行时，透传 argv 给 login_bi 的 __main__ 入口
   （python bi_login.py --status / --reuse / --no-ui / --no-remote）。

登录逻辑 100% 来自同目录 login_bi.py，连接配置（BI 地址/企微 corpId/固定头）硬编码在
其顶部模块常量里——但**不含任何账号、密码、token**，凭证由企微扫码动态获取。
本 skill 不触碰、不复制任何认证信息。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 同目录导入兜底（作为模块被其它目录脚本导入时保证可见）
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from login_bi import (  # noqa: E402,F401  —— 原样复用，不重写
    relogin,
    verify_credential,
    get_credential,
    is_authenticated,
    login_and_store,
    list_accounts,
    get_account,
    get_all_accounts,
    verify_account,
    verify_all_accounts,
    add_account,
    update_account,
    delete_account,
    set_account_category,
    fetch_user_info,
    account_store,
    DEFAULT_CATEGORY,
    BiError,
    LoginFlow,
    build_credential,
    run_login_dialog,
    create_login_dialog,
)


if __name__ == "__main__":
    # 直接复用已导入的 login_bi 执行其 CLI（argparse 读 sys.argv，退出码随 main 返回）。
    # 不再用 runpy.run_path：那会把 login_bi.py 再执行一遍，造成模块状态重复加载。
    import login_bi

    raise SystemExit(login_bi.main())
