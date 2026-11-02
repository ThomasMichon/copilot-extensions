from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from agent_procutil import no_window_kwargs

from agent_index.sources.credentials import SourceCredentialError, authorization
from agent_index.sources.git_repo import GitRepoConnector
from agent_index.sources.github import GitHubConnector


def test_renewed_header_is_not_masked() -> None:
    value = authorization(lambda: "synthetic-source-token")
    assert value.endswith(" synthetic-source-token")
    assert value.split()[0].lower() == "bear" + "er"


def test_account_relay_is_explicit_and_not_the_scoped_broker(monkeypatch, tmp_path):
    from agent_index.indexing import engine
    from agent_index import source_account_relay as account_relay

    calls = []
    monkeypatch.setattr(account_relay, "token_provider", lambda path, **kwargs: (
        calls.append((path, kwargs)) or (lambda: "renewed-account-token")
    ))
    spec = engine.SourceSpec(
        name="github:example/project", type="github", auth_account="account-a",
        auth_relay=str(tmp_path / "worker.json"),
    )
    kwargs = engine._connector_kwargs(spec)
    assert kwargs["token_provider"]() == "renewed-account-token"
    assert calls == [(tmp_path / "worker.json", {
        "source": "github:example/project", "account": "account-a", "host": "github.com",
    })]
    from dataclasses import replace
    with pytest.raises(SourceCredentialError, match="source-provider-policy-invalid"):
        engine._connector_kwargs(replace(spec, auth_gateway=str(tmp_path / "other.json")))


@pytest.mark.parametrize("reference", [None, "", "relative"])
def test_core_corpus_config_rejects_present_invalid_relay(monkeypatch, tmp_path, reference):
    import base64
    import json
    from agent_index import config
    from agent_index.indexing import engine

    auth = {"account": "selected", "relay": reference}
    value = {"corpus": {"sources": [{"name": "github:example/project", "auth": auth}]}}
    monkeypatch.setenv(config.CONFIG_DATA_ENV, base64.urlsafe_b64encode(
        json.dumps(value).encode(),
    ).decode())
    monkeypatch.setenv(config.SOURCE_MODE_ENV, "explicit")
    monkeypatch.delenv("AGENT_INDEX_SOURCES", raising=False)
    assert config.read_corpus_sources()[0]["auth"] == auth
    with pytest.raises(SourceCredentialError, match="source-provider-policy-invalid"):
        engine.configured_source_specs()


def test_core_corpus_config_rejects_null_gateway_alongside_relay(monkeypatch, tmp_path):
    import base64
    import json
    from agent_index import config
    from agent_index.indexing import engine

    auth = {"account": "selected", "gateway": None, "relay": str(tmp_path / "worker.json")}
    value = {"corpus": {"sources": [{"name": "github:example/project", "auth": auth}]}}
    monkeypatch.setenv(config.CONFIG_DATA_ENV, base64.urlsafe_b64encode(
        json.dumps(value).encode(),
    ).decode())
    monkeypatch.setenv(config.SOURCE_MODE_ENV, "explicit")
    monkeypatch.delenv("AGENT_INDEX_SOURCES", raising=False)
    with pytest.raises(SourceCredentialError, match="source-provider-policy-invalid"):
        engine.configured_source_specs()


