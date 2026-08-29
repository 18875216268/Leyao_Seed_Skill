"""连通性兜底测试：云函数 hosts 解析 / IP 直连拉取（确定性，本地 mock server，不依赖外网）。"""

import http.server
import os
import shutil
import socketserver
import subprocess
import sys
import tarfile
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


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/hosts"):
            body = _HOSTS_BODY.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def _start_server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def test_parse_hosts():
    hosts = connectivity._parse_hosts(_HOSTS_BODY)
    assert hosts.get("github.com") == "20.205.243.166"
    assert hosts.get("raw.githubusercontent.com") == "185.199.111.133"
    assert "# comment" not in hosts


def test_resolve_args_format():
    args = connectivity.resolve_args({"github.com": "1.2.3.4"}, ["github.com"])
    assert args == ["--resolve", "github.com:443:1.2.3.4"]


def test_repo_slug():
    owner, repo = connectivity._repo_slug("https://github.com/18875216268/Leyao_Seed_Skill.git")
    assert owner == "18875216268" and repo == "Leyao_Seed_Skill"
    owner, repo = connectivity._repo_slug("git@github.com:18875216268/Leyao_Seed_Skill.git")
    assert owner == "18875216268" and repo == "Leyao_Seed_Skill"


def test_fetch_hosts_local_mock():
    httpd, port = _start_server()
    try:
        hosts = connectivity.fetch_hosts("http://127.0.0.1:%d/hosts" % port, "ziyou", timeout=5)
        assert hosts.get("github.com") == "20.205.243.166"
    finally:
        httpd.shutdown()


def test_fetch_hosts_failure_returns_empty():
    assert connectivity.fetch_hosts("http://127.0.0.1:1/nope", "ziyou", timeout=2) == {}


def test_extract_tarball_overwrites_tree():
    root = tempfile.mkdtemp(prefix="conn-extract-")
    try:
        tar_path = os.path.join(root, "blob.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tf:
            info = tarfile.TarInfo("Leyao_Seed_Skill/manifest.json")
            payload = b'{"deploy": {"accelerator_source": "ziyou"}}'
            info.size = len(payload)
            tf.addfile(info, __import__("io").BytesIO(payload))
            dir_info = tarfile.TarInfo("Leyao_Seed_Skill/skills")
            dir_info.type = tarfile.DIRTYPE
            tf.addfile(dir_info)
        ok, msg = connectivity._extract_tarball(tar_path, root)
        assert ok is True
        out = os.path.join(root, "manifest.json")
        assert os.path.exists(out)
        with open(out, encoding="utf-8") as f:
            assert '"ziyou"' in f.read()
    finally:
        shutil.rmtree(root, ignore_errors=True)


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
