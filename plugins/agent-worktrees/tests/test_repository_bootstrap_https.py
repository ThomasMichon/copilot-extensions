"""Disposable loopback HTTPS Git acquisition with Git-owned credential handling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import shlex
import shutil
import ssl
import subprocess
import sys
import threading

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import repos, repository_bootstrap as bootstrap
from test_repository_bootstrap import registry as registry
from test_repository_setup import consumer, git, source as source


def _certificate(certfile: Path, keyfile: Path) -> None:
    if importlib.util.find_spec("cryptography") is None:
        executable = shutil.which("openssl")
        if executable is None and (git_executable := shutil.which("git")):
            for parent in Path(git_executable).resolve().parents:
                candidate = parent / "usr" / "bin" / "openssl.exe"
                if candidate.is_file():
                    executable = str(candidate)
                    break
        if executable is None:
            pytest.skip("Loopback TLS requires cryptography or an existing OpenSSL")
        result = subprocess.run([
            executable, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(keyfile), "-out", str(certfile), "-days", "1",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        ], capture_output=True, text=True, timeout=30, **no_window_kwargs())
        assert result.returncode == 0, result.stderr
        return
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    certfile.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))


@pytest.mark.timeout(240)
def test_real_https_clone_credential_owner_and_repository_setup(
    source: Path, registry: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer(source, """
if ($r.mode -eq 'apply') { Set-Content -LiteralPath $state -Value 'done' }
$s = if (Test-Path -LiteralPath $state) { 'ready' } else { 'needed' }
[IO.File]::WriteAllText($r.result_file,
    ('{"version":1,"status":"' + $s + '","message":"owner verified"}'))
""")
    web = tmp_path / "web"
    web.mkdir()
    bare = web / "sample.git"
    git(tmp_path, "clone", "--bare", "-q", str(source), str(bare))
    try:
        git(bare, "--git-dir", str(bare), "update-server-info")
    except subprocess.CalledProcessError as error:
        pytest.fail(error.stderr)
    certfile, keyfile = tmp_path / "loopback.pem", tmp_path / "loopback-key.pem"
    _certificate(certfile, keyfile)
    observed = {"challenge": False, "authenticated": False}

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if not self.headers.get("Authorization"):
                observed["challenge"] = True
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Disposable Git fixture"')
                self.end_headers()
                return
            observed["authenticated"] = True
            super().do_GET()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(web)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile, keyfile)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    helper = tmp_path / "credential owner.py"
    helper.write_text(
        "import pathlib, sys\n"
        "if sys.argv[-1] == 'get':\n"
        "    sys.stdin.read()\n"
        "    pathlib.Path(__file__).with_suffix('.called').write_text('get')\n"
        "    print('username=disposable-test-user\\npassword=disposable-test-value\\n')\n",
        encoding="utf-8",
    )
    config = tmp_path / "gitconfig"
    git(tmp_path, "config", "--file", str(config), "http.sslCAInfo", str(certfile))
    git(tmp_path, "config", "--file", str(config), "http.schannelUseSSLCAInfo", "true")
    git(tmp_path, "config", "--file", str(config), "credential.helper",
        "!" + shlex.quote(sys.executable) + " " + shlex.quote(str(helper)))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    url = f"https://localhost:{server.server_port}/sample.git"
    target = tmp_path / "acquired source"
    before = config.read_bytes()
    thread.start()
    try:
        assert bootstrap.command([
            url, "--target", str(target), "--apply", "--trust-repository-setup",
        ]) == 0
        assert observed == {"challenge": True, "authenticated": True}
        assert helper.with_suffix(".called").read_text() == "get"
        assert git(target, "rev-parse", "HEAD") == git(source, "rev-parse", "HEAD")
        registered = repos.read_registry(strict=True).repos["sample"]
        assert registered.local_path() == str(target)
        assert registered.remote == url
        assert git(target, "remote", "get-url", "origin") == registered.remote
        assert (tmp_path / "calls.txt").read_text().splitlines() == ["check", "apply", "check"]
        assert config.read_bytes() == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