@pytest.mark.parametrize("kind,provider_host", [("git", "github.com"), ("github", "enterprise.example")])
def test_relay_destination_mismatch_denies_before_credential_delivery(
    monkeypatch, tmp_path, kind, provider_host,
):
    import json
    from types import SimpleNamespace
    from agent_index.indexing import engine
    from agent_index import source_account_relay as account_relay

    capability = tmp_path / "capability"
    capability.write_text("isolated-account-capability-value-123456789")
    source = "git:project" if kind == "git" else "github:example/project"
    policy = tmp_path / "worker.json"
    policy.write_text(json.dumps({
        "schema": "credential-relay.account-worker", "version": 1,
        "transport": {"host": "127.0.0.1", "port": 19999},
        "bindings": [{
            "source": source, "account": "selected", "recipient": "index",
            "host": provider_host, "action": "get-github-token",
            "capability_file": str(capability),
        }],
    }))
    monkeypatch.setattr(account_relay, "exchange", lambda *a, **k: pytest.fail(
        "destination mismatch requested an account credential",
    ))
    if kind == "git":
        monkeypatch.setattr(GitRepoConnector, "materialize", lambda **kwargs: SimpleNamespace(
            source_name=source, repo_path=tmp_path / "project",
        ))
    spec = engine.SourceSpec(
        name=source, type=kind, auth_account="selected", auth_relay=str(policy),
        repo_path=str(tmp_path / "project"), remote_url="https://other.example/project.git",
    )
    kwargs = engine._connector_kwargs(spec)
    if kind == "git":
        with pytest.raises(RuntimeError, match="unavailable"):
            kwargs["token_provider"]()
    else:
        connector = GitHubConnector(
            source, **kwargs, min_interval_s=0,
            transport=httpx.MockTransport(lambda request: pytest.fail("unexpected upstream request")),
        )
        try:
            with pytest.raises(SourceCredentialError, match="unavailable"):
                connector.discover()
        finally:
            connector._client.close()


def test_http_refreshes_every_page_and_retry_without_ambient_fallback(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "wrong-ambient-token")
    monkeypatch.setattr("time.sleep", lambda _: None)
    acquisitions: list[str] = []
    received: list[str] = []

    def provider() -> str:
        acquisitions.append(f"scoped-token-{len(acquisitions)}")
        return acquisitions[-1]

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request.headers["Authorization"].split()[1])
        if len(received) == 1:
            return httpx.Response(503)
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=[], headers={"Link": '<unused>; rel="next"'})
        return httpx.Response(200, json=[])

    connector = GitHubConnector(
        "github:sample/repo", token_provider=provider,
        transport=httpx.MockTransport(handler), min_interval_s=0,
    )
    assert connector.discover() == []
    assert received == acquisitions == ["scoped-token-0", "scoped-token-1", "scoped-token-2"]
    assert "Authorization" not in connector._client.headers
    connector._client.close()


@pytest.mark.parametrize("mode,expected", [
    ("renewable", "api.github.com"), ("explicit", "selected.test"), ("native", "ambient.test"),
])
def test_http_destination_ignores_ambient_override_only_in_renewable_mode(monkeypatch, mode, expected):
    monkeypatch.setenv("AGENT_INDEX_GITHUB_API", "https://ambient.test")
    visits = []
    acquired = []

    def provider():
        acquired.append(True)
        return "synthetic-scoped-token"

    def handler(request):
        visits.append((request.url.host, request.headers["Authorization"].split()[1]))
        return httpx.Response(200, json=[])

    connector = GitHubConnector(
        "github:sample/repo", token_provider=provider if mode != "native" else None,
        token="synthetic-native-token" if mode == "native" else None,
        api_base="https://selected.test" if mode == "explicit" else None,
        transport=httpx.MockTransport(handler), min_interval_s=0,
    )
    try:
        assert connector.discover() == []
        assert visits == [(expected, "synthetic-native-token" if mode == "native" else "synthetic-scoped-token")]
        assert len(acquired) == (0 if mode == "native" else 1)
    finally:
        connector._client.close()


@pytest.mark.parametrize("value", ["", "bad\nvalue", "bad value", None])
def test_unavailable_http_credentials_never_make_anonymous_request(value) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("failed credential acquisition made an upstream request")

    connector = GitHubConnector(
        "github:sample/repo", token_provider=lambda: value,
        transport=httpx.MockTransport(handler), min_interval_s=0,
    )
    with pytest.raises(SourceCredentialError, match="source-credential-invalid"):
        connector.discover()
    connector._client.close()


@pytest.mark.parametrize("failure", [
    RuntimeError("private-provider-diagnostic"),
    KeyError("private-provider-diagnostic"),
    subprocess.CalledProcessError(1, ["provider", "private-provider-diagnostic"]),
    Exception("private-provider-diagnostic"),
])
def test_credential_provider_failure_is_sanitized(failure, caplog) -> None:
    import threading
    import traceback

    workers = []

    def unavailable() -> str:
        workers.append(threading.current_thread())
        raise failure

    with pytest.raises(SourceCredentialError) as captured:
        authorization(unavailable)
    assert str(captured.value) == "source-credential-unavailable"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "private-provider-diagnostic" not in "".join(traceback.format_exception(captured.value))
    assert "private-provider-diagnostic" not in caplog.text
    assert workers and all(not worker.is_alive() for worker in workers)


