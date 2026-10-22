"""Real subprocess and isolated TLS counterparts to the credential unit controls."""

from __future__ import annotations

import ipaddress
import json
import shutil
import ssl
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

import pytest

from agent_worktrees.providers import github
from agent_worktrees.providers.azure_devops import AzureDevOpsProvider
from agent_worktrees.providers.azure_observation_reads import AdoReader, NoRedirect
from agent_worktrees.providers.base import ProviderError, run_cli
from agent_worktrees.providers.observation_credentials import ObservationCredentialError
from test_pr_observation_credentials import Binding, Source


@pytest.mark.parametrize("host,api_base,destination", [
    ("github.com", "https://api.github.com", "api.github.com:443"),
    ("github.com:443", "https://api.github.com", "api.github.com:443"),
    ("tenant.ghe.com:443", "https://api.tenant.ghe.com", "api.tenant.ghe.com:443"),
])
def test_actual_gh_api_host_override_cannot_redirect_a_scoped_request(
    tmp_path, monkeypatch, host, api_base, destination,
):
    executable = shutil.which("gh")
    if executable is None:
        pytest.skip("native GitHub CLI is unavailable")
    config = tmp_path / "gh-config"
    config.mkdir()
    (config / "hosts.yml").write_text(
        "github.com:\n    user: fixture\n    api_host: alternate.invalid\n", encoding="utf-8",
    )
    destinations = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_CONNECT(self):
            destinations.append(self.path)
            self.send_error(502, "isolated routing fixture does not forward")

    with HTTPServer(("127.0.0.1", 0), Proxy) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        proxy = f"http://127.0.0.1:{server.server_port}"
        environment = {
            "GH_CONFIG_DIR": str(config), "GH_PROMPT_DISABLED": "1",
            "GH_TOKEN": "synthetic-control", "GITHUB_TOKEN": "synthetic-control",
            "GH_ENTERPRISE_TOKEN": "synthetic-control", "GITHUB_ENTERPRISE_TOKEN": "synthetic-control",
            "HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy, "http_proxy": proxy,
            "NO_PROXY": "", "no_proxy": "", "GH_DEBUG": "",
        }
        try:
            # A real relative request proves the inherited override is active.
            control = run_cli(
                [executable, "api", "--hostname", "github.com",
                 "/repos/owner/repository/pulls/7"], env=environment,
            )
            assert control.returncode != 0
            if destinations == ["api.github.com:443"]:
                pytest.skip("installed GitHub CLI has no api_host routing override")
            assert destinations == ["alternate.invalid:443"], control.stderr
            destinations.clear()
            monkeypatch.setattr(
                github, "run_cli",
                lambda argv, *, env: run_cli(
                    [executable, *argv[1:]], env={**environment, **env},
                ),
            )
            source = Source(Binding(api_base=api_base))
            with pytest.raises(ProviderError):
                github.GitHubProvider().get_snapshot(
                    "owner/repository", 7, api_base="https://" + host, credential_source=source,
                )
            assert destinations == [destination]
            assert source.calls == 1
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_actual_native_subprocess_receives_each_renewal_without_token_argv(tmp_path, monkeypatch):
    script = tmp_path / "native-forge-fixture.py"
    script.write_text(
        "import json, os, sys\n"
        "keys = ('GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN')\n"
        "values = {os.environ.get(key) for key in keys}\n"
        "assert len(values) == 1 and next(iter(values)).startswith('synthetic-')\n"
        "assert not any('synthetic-' in arg for arg in sys.argv)\n"
        "url = sys.argv[-1]\n"
        "if '/pulls/7' == url[-8:]:\n"
        "    value = {'state':'open','head':{'sha':'head'},'title':'Example'}\n"
        "elif url.endswith('/status'):\n"
        "    value = {'total_count':0,'statuses':[]}\n"
        "elif '/check-runs?' in url:\n"
        "    value = {'total_count':0,'check_runs':[]}\n"
        "else:\n"
        "    value = []\n"
        "print(json.dumps(value))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GH_TOKEN", "ambient-must-not-win")
    monkeypatch.setenv("GITHUB_ENTERPRISE_TOKEN", "ambient-must-not-win")
    monkeypatch.setattr(
        github, "run_cli",
        lambda argv, **options: run_cli([sys.executable, str(script), *argv[1:]], **options),
    )
    source = Source()
    snapshot = github.GitHubProvider().get_snapshot(
        "owner/repository", 7, credential_source=source,
    )
    assert snapshot.head_sha == "head" and snapshot.checks_complete
    assert source.calls == 4
    assert "synthetic-" not in repr(snapshot)


def certificate(tmp_path):
    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "loopback fixture")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "tls.pem", tmp_path / "tls.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    return cert_path, key_path


def test_subprocess_controls_remain_collectable_without_cryptography():
    path = Path(__file__).resolve()
    code = (
        "import importlib.abc, runpy, sys\n"
        "class NoCrypto(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'cryptography' or name.startswith('cryptography.'):\n"
        "            raise ImportError('fixture: unavailable cryptography')\n"
        "sys.meta_path.insert(0, NoCrypto())\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "module = runpy.run_path(sys.argv[2])\n"
        "assert callable(module['test_actual_native_subprocess_receives_each_renewal_without_token_argv'])\n"
        "assert not any(name.startswith('cryptography') for name in sys.modules)\n"
    )
    result = run_cli([sys.executable, "-c", code, str(path.parent), str(path)])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fail_at", [None, 2])
def test_actual_ado_tls_pages_renew_and_disconnect_without_another_request(tmp_path, fail_at):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            observed.append(self.headers.get("Authorization"))
            body = json.dumps({"value": [{"id": len(observed)}], "count": 1}).encode()
            self.send_response(200)
            if len(observed) == 1:
                self.send_header("x-ms-continuationtoken", "next")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    cert, key = certificate(tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=str(cert))
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.socket = server_context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"https://127.0.0.1:{server.server_port}/organization"
        source = Source(Binding("azure-devops", base, "project/repository", "observe", "bearer"),
                        fail_at=fail_at)
        reader = AdoReader(
            AzureDevOpsProvider(), "project/repository", 7, base, None,
            credential_source=source,
            opener=build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=client_context)),
        )
        try:
            if fail_at:
                with pytest.raises(ObservationCredentialError):
                    reader.collection(reader.base, continuation_paging=True)
                assert source.calls == 2 and len(observed) == 1
            else:
                assert reader.collection(reader.base, continuation_paging=True).complete
                assert source.calls == len(observed) == 2
            for index, value in enumerate(observed, 1):
                scheme, material = value.split(" ", 1)
                assert scheme == "Bear" + "er"
                assert material == "synthetic-" + str(index)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()
