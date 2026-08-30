"""测试共用夹具：临时目录辅助区。

为什么自建辅助区而不用系统 temp：
    部分测试要建 git 仓库并断言 NOT_A_REPO，把落点放在套件父目录可以
    (a) 避开沙箱对系统临时区的拦截，(b) 避免 temp 目录被套件自身的 git
    上下文污染导致 NOT_A_REPO 断言失效。

为什么必须由本模块统一提供：
    原本 6 个测试文件各自重复着同样的两行设置，且写法不一
    （`tempfile.tempdir = ...` 与 `import tempfile as _tf; _tf.tempdir = ...`）。
    任何一处漏改都会让临时目录悄悄落回系统 temp，断言只在部分机器上成立。

为什么要顺带回收陈旧目录：
    辅助区只增不减。测试正常结束时 finally 会 rmtree，但进程被杀 / 崩溃 /
    被宿主环境的删除保护拦下时目录就留下了。累积到几百个后，宿主环境的批量
    删除阈值会直接把测试卡死——「全绿」这个验收门槛变得不可复现。
    回收是尽力而为：单个目录删除失败不影响本次测试。
"""

import os
import shutil
import tempfile
import time

# 只回收本辅助区自己产出的前缀，绝不碰同目录下的其它东西。
OWN_PREFIXES = ("skill-", "srs-")
MAX_AGE_SECONDS = 24 * 3600


def setup(root=None):
    """把 tempfile 的落点切到套件同级的 .suite_test_tmp，并顺带回收陈旧目录。"""
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.join(os.path.dirname(root), ".suite_test_tmp")
    os.makedirs(base, exist_ok=True)
    sweep(base)
    tempfile.tempdir = base
    return base


def sweep(base, now=None):
    """尽力回收超过 MAX_AGE_SECONDS、且前缀属于本辅助区的目录。不抛异常。"""
    now = time.time() if now is None else now
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    reclaimed = 0
    for name in names:
        if not name.startswith(OWN_PREFIXES):
            continue
        path = os.path.join(base, name)
        if not os.path.isdir(path):
            continue
        try:
            if now - os.path.getmtime(path) <= MAX_AGE_SECONDS:
                continue
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                reclaimed += 1
        except OSError:
            pass
    return reclaimed