@pytest.mark.parametrize("failure", [
    KeyboardInterrupt(), SystemExit(7), GeneratorExit(), asyncio.CancelledError(),
])
def test_credential_provider_process_control_propagates_and_reaps_worker(failure):
    import threading

    workers = []

    def interrupted():
        workers.append(threading.current_thread())
        raise failure

    with pytest.raises(type(failure)) as captured:
        authorization(interrupted)
    assert captured.value is failure
    assert workers and all(not worker.is_alive() for worker in workers)


def test_credential_provider_preserves_context_and_reaps_successful_worker():
    import threading
    from contextvars import ContextVar

    selected = ContextVar("source-credential-fixture")
    selection = selected.set("fixture-scoped-token")
    workers = []

    def provider():
        workers.append(threading.current_thread())
        return selected.get()

    try:
        assert authorization(provider).endswith(" fixture-scoped-token")
    finally:
        selected.reset(selection)
    assert workers and all(not worker.is_alive() for worker in workers)


def test_refresh_mode_disallows_http_redirect_even_with_redirecting_client() -> None:
    visits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visits.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.test/private"})

    with httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        connector = GitHubConnector(
            "github:sample/repo", token_provider=lambda: "scoped-token",
            client=client, min_interval_s=0,
        )
        with pytest.raises(httpx.HTTPStatusError):
            connector.discover()
    assert len(visits) == 1


def test_refresh_mode_rejects_conflicting_client_authentication() -> None:
    with httpx.Client(base_url="https://api.github.com", auth=("other", "account")) as client:
        with pytest.raises(ValueError, match="client authentication"):
            GitHubConnector(
                "github:sample/repo", token_provider=lambda: "scoped-token", client=client,
            )


def _bare(tmp_path: Path) -> Path:
    path = tmp_path / "bare"
    subprocess.run(
        ["git", "init", "--bare", "--quiet", str(path)], check=True, **no_window_kwargs(),
    )
    result = subprocess.run(
        ["git", f"--git-dir={path}", "remote", "add", "origin",
         "https://example.test/owner/repo.git"],
        capture_output=True, text=True,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    return path


def test_git_refresh_is_per_fetch_not_argv_or_global_environment(tmp_path, monkeypatch) -> None:
    path = _bare(tmp_path)
    acquisitions: list[str] = []
    calls: list[dict] = []
    real_run = subprocess.run
    monkeypatch.setenv("GIT_TRACE_CURL", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.extraHeader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "ambient-header")
    before = dict(os.environ)

    def provider() -> str:
        acquisitions.append(f"scoped-token-{len(acquisitions)}")
        return acquisitions[-1]

    def run(argv, **kwargs):
        if "fetch" in argv:
            calls.append({"argv": argv, "env": kwargs["env"]})
            return subprocess.CompletedProcess(argv, 0)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    connector = GitRepoConnector(repo_path=path, token_provider=provider)
    connector._fetch_authenticated()
    connector._fetch_authenticated()
    assert len(calls) == 2
    assert acquisitions == ["scoped-token-0", "scoped-token-1"]
    for call, expected in zip(calls, acquisitions, strict=True):
        assert not any(expected in part for part in call["argv"])
        env = call["env"]
        settings = {
            env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
            for i in range(int(env["GIT_CONFIG_COUNT"]))
        }
        assert settings["http.https://example.test/owner/repo.git.extraHeader"].endswith(expected)
        assert settings["http.https://example.test/owner/repo.git.extraHeader"].startswith(
            "Authorization: ",
        )
        assert settings["credential.helper"] == ""
        assert settings["credential.https://example.test/owner/repo.git.helper"] == ""
        assert settings["http.https://example.test/owner/repo.git.sslVerify"] == "true"
        assert settings["http.followRedirects"] == "false"
        assert "GIT_TRACE_CURL" not in env
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert "--prune" in call["argv"]
    assert dict(os.environ) == before


def test_failed_git_refresh_never_memoizes_stale_success(tmp_path, monkeypatch) -> None:
    connector = GitRepoConnector(repo_path=_bare(tmp_path), token_provider=lambda: "scoped-token")
    real_run = subprocess.run
    attempts: list[str] = []

    def run(argv, **kwargs):
        if "fetch" in argv:
            attempts.append("fetch")
            return subprocess.CompletedProcess(argv, 128, stderr=b"sensitive provider error")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    for _ in range(2):
        with pytest.raises(SourceCredentialError, match="source-fetch-denied"):
            connector.current_commit()
        assert not connector._resolved
        assert connector._index_commit is None
    assert len(attempts) == 2


def test_materialization_refuses_existing_working_checkout(tmp_path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)], check=True, **no_window_kwargs(),
    )
    with pytest.raises(SourceCredentialError, match="source-materialization-mismatch"):
        GitRepoConnector.materialize(
            repo_path=tmp_path, remote_url="https://example.test/repo.git",
            token_provider=lambda: pytest.fail("mismatched directory acquired credentials"),
        )


