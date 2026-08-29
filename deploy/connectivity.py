"""GitHub 连通性兜底（只读消费端专用）。

职责边界（用户定义）：
- 云函数【只提供可达的 GitHub IP】（hosts 行），不负责拉仓库。
- 拉取仍由本地 git 完成；本层只是把云函数给的 IP 应用到 git 的网络路径上，使其能正确连到 github。
- 作用域限定在单次 git 调用的本地 CONNECT 代理：把 github 相关域名钉到云函数返回的 IP，
  SNI/TLS 照常校验（默认不关 sslVerify，第三方源 IP 若被投毒会因证书不匹配而失败，不会泄露凭据）。
"""

import os
import re
import socket
import subprocess
import threading

DEFAULT_ACCELERATOR = "https://1317825751-jonkwhxmyb.ap-guangzhou.tencentscf.com"
_HOST_LINE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)\s*$")
_SCOPES = ("github.com", "githubusercontent.com", "githubassets.com",
           "fastly.net", "githubapp.com", "amazonaws.com")


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


def _probe_ip(ip, timeout=2.5):
    try:
        s = socket.create_connection((ip, 443), timeout)
        s.close()
        return True
    except Exception:
        return False


# 本地需钉定的关键域名：git/clone 实际会连的。其余域名走代理正常 DNS 即可。
_CRITICAL_DOMAINS = ("github.com", "api.github.com", "codeload.github.com",
                     "raw.githubusercontent.com", "github.githubassets.com",
                     "objects.githubusercontent.com")


def select_usable(hosts, critical=_CRITICAL_DOMAINS, timeout=2.5):
    """本地自测 + 排序 + 选优：云函数给的是腾讯云视角的"最快"，本机网络未必认。

    对关键域名，除云函数给的 IP 外，再补一次本地 DNS 解析拿更多候选，并行 TCP 443 探测，
    挑本机真正可达者钉定；全不可达时回退信任云函数 IP（让 git 仍尝试）。非关键域名直接沿用云函数 IP。
    """
    import concurrent.futures

    cand = {}
    for domain, ip in hosts.items():
        lst = [ip]
        if domain in critical:
            try:
                for r in socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP):
                    a = r[4][0]
                    if a not in lst:
                        lst.append(a)
            except Exception:
                pass
        cand[domain] = lst

    def pick(domain):
        for ip in cand[domain]:
            if _probe_ip(ip, timeout):
                return domain, ip
        return domain, hosts.get(domain)

    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as ex:
        for domain, ip in ex.map(pick, list(cand.keys())):
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
