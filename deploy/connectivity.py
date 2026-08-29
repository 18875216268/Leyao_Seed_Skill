"""GitHub 连通性兜底（只读消费端专用）。

职责边界（用户定义）：
- 云函数【只提供可达的 GitHub IP】（hosts 行），不负责拉仓库。
- 拉取仍由本地 git 完成；本层只是把云函数给的 IP 应用到 git 的网络路径上，使其能正确连到 github。
- 作用域限定在单次 git 调用的本地 CONNECT 代理：把 github 相关域名钉到云函数返回的 IP。
- 流程三步：云函数全员返回 → 本地 TCP 443 自测 → 按延迟排序 → 取最快可达者使用。简单清晰。
"""

import os
import re
import socket
import subprocess
import threading
import concurrent.futures
import time

DEFAULT_ACCELERATOR = "https://1317825751-jonkwhxmyb.ap-guangzhou.tencentscf.com"
_HOST_LINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)\s*$")


def fetch_hosts(base_url=DEFAULT_ACCELERATOR, source="all", timeout=10, retries=2):
    """向云函数取可达 GitHub IP 映射 {domain: ip}。失败返回空 dict，绝不抛异常。

    source 默认 'all'：合并 ziyou（函数自建 DNS+TCP 探测）与第三方镜像（gitcdn/gitee），
    由云函数侧去重、测速、每域名取最快可达。证书校验始终开启，投毒 IP 不会通过 TLS。
    """
    url = base_url.rstrip("/") + "/?source=" + source
    for _ in range(retries + 1):
        try:
            proc = subprocess.run(["curl", "-sS", "-m", str(timeout), url], capture_output=True, text=True)
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


def _probe_latency(ip, timeout=2.5):
    t0 = time.monotonic()
    try:
        s = socket.create_connection((ip, 443), timeout)
        s.close()
        return time.monotonic() - t0
    except Exception:
        return None


def select_usable(hosts, timeout=2.5):
    """云函数全员返回后，本地三步：测试 → 排序 → 按序使用。

    对每个域名并行 TCP 443 测延迟，取最快可达者钉定；全不可达则回退信任云函数 IP（让 git 仍尝试）。
    """
    out = dict(hosts)
    best = {}

    def probe(item):
        domain, ip = item
        return domain, ip, _probe_latency(ip, timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as ex:
        for domain, ip, lat in ex.map(probe, list(hosts.items())):
            if lat is not None and (domain not in best or lat < best[domain][1]):
                best[domain] = (ip, lat)
    for domain, (ip, _) in best.items():
        out[domain] = ip
    return out


class IPProxy:
    """本地 CONNECT 代理：把 github 相关域名钉到云函数返回的 IP，其余走正常 DNS。

    作用域 = 单次拉取：随 git 子进程起停，不写系统 hosts、无需管理员、进程结束即失效。
    """

    def __init__(self, hosts, port=0):
        self.hosts = hosts
        self.port = port
        self._srv = None
        self._thread = None

    def _resolve(self, host):
        for domain, ip in self.hosts.items():
            if host == domain or host.endswith("." + domain):
                return ip
        return None

    def _handle(self, conn):
        try:
            conn.settimeout(15)
            req = conn.recv(65536)
            if not req.startswith(b"CONNECT"):
                conn.close()
                return
            first = req.split(b"\r\n", 1)[0].decode()
            _, target, _ = first.split()
            host, port = target.rsplit(":", 1)
            port = int(port)
            ip = self._resolve(host)
            try:
                dest = socket.create_connection((ip, port) if ip else (host, port), timeout=12)
            except Exception:
                conn.close()
                return
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")

            def pipe(a, b):
                try:
                    while True:
                        data = a.recv(65536)
                        if not data:
                            break
                        b.sendall(data)
                except Exception:
                    pass
                finally:
                    try:
                        a.close()
                    except Exception:
                        pass
                    try:
                        b.close()
                    except Exception:
                        pass

            threading.Thread(target=pipe, args=(conn, dest), daemon=True).start()
            threading.Thread(target=pipe, args=(dest, conn), daemon=True).start()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.port))
        self._srv.listen(64)
        self.port = self._srv.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.port

    def _serve(self):
        while True:
            try:
                conn, _ = self._srv.accept()
            except Exception:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def stop(self):
        try:
            self._srv.close()
        except Exception:
            pass


def run_git_with_hosts(root, args, hosts, timeout=120):
    """在云函数 IP 钉定下执行 git 命令（args 不含 'git'）。返回 subprocess.CompletedProcess。

    仅对 github 相关域名钉 IP；系统代理被清空，改走本机代理（直连钉定 IP）。
    """
    proxy = IPProxy(hosts)
    port = proxy.start()
    try:
        env = dict(os.environ)
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        env.pop("HTTP_PROXY", None)
        env.pop("HTTPS_PROXY", None)
        return subprocess.run(
            ["git", "-c", "http.proxy=http://127.0.0.1:%d" % port] + list(args),
            cwd=root, capture_output=True, text=True, env=env, timeout=timeout,
        )
    finally:
        proxy.stop()