def test_refresh_mode_refuses_unfetched_local_revision(tmp_path) -> None:
    connector = GitRepoConnector(
        repo_path=_bare(tmp_path), ref="local-tag",
        token_provider=lambda: pytest.fail("local revision attempted credential acquisition"),
    )
    with pytest.raises(SourceCredentialError, match="source-remote-ref-required"):
        connector.current_commit()


def test_first_touch_materialization_is_dedicated_and_reopenable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_GIT_REMOTE", "secondary")
    remote = "https://example.test/owner/repo.git"
    path = tmp_path / "new" / "repo"
    acquisitions = []

    def provider():
        acquisitions.append("acquired")
        return "scoped-token"

    connector = GitRepoConnector.materialize(
        repo_path=path, remote_url=remote, token_provider=provider, ref="origin/dev",
    )
    assert connector.source_name == "git:repo"
    assert connector._bare_repository
    assert not connector._resolved
    assert connector._remote == "origin"
    assert acquisitions == []  # Initialization is local; acquisition belongs to fetch.
    reopened = GitRepoConnector.materialize(
        repo_path=path, remote_url=remote, token_provider=provider, ref="origin/dev",
    )
    assert reopened.source_name == connector.source_name
    assert not (path / ".git").exists()
    with pytest.raises(SourceCredentialError, match="source-materialization-mismatch"):
        GitRepoConnector.materialize(
            repo_path=path, remote_url="https://other.test/owner/repo.git",
            token_provider=provider,
        )


def test_renewable_git_ignores_ambient_remote_but_honors_explicit_selection(tmp_path, monkeypatch):
    path = _bare(tmp_path)
    secondary = "https://other.test/owner/repo.git"
    subprocess.run(
        ["git", f"--git-dir={path}", "remote", "add", "secondary", secondary],
        check=True, capture_output=True, **no_window_kwargs(),
    )
    monkeypatch.setenv("AGENT_INDEX_GIT_REMOTE", "secondary")
    real_run = subprocess.run
    destinations = []

    def run(argv, **kwargs):
        if "fetch" in argv:
            destinations.append(next(part for part in argv if part.startswith("https://")))
            return subprocess.CompletedProcess(argv, 0)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    provider = lambda: "scoped-token"
    owned = GitRepoConnector.materialize(
        repo_path=path, remote_url="https://example.test/owner/repo.git", token_provider=provider,
    )
    owned._fetch_authenticated()
    explicit = GitRepoConnector(repo_path=path, remote="secondary", token_provider=provider)
    explicit._fetch_authenticated()
    assert destinations == ["https://example.test/owner/repo.git", secondary]
    assert GitRepoConnector(repo_path=path)._remote == "secondary"


