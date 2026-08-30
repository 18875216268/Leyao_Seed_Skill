"""原子写 JSON。

为什么需要它：
`open(path, "w")` 会**先截断文件再写入**。写到一半进程崩溃、被杀、或磁盘写满，
留下的就是半个 JSON——而 registry（路由表）与 manifest（完整性 pin）是唯一事实源，
一旦损坏整个套件不可用，且没有可回溯的副本。

原子写的做法（POSIX 与 Windows 通用的标准配方）：
  1. 先在**同目录**写临时文件（跨分区 rename 不原子，所以必须同目录）；
  2. flush + fsync，确保数据真的落盘而非停在页缓存；
  3. `os.replace(tmp, path)` 原子替换——读方只会看到旧值或新值，绝不会看到中间态。

额外收益：**先序列化再开写**。若 payload 里有不可序列化的对象，`json.dumps` 会在
碰触原文件之前就抛错，原文件毫发无损；而直接 `open(path,"w")` 会先把原文件截断成 0 字节。
"""

import json
import os
import tempfile

FILE_MODE = 0o644


def write_json(path, payload, indent=2, ensure_ascii=False):
    """原子地把 payload 写成 JSON。任一环节失败则原文件保持不变。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    # 先序列化：失败时原文件未被触碰（直接 open('w') 会先截断）
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)

    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".srs-tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, FILE_MODE)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # 目录项本身也要落盘，否则崩溃后 rename 可能丢失。Windows 上目录不可 fsync，忽略。
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, AttributeError):
        pass
    return path
