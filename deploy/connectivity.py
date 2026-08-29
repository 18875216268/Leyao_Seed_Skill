"""GitHub 连通性兜底（只读消费端专用）。

设计约束：
- 仅用于拉取更新，绝不推送、绝不改写系统 hosts。
- 默认走用户自建腾讯云函数的 ziyou 源：函数侧自建 DNS+TCP 探测选最快可达 IP，
  不依赖任何第三方 hosts 镜像，无 MITM 面。
- 作用域限定在单次 curl 调用：通过 --resolve 把 github 域名钉到云函数返回的 IP，
  直连拉取（SNI 保留、TLS 证书照常校验）。进程结束即失效，无系统级副作用。
"""

import os
import re
import subprocess
import tarfile

DEFAULT_ACCELERATOR = "https://1317825751-jonkwhxmyb.ap-guangzhou.tencentscf.com"
_HOST_LINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)\s*$")
_SLUG = re.compile(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", re.IGNORECASE)


def _no_proxy_env():
    env = dict(os.environ)
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        env.pop(key, None)
    return env


def fetch_hosts(base_url=DEFAULT_ACCELERATOR, source="ziyou", timeout=10, retries=2):
    """向云函数取可达 GitHub IP 映射 {domain: ip}。失败返回空 dict，绝不抛异常。"""
    url = base_url.rstrip("/") + "/?source=" + source
    for _ in range(retries + 1):
        try:
            proc = subprocess.run(
                ["curl", "-sS", "-m", str(timeout), url],
                capture_output=True, text=True, env=_no_proxy_env(),
            )
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        if proc.returncode == 0 and proc.stdout.strip():
            return _parse_hosts(proc.stdout)
    return {}


def _parse_hosts(text):
    hosts = {}
    for line in text.splitlines():
        m = _HOST_LINE.match(line.strip())
        if m:
            hosts[m.group(2)] = m.group(1)
    return hosts


def resolve_args(hosts, domains=None):
    """构造 curl --resolve 参数列表，把指定域名钉到 IP（保留 443/SNI）。"""
    sel = hosts if domains is None else {d: hosts[d] for d in domains if d in hosts}
    args = []
    for domain, ip in sel.items():
        args += ["--resolve", "{}:443:{}".format(domain, ip)]
    return args


def _repo_slug(remote_url):
    m = _SLUG.search(remote_url or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def pull_via_api(root, remote_url, branch, hosts, timeout=90):
    """经云函数 IP 直连下载仓库 tarball 并覆盖工作树。返回 (ok, message)。"""
    owner, repo = _repo_slug(remote_url)
    if not owner or not repo:
        return False, "无法从 remote_url 解析仓库标识"
    tarball_url = "https://codeload.github.com/{}/{}/tar.gz/refs/heads/{}".format(owner, repo, branch)
    cache_dir = os.path.join(root, "state", ".pull_cache")
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return False, "缓存目录不可写"
    tmp_tar = os.path.join(cache_dir, "{}-{}.tar.gz".format(repo, branch))
    rargs = resolve_args(hosts, ["codeload.github.com", "github.com", "objects.githubusercontent.com"])
    proc = subprocess.run(
        ["curl", "-sSL", "-m", str(timeout)] + rargs + ["-o", tmp_tar, tarball_url],
        capture_output=True, text=True, env=_no_proxy_env(),
    )
    if proc.returncode != 0 or not os.path.exists(tmp_tar) or os.path.getsize(tmp_tar) == 0:
        return False, (proc.stderr.strip() or "tarball 下载失败")
    try:
        ok, msg = _extract_tarball(tmp_tar, root)
    finally:
        try:
            os.remove(tmp_tar)
        except OSError:
            pass
    return ok, msg


def _extract_tarball(tmp_tar, root):
    try:
        with tarfile.open(tmp_tar, "r:gz") as tf:
            top = None
            for m in tf.getmembers():
                name = m.name[2:] if m.name.startswith("./") else m.name
                top = name.split("/")[0]
                break
            if not top:
                return False, "tarball 为空"
            for m in tf.getmembers():
                name = m.name[2:] if m.name.startswith("./") else m.name
                if name == top or name == top + "/":
                    continue
                rel = name[len(top) + 1:] if name.startswith(top + "/") else name
                if not rel:
                    continue
                dest = os.path.join(root, rel)
                if m.isdir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with open(dest, "wb") as out:
                    out.write(src.read())
    except (tarfile.TarError, OSError) as e:
        return False, "解包失败: " + str(e)
    return True, "api 直连拉取已应用"