def test_renewable_git_ref_and_fetch_defaults_do_not_inherit_native_environment(tmp_path, monkeypatch):
    path = _bare(tmp_path)
    monkeypatch.setenv("AGENT_INDEX_GIT_REF", "origin/feature")
    monkeypatch.setenv("AGENT_INDEX_GIT_FETCH", "0")
    provider = lambda: "synthetic-scoped-token"
    renewable = GitRepoConnector(repo_path=path, token_provider=provider)
    assert renewable._ref == "origin/HEAD" and renewable._fetch
    explicit = GitRepoConnector(repo_path=path, ref="origin/dev", fetch=True, token_provider=provider)
    assert explicit._ref == "origin/dev" and explicit._fetch
    with pytest.raises(ValueError, match="require fetch"):
        GitRepoConnector(repo_path=path, fetch=False, token_provider=provider)
    native = GitRepoConnector(repo_path=path)
    assert native._ref == "origin/feature" and not native._fetch


def test_configured_gateway_sources_never_use_ambient_gh_resolution(tmp_path, monkeypatch):
    from agent_index.indexing import engine
    from fleet_manager import gateway_worker

    provider = lambda: "scoped-token"
    selections = []

    def select(path, *, source, account):
        selections.append((path, source, account))
        return provider

    monkeypatch.setattr(gateway_worker, "token_provider", select)
    monkeypatch.setattr(
        engine, "_resolve_gh_token",
        lambda *args: pytest.fail("Gateway selection resolved an ambient account"),
    )
    policy = tmp_path / "worker.json"
    checkout = tmp_path / "repo"
    kwargs = engine._connector_kwargs(engine.SourceSpec(
        name="git:repo", auth_account="selected", auth_gateway=str(policy),
        repo_path=str(checkout), remote_url="https://example.test/owner/repo.git", ref="origin/dev",
    ))
    assert kwargs == {
        "repo_path": str(checkout), "ref": "origin/dev", "token_provider": provider,
        "hint_account": "selected",
    }
    kwargs = engine._connector_kwargs(engine.SourceSpec(
        name="github:owner/repo", type="github", auth_account="selected", auth_gateway=str(policy),
    ))
    assert kwargs == {"token_provider": provider}
    assert selections == [
        (policy, "git:repo", "selected"), (policy, "github:owner/repo", "selected"),
    ]


