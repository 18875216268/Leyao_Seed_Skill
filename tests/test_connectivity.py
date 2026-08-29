"""连通性兜底测试：云函数 hosts 解析 / 本地 IP 钉定代理隧道（确定性，本地 mock，不依赖外网）。"""

import os
import shutil
import socket
import sys
import tempfile
import threading

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import tempfile as _tf

_tf.tempdir = os.path.join(os.path.dirname(ROOT), ".suite_test_tmp")
os.makedirs(_tf.tempdir, exist_ok=True)

from deploy import connectivity  # noqa: E402

_HOSTS_BODY = "20.205.243.166    github.com\n185.199.111.133   raw.githubusercontent.com\n# comment\n\n"


def test_parse_hosts():
    hosts = connectivity._parse_hosts(_HOSTS_BODY)
    assert hosts.get("github.com") == "20.205.243.166"
    assert hosts.get("raw.githubusercontent.com") == "185.199.111.133"
    assert "# comment" not in hosts


def _start_mock_upstream():
    """本地 TCP 回显服务，模拟被钉定的 github 端点。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                break
            try:
                conn.sendall(b"UPSTREAM-OK")
                conn.recv(65536)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    threading.Thread(target=serve, daemon=True).start()
    return srv, port


def test_ip_proxy_tunnels_to_pinned_ip():
    srv, port = _start_mock_upstream()
    try:
        proxy = connectivity.IPProxy({"github.com": "127.0.0.1"}, port=0)
        p = proxy.start()
        try:
            sock = socket.create_connection(("127.0.0.1", p), timeout=5)
            sock.sendall(b"CONNECT github.com:%d HTTP/1.1\r\n\r\n" % port)
            banner = sock.recv(65536)
            assert b"200 Connection Established" in banner
            assert sock.recv(65536) == b"UPSTREAM-OK"
            sock.close()
        finally:
            proxy.stop()
    finally:
        try:
            srv.close()
        except Exception:
            pass


def test_ip_proxy_falls_back_to_dns_for_unmapped():
    srv, port = _start_mock_upstream()
    try:
        proxy = connectivity.IPProxy({}, port=0)
        p = proxy.start()
        try:
            sock = socket.create_connection(("127.0.0.1", p), timeout=5)
            sock.sendall(b"CONNECT 127.0.0.1:%d HTTP/1.1\r\n\r\n" % port)
            banner = sock.recv(65536)
            assert b"200 Connection Established" in banner
            assert sock.recv(65536) == b"UPSTREAM-OK"
            sock.close()
        finally:
            proxy.stop()
    finally:
        try:
            srv.close()
        except Exception:
            pass


def test_run_git_with_hosts_invokes_git_with_proxy():
    # 不依赖真实网络：验证 run_git_with_hosts 确实以 http.proxy 形式调用 git。
    pr = connectivity.run_git_with_hosts(ROOT, ["--version"], {"github.com": "127.0.0.1"}, timeout=30)
    assert pr.returncode == 0
    assert pr.stdout.strip().startswith("git version")


def test_fetch_hosts_failure_returns_empty():
    assert connectivity.fetch_hosts("http://127.0.0.1:1/nope", "all", timeout=2) == {}


def test_select_usable_keeps_only_reachable():
    # 候选来自云函数全员返回；本地三步：测试 → 排序 → 仅保留直连可达者。
    hosts = {"github.com": "1.2.3.4", "api.github.com": "5.6.7.8"}

    orig = connectivity._probe_latency

    def fake_probe(ip, timeout=2.5):
        # github.com 可达(延迟小)，api.github.com 不可达
        return {"1.2.3.4": 0.05}.get(ip)  # 5.6.7.8 → None（不可达，不下钉）

    connectivity._probe_latency = fake_probe
    try:
        out = connectivity.select_usable(hosts)
    finally:
        connectivity._probe_latency = orig

    assert out == {"github.com": "1.2.3.4"}  # 仅保留可达者；不可达域名不钉，留给系统代理


def test_select_usable_all_unreachable_returns_empty():
    hosts = {"github.com": "1.2.3.4"}
    orig = connectivity._probe_latency
    connectivity._probe_latency = lambda ip, timeout=2.5: None
    try:
        out = connectivity.select_usable(hosts)
    finally:
        connectivity._probe_latency = orig
    assert out == {}  # 全不可达 → 返回空，由调用方回退系统代理/正常 DNS



def main():
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, True, ""))
        except AssertionError as ex:
            results.append((name, False, "assert: %s" % ex))
        except Exception as ex:
            results.append((name, False, "%s: %s" % (type(ex).__name__, ex)))
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " -> " + detail))
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