def test_real_https_git_first_ingestion_refresh_and_denied_stale_cursor(tmp_path, monkeypatch):
    import datetime
    import ipaddress
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    def git(*args):
        result = subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True,
            timeout=15, **no_window_kwargs(),
        )
        return result.stdout.strip()

    executable = Path(git("--exec-path")) / (
        "git-http-backend.exe" if os.name == "nt" else "git-http-backend"
    )
    if not executable.is_file():
        pytest.skip("installed Git does not supply its HTTP backend")
    remote_root = tmp_path / "export"
    remote_root.mkdir()
    remote = remote_root / "repo.git"
    seed = tmp_path / "seed"
    git("init", "--bare", "--quiet", str(remote))
    git("init", "--quiet", "-b", "main", str(seed))
    git("-C", str(seed), "config", "user.name", "Synthetic Source")
    git("-C", str(seed), "config", "user.email", "source@example.test")
    (seed / "README.md").write_text("# First remote revision\n", encoding="utf-8")
    git("-C", str(seed), "add", "README.md")
    git("-C", str(seed), "commit", "--quiet", "-m", "first")
    git("-C", str(seed), "push", "--quiet", str(remote), "main")
    git(f"--git-dir={remote}", "symbolic-ref", "HEAD", "refs/heads/main")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-git-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False).sign(key, hashes.SHA256())
    )
    ca, pem = tmp_path / "ca.pem", tmp_path / "key.pem"
    ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    accepted = ["scoped-token-first"]
    requests = []
    backend_failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self._backend()

        def do_POST(self):
            self._backend()

        def _backend(self):
            received = self.headers.get("Authorization", "").split()
            authorized = len(received) == 2 and received[1] == accepted[0]
            requests.append(authorized)
            if not authorized:
                self.send_error(403)
                return
            parts = urlsplit(self.path)
            environment = GitRepoConnector._authenticated_environment() | {
                "GIT_PROJECT_ROOT": str(remote_root), "GIT_HTTP_EXPORT_ALL": "1",
                "REQUEST_METHOD": self.command, "PATH_INFO": parts.path,
                "QUERY_STRING": parts.query, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "REMOTE_USER": "synthetic", "SERVER_PROTOCOL": "HTTP/1.1",
            }
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            result = subprocess.run(
                [str(executable)], input=body, capture_output=True, env=environment,
                timeout=15, **no_window_kwargs(),
            )
            if result.returncode:
                backend_failures.append(result.stderr.decode("utf-8", errors="replace"))
                self.send_error(500)
                return
            headers, content = result.stdout.split(b"\r\n\r\n", 1)
            self.send_response(200)
            for line in headers.decode().split("\r\n"):
                header, value = line.split(":", 1)
                self.send_header(header, value.strip())
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(ca, pem)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    origin = f"https://127.0.0.1:{server.server_port}/repo.git"
    checkout = tmp_path / "independent"
    acquisitions = []

    def provider():
        acquisitions.append(accepted[0])
        return accepted[0]

    fetch_errors = []
    real_run = subprocess.run

    def inspect_fetch(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if "fetch" in argv and result.returncode:
            diagnostic = result.stderr.decode("utf-8", errors="replace")
            for synthetic in ("scoped-token-first", "scoped-token-renewed", "revoked-token"):
                diagnostic = diagnostic.replace(synthetic, "[synthetic redacted]")
            fetch_errors.append(diagnostic)
        return result

    monkeypatch.setattr(subprocess, "run", inspect_fetch)
    monkeypatch.setenv("AGENT_INDEX_GIT_REF", "origin/feature")
    monkeypatch.setenv("AGENT_INDEX_GIT_FETCH", "0")
    try:
        connector = GitRepoConnector.materialize(
            repo_path=checkout, remote_url=origin, token_provider=provider,
        )
        git(f"--git-dir={checkout}", "config", f"http.{origin}.sslCAInfo", str(ca))
        if os.name == "nt":
            git(f"--git-dir={checkout}", "config", "http.sslBackend", "openssl")
        try:
            first = connector.discover()
        except SourceCredentialError:
            pytest.fail(f"synthetic HTTPS fixture failed: {fetch_errors}; backend={backend_failures}")
        assert {entry.source for entry in first} == {"git:repo", "git:repo:commits"}
        assert any("First remote revision" in entry.content for entry in first)
        cursor = connector.current_commit()
        accepted[0] = "scoped-token-renewed"
        (seed / "README.md").write_text("# Second remote revision\n", encoding="utf-8")
        git("-C", str(seed), "commit", "--quiet", "-am", "second")
        git("-C", str(seed), "push", "--quiet", str(remote), "main")
        reopened = GitRepoConnector.materialize(
            repo_path=checkout, remote_url=origin, token_provider=provider,
        )
        assert any("Second remote revision" in entry.content
                   for entry in reopened.discover_changed(cursor))
        assert reopened.current_commit() != cursor
        assert acquisitions == ["scoped-token-first", "scoped-token-renewed"]
        denied = GitRepoConnector(repo_path=checkout, token_provider=lambda: "revoked-token")
        with pytest.raises(SourceCredentialError, match="source-fetch-denied"):
            denied.discover()
        assert not denied._resolved and denied._index_commit is None
        assert requests.count(False) == 1 and requests.count(True) >= 4
        git(f"--git-dir={remote}", "update-ref", "refs/heads/dev", reopened.current_commit())
        git(f"--git-dir={checkout}", "update-ref", "refs/tags/origin/dev", cursor)
        git(f"--git-dir={checkout}", "update-ref", "refs/tags/refs/remotes/origin/dev", cursor)
        branch = GitRepoConnector(repo_path=checkout, token_provider=provider, ref="origin/dev")
        assert branch.current_commit() == reopened.current_commit()
        git(f"--git-dir={remote}", "update-ref", "-d", "refs/heads/dev")
        deleted = GitRepoConnector(repo_path=checkout, token_provider=provider, ref="origin/dev")
        with pytest.raises(SourceCredentialError, match="source-remote-ref-unavailable"):
            deleted.discover()
        assert not deleted._resolved and deleted._index_commit is None
        assert git(f"--git-dir={checkout}", "for-each-ref", "refs/remotes/origin/dev") == ""
        assert git(f"--git-dir={checkout}", "show-ref", "--verify", "--hash", "refs/tags/origin/dev") == cursor
        assert backend_failures == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
