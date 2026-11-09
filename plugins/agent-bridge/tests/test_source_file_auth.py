"""Lease delivery and native-file effects, with no live accounts or processes."""

from __future__ import annotations

import json
import gzip
import os
import asyncio
import datetime
import ssl
import stat
import time
from types import SimpleNamespace

import httpx
import pytest
from credential_relay.http_auth import HttpAuthContext, SourceHttpAuth

from agent_bridge import source_lease_http as wire
from agent_bridge.copilot_file_auth import CopilotAccountFile, CopilotFileAuthError


class Source:
    def __init__(self):
        self.calls = []

    def supports(self, action, fields):
        return action == "get"

    async def resolve(self, action, fields, *, timeout):
        self.calls.append(dict(fields))
        return "username=synthetic\npassword=synthetic-http-credential\n"


def fixture_client(tmp_path, monkeypatch, handler):
    clock = SimpleNamespace(wall=1000.0, monotonic=200.0)
    monkeypatch.setattr(wire, "time", SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.monotonic,
    ))
    capability = tmp_path / "capability"
    capability.write_text("a" * 64, encoding="ascii")
    endpoint = "https://example.com/api/v1/source-credentials"
    context = HttpAuthContext("https://example.com", "synthetic", "generation")
    source = Source()
    auth = SourceHttpAuth(source, context, endpoint)
    discovery_endpoint = "https://example.com/health"
    client = wire.HttpSourceLeaseClient(
        endpoint, auth=auth, context=context, source="example", account="synthetic",
        recipient="worker", host="github.com", capability_file=capability,
        discovery_endpoint=discovery_endpoint, discovery_context=context,
        discovery_auth=SourceHttpAuth(source, context, discovery_endpoint),
        installation_id="synthetic-index",
        transport=httpx.MockTransport(handler),
    )
    return client, clock, source, capability


def response(**changes):
    return {
        "account": "synthetic", "host": "github.com", "token": "synthetic-native-token",
        "lease_contract": wire.LEASE_CONTRACT, "lease_id": "b" * 32,
        "lease_issued_at": 999.0, "lease_expires_at": 1059.0,
        "lease_remaining_seconds": 55.0, "installationId": "synthetic-index", **changes,
    }


def discovery(**changes):
    return {
        "status": "ok", "plugin": "agent-index", "version": "fixture",
        "source_credentials_contract": wire.HTTP_SUPPORT, "installationId": "synthetic-index",
        **changes,
    }


@pytest.mark.asyncio
async def test_real_injector_and_caller_capability_rotate_without_token_fallback(tmp_path, monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        return httpx.Response(200, json=response())
    client, clock, source, capability = fixture_client(tmp_path, monkeypatch, handler)
    first = await client.acquire()
    capability.write_text("c" * 64, encoding="ascii")
    second = await client.acquire()
    assert [request.method for request in requests] == ["GET", "POST", "GET", "POST"]
    assert [json.loads(request.content)["capability"] for request in requests[1::2]] == [
        "a" * 64, "c" * 64,
    ]
    assert all(request.headers["Authorization"].split() == [
        "Bearer", "synthetic-http-credential",
    ] for request in requests)
    assert len(source.calls) == 4
    assert [call["path"] for call in source.calls] == [
        "health", "api/v1/source-credentials", "health", "api/v1/source-credentials",
    ]
    assert all(request.headers[wire.INSTALLATION_HEADER] == "synthetic-index" for request in requests)
    assert first.remaining_seconds() == second.remaining_seconds() == 55
    assert first.token not in repr(first)


@pytest.mark.asyncio
@pytest.mark.parametrize("support,status", [
    ({}, 200), ({"lease_contract": wire.LEASE_CONTRACT}, 200),
    (discovery(source_credentials_contract={**wire.HTTP_SUPPORT, "exchange": "other"}), 200),
    (discovery(installationId="different-index"), 200), (discovery(), 302),
])
async def test_unsupported_peer_never_reads_capability_or_posts(tmp_path, monkeypatch, support, status):
    methods = []
    def handler(request):
        methods.append(request.method)
        return httpx.Response(status, json=support, headers={"Location": "https://other.example.com/"})
    client, _, _, capability = fixture_client(tmp_path, monkeypatch, handler)
    capability.unlink()
    with pytest.raises(wire.SourceLeaseError):
        await client.acquire()
    assert methods == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"account": "other"}, {"host": "other.example.com"}, {"token": "invalid\nvalue"},
    {"lease_id": "bad"}, {"lease_issued_at": True}, {"lease_expires_at": float("inf")},
    {"lease_remaining_seconds": float("nan")}, {"lease_remaining_seconds": 61},
    {"lease_issued_at": 1001}, {"lease_contract": "other"}, {"installationId": "other"},
])
async def test_malformed_lease_is_refused_without_retry(tmp_path, monkeypatch, changes):
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        return httpx.Response(
            200, content=json.dumps(response(**changes)).encode(),
            headers={"Content-Type": "application/json"},
        )
    client, _, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    with pytest.raises(wire.SourceLeaseError):
        await client.acquire()
    assert methods == ["GET", "POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["encoding", "media-type"])
async def test_unexpected_discovery_representation_refuses_before_capability_post(tmp_path, monkeypatch, kind):
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json=response())
        raw = json.dumps(discovery()).encode()
        headers = {"Content-Type": "application/json"}
        if kind == "encoding":
            raw = gzip.compress(raw)
            headers["Content-Encoding"] = "gzip"
        else:
            headers["Content-Type"] = "text/plain"
        return httpx.Response(200, content=raw, headers=headers)
    client, _, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    with pytest.raises(wire.SourceLeaseError, match="response-invalid"):
        await client.acquire()
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_entire_exchange_latency_and_clock_regression_consume_lease(tmp_path, monkeypatch):
    def handler(request):
        clock.wall += 2
        clock.monotonic += 2
        return httpx.Response(200, json=discovery() if request.method == "GET" else response())
    client, clock, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    lease = await client.acquire()
    assert lease.remaining_seconds() == 51
    clock.monotonic += 5
    assert lease.remaining_seconds() == 46
    clock.wall -= 1
    assert lease.remaining_seconds() == 0
    clock.wall += 100
    assert lease.remaining_seconds() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["acquisition", "retention"])
async def test_wall_only_suspension_exhausts_the_conservative_budget(tmp_path, monkeypatch, stage):
    def handler(request):
        if stage == "acquisition":
            clock.wall += 28
            clock.monotonic += 0.5
        return httpx.Response(200, json=discovery() if request.method == "GET" else response())
    client, clock, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    if stage == "acquisition":
        with pytest.raises(wire.SourceLeaseError, match="expired"):
            await client.acquire()
    else:
        lease = await client.acquire()
        clock.wall += 56
        clock.monotonic += 1
        assert lease.remaining_seconds() == 0


@pytest.mark.asyncio
async def test_post_transport_failure_is_not_replayed_or_exposed(tmp_path, monkeypatch):
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        raise httpx.ReadError("synthetic-secret-provider-detail")
    client, _, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    with pytest.raises(wire.SourceLeaseError) as caught:
        await client.acquire()
    assert str(caught.value) == "source-lease-unavailable"
    assert methods == ["GET", "POST"]


@pytest.mark.asyncio
async def test_source_loss_and_recovery_never_extend_the_retained_lease(tmp_path, monkeypatch):
    available, methods = True, []

    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        if not available:
            clock.wall += 10
            clock.monotonic += 10
            return httpx.Response(503)
        return httpx.Response(200, json=response(
            lease_issued_at=clock.wall - 1, lease_expires_at=clock.wall + 59,
        ))

    client, clock, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    original = await client.acquire()
    available = False
    with pytest.raises(wire.SourceLeaseError):
        await client.acquire()
    assert methods == ["GET", "POST", "GET", "POST"]
    assert original.remaining_seconds() == 45
    clock.wall += 46
    clock.monotonic += 46
    assert original.remaining_seconds() == 0
    available = True
    fresh = await client.acquire()
    assert fresh.remaining_seconds() == 55
    assert original.remaining_seconds() == 0
    assert methods == ["GET", "POST"] * 3


@pytest.mark.asyncio
async def test_caller_deadline_cancels_an_unfinished_exchange_without_retry(tmp_path, monkeypatch):
    methods = []
    async def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        await asyncio.Event().wait()
    client, _, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    with pytest.raises(wire.SourceLeaseError, match="unavailable"):
        await client.acquire(timeout=0.05)
    assert methods == ["GET", "POST"]


@pytest.mark.skipif(os.name != "posix", reason="Native plaintext-file adapter declares POSIX only")
@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["", "// Native config header\r\n\n", "\ufeff// Native header\n"])
async def test_acquire_renew_withdraw_and_recover_preserve_other_native_config(tmp_path, monkeypatch, header):
    def handler(request):
        return httpx.Response(200, json=discovery() if request.method == "GET" else response())
    client, clock, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    original = {
        "theme": "dark", "authTokens": {
            "https://github.com:synthetic": {"token": "prior-synthetic-token", "extra": 1},
            "https://github.com:other": {"token": "other-synthetic-token"},
        },
    }
    config.write_bytes(header.encode("utf-8") + json.dumps(original).encode("utf-8"))
    config.chmod(0o600)
    def document():
        raw = config.read_bytes()
        assert raw.startswith(header.encode("utf-8"))
        return json.loads(raw[len(header.encode("utf-8")):])
    store = CopilotAccountFile(config, account="synthetic")
    lease = await store.renew(client)
    expected = json.loads(json.dumps(original))
    expected["authTokens"]["https://github.com:synthetic"]["token"] = lease.token
    assert document() == expected
    assert config.stat().st_mode & 0o777 == 0o600
    clock.monotonic += 60
    with pytest.raises(CopilotFileAuthError, match="expired"):
        store.install(lease)
    assert document() == expected
    store.withdraw()
    del expected["authTokens"]["https://github.com:synthetic"]["token"]
    assert document() == expected
    clock.wall, clock.monotonic = 1000.0, 200.0
    await store.renew(client)
    expected["authTokens"]["https://github.com:synthetic"]["token"] = lease.token
    assert document() == expected
    assert not list(root.glob(".bridge-account-auth-*"))


@pytest.mark.skipif(os.name != "posix", reason="Native plaintext-file adapter declares POSIX only")
@pytest.mark.asyncio
async def test_source_outage_preserves_file_and_fresh_renewal_uses_the_same_owner(tmp_path, monkeypatch):
    available, generation, posts = True, 1, []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=discovery())
        posts.append(json.loads(request.content))
        if not available:
            return httpx.Response(503)
        return httpx.Response(200, json=response(
            token=f"synthetic-token-{generation}", lease_id=str(generation) * 32,
            lease_issued_at=clock.wall - 1, lease_expires_at=clock.wall + 59,
        ))

    client, clock, _, _ = fixture_client(tmp_path, monkeypatch, handler)
    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    original = {
        "theme": "dark",
        "authTokens": {
            "https://github.com:synthetic": {"token": "prior-synthetic-token"},
            "https://github.com:other": {"token": "other-synthetic-token"},
        },
    }
    config.write_text(json.dumps(original), encoding="utf-8")
    config.chmod(0o600)
    history = root / "history"
    history.write_bytes(b"synthetic-native-history")
    store = CopilotAccountFile(config, account="synthetic")
    retained = await store.renew(client)
    before = config.read_bytes()
    available = False
    clock.wall += 56
    clock.monotonic += 56
    with pytest.raises(wire.SourceLeaseError):
        await store.renew(client)
    with pytest.raises(CopilotFileAuthError, match="expired"):
        store.install(retained)
    assert config.read_bytes() == before
    assert retained.remaining_seconds() == 0
    assert len(posts) == 2
    available, generation = True, 2
    fresh = await store.renew(client)
    expected = json.loads(json.dumps(original))
    expected["authTokens"]["https://github.com:synthetic"]["token"] = fresh.token
    assert json.loads(config.read_bytes()) == expected
    assert history.read_bytes() == b"synthetic-native-history"
    assert retained.remaining_seconds() == 0
    assert len(posts) == 3


@pytest.mark.skipif(os.name != "posix", reason="Native plaintext-file adapter declares POSIX only")
@pytest.mark.parametrize("kind", [
    "symlink", "public", "malformed", "header-duplicate", "nonfinite", "overflow",
    "negative-overflow", "missing-account", "hardlink",
])
def test_unsafe_native_file_is_preserved(tmp_path, monkeypatch, kind):
    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    config.write_text(json.dumps({"authTokens": {
        "https://github.com:synthetic": {"token": "old-token"},
    }}))
    config.chmod(0o600)
    if kind == "symlink":
        saved = root / "original"
        config.rename(saved)
        config.symlink_to(saved)
    elif kind == "public":
        config.chmod(0o644)
    elif kind == "malformed":
        config.write_text('{"authTokens":{},"authTokens":{}}')
    elif kind == "header-duplicate":
        config.write_text('// Native header\n{"authTokens":{},"authTokens":{}}')
    elif kind == "nonfinite":
        config.write_text('{"authTokens":{"https://github.com:synthetic":{"token":"old-token"}},"value":NaN}')
    elif kind in {"overflow", "negative-overflow"}:
        number = "1e309" if kind == "overflow" else "-1e309"
        config.write_text(
            '{"authTokens":{"https://github.com:synthetic":{"token":"old-token"}},"value":'
            + number + "}",
        )
    elif kind == "missing-account":
        config.write_text('{"authTokens":{}}')
    elif kind == "hardlink":
        os.link(config, root / "alias")
    original = config.read_bytes()
    lease = wire.HttpSourceLease("new-token", "synthetic", "github.com", "b" * 32, 1, 2000, 1000, 200, 60)
    monkeypatch.setattr(wire, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: 200))
    with pytest.raises(CopilotFileAuthError):
        CopilotAccountFile(config, account="synthetic").install(lease)
    assert config.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="Native plaintext-file staging")
@pytest.mark.parametrize("characters,oversized", [(100000, False), (180000, True)])
def test_serialized_size_cannot_lock_the_owner_out_of_renewal_or_withdrawal(
    tmp_path, monkeypatch, characters, oversized,
):
    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    original = {
        "label": "\u00e9" * characters,
        "authTokens": {"https://github.com:synthetic": {"token": "old-token"}},
    }
    config.write_bytes(json.dumps(original, ensure_ascii=False).encode("utf-8"))
    config.chmod(0o600)
    before = config.read_bytes()
    assert len(before) < 1024 * 1024
    monkeypatch.setattr(wire, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: 200))
    lease = wire.HttpSourceLease(
        "new-token", "synthetic", "github.com", "b" * 32, 999, 1060, 1000, 200, 60,
    )
    store = CopilotAccountFile(config, account="synthetic")
    if oversized:
        with pytest.raises(CopilotFileAuthError, match="too-large"):
            store.install(lease)
        assert config.read_bytes() == before
    else:
        store.install(lease)
        assert config.stat().st_size <= 1024 * 1024
        store.withdraw()
        assert "token" not in json.loads(config.read_bytes())["authTokens"]["https://github.com:synthetic"]
        store.install(lease)
        assert json.loads(config.read_bytes())["label"] == original["label"]
    assert not list(root.glob(".bridge-account-auth-*"))


@pytest.mark.skipif(os.name != "posix", reason="Native plaintext-file staging")
def test_exclusive_stage_collision_preserves_the_existing_file(tmp_path, monkeypatch):
    from agent_bridge import copilot_file_auth as storage

    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    config.write_text('{"authTokens":{"https://github.com:synthetic":{"token":"old-token"}}}')
    config.chmod(0o600)
    collision = root / ".bridge-account-auth-collision"
    collision.write_bytes(b"existing-other-writer-stage")
    before = config.read_bytes()
    monkeypatch.setattr(storage.secrets, "token_hex", lambda _: "collision")
    monkeypatch.setattr(wire, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: 200))
    lease = wire.HttpSourceLease(
        "new-token", "synthetic", "github.com", "b" * 32, 999, 1060, 1000, 200, 60,
    )
    with pytest.raises(CopilotFileAuthError):
        CopilotAccountFile(config, account="synthetic").install(lease)
    assert collision.read_bytes() == b"existing-other-writer-stage"
    assert config.read_bytes() == before


@pytest.mark.skipif(os.name == "posix", reason="Other platforms must refuse before acquisition")
def test_native_file_adapter_refuses_unsupported_platform(tmp_path):
    with pytest.raises(CopilotFileAuthError, match="platform-unsupported"):
        CopilotAccountFile(tmp_path / "config.json", account="synthetic")


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO refusal")
def test_native_fifo_is_opened_nonblocking_then_refused(tmp_path, monkeypatch):
    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    os.mkfifo(config, 0o600)
    original_open = os.open
    def guarded_open(path, flags, *args, **kwargs):
        if path == "config.json":
            assert flags & os.O_NONBLOCK, "A FIFO must never reach a blocking open"
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", guarded_open)
    monkeypatch.setattr(wire, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: 200))
    lease = wire.HttpSourceLease(
        "new-token", "synthetic", "github.com", "b" * 32, 999, 1060, 1000, 200, 60,
    )
    with pytest.raises(CopilotFileAuthError):
        CopilotAccountFile(config, account="synthetic").install(lease)
    assert stat.S_ISFIFO(config.lstat().st_mode)


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO refusal")
@pytest.mark.asyncio
async def test_capability_fifo_refuses_without_post(tmp_path, monkeypatch):
    methods = []
    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json=discovery())
    client, _, _, capability = fixture_client(tmp_path, monkeypatch, handler)
    capability.unlink()
    os.mkfifo(capability, 0o600)
    with pytest.raises(wire.SourceLeaseError):
        await client.acquire(timeout=1)
    assert methods == ["GET"]


@pytest.mark.skipif(os.name != "posix", reason="Native plaintext-file adapter declares POSIX only")
@pytest.mark.parametrize("change", ["foreign-token", "wrong-account", "expires-during-write"])
def test_native_renewal_refuses_changed_custody_and_last_moment_expiry(tmp_path, monkeypatch, change):
    root = tmp_path / ".copilot"
    root.mkdir(mode=0o700)
    config = root / "config.json"
    config.write_text('{"authTokens":{"https://github.com:synthetic":{"token":"old-token"}}}')
    config.chmod(0o600)
    clock = SimpleNamespace(wall=1000, monotonic=200)
    monkeypatch.setattr(wire, "time", SimpleNamespace(
        time=lambda: clock.wall, monotonic=lambda: clock.monotonic,
    ))
    def lease(account="synthetic"):
        return wire.HttpSourceLease(
            "new-token", account, "github.com", "b" * 32, 999, 1060, 1000, 200, 60,
        )
    store = CopilotAccountFile(config, account="synthetic")
    if change == "foreign-token":
        store.install(lease())
        config.write_text('{"authTokens":{"https://github.com:synthetic":{"token":"foreign-token"}}}')
    elif change == "expires-during-write":
        original_dump = json.dump
        def delayed_dump(*args, **kwargs):
            original_dump(*args, **kwargs)
            clock.monotonic += 61
        monkeypatch.setattr(json, "dump", delayed_dump)
    before = config.read_bytes()
    with pytest.raises(CopilotFileAuthError):
        store.install(lease("other" if change == "wrong-account" else "synthetic"))
    assert config.read_bytes() == before
    assert not list(root.glob(".bridge-account-auth-*"))
    if change == "foreign-token":
        with pytest.raises(CopilotFileAuthError, match="owner-changed"):
            store.withdraw()
        assert config.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_delay", [0, 5.2])
async def test_real_https_requires_trust_and_preserves_fixed_account_exchange(tmp_path, provider_delay):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert, private = tmp_path / "certificate.pem", tmp_path / "key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    serving = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    serving.load_cert_chain(cert, private)
    requests, tasks = [], set()

    async def handle(reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            lines = raw.decode("ascii").split("\r\n")
            method, path, _ = lines[0].split()
            headers = dict(line.split(": ", 1) for line in lines[1:] if line)
            length = int(headers.get("Content-Length", "0"))
            assert 0 <= length <= 4096
            body = await asyncio.wait_for(reader.readexactly(length), 3)
            requests.append((method, path, headers, body))
            if method == "POST":
                await asyncio.sleep(provider_delay)
            issued = time.time()
            assert headers[wire.INSTALLATION_HEADER] == "synthetic-index"
            assert (method, path) in {("GET", "/health"), ("POST", "/api/v1/source-credentials")}
            value = discovery() if method == "GET" else response(
                lease_issued_at=issued, lease_expires_at=issued + 60,
                lease_remaining_seconds=60,
            )
            data = json.dumps(value).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        tasks.add(asyncio.create_task(handle(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0, ssl=serving)
    try:
        port = server.sockets[0].getsockname()[1]
        endpoint = f"https://localhost:{port}/api/v1/source-credentials"
        context = HttpAuthContext(f"https://localhost:{port}", "synthetic", "generation")
        capability = tmp_path / "capability"
        capability.write_text("a" * 64)
        kwargs = dict(
            auth=SourceHttpAuth(Source(), context, endpoint), context=context, source="example",
            account="synthetic", recipient="worker", host="github.com", capability_file=capability,
            discovery_endpoint=f"https://localhost:{port}/health",
            discovery_auth=SourceHttpAuth(Source(), context, f"https://localhost:{port}/health"),
            discovery_context=context, installation_id="synthetic-index",
        )
        with pytest.raises(wire.SourceLeaseError):
            await wire.HttpSourceLeaseClient(endpoint, **kwargs).acquire(timeout=5)
        assert requests == []
        trust = ssl.create_default_context(cafile=str(cert))
        lease = await wire.HttpSourceLeaseClient(endpoint, tls_context=trust, **kwargs).acquire(timeout=8)
        assert 0 < lease.remaining_seconds() < 60
        assert [item[0] for item in requests] == ["GET", "POST"]
        assert [item[1] for item in requests] == ["/health", "/api/v1/source-credentials"]
        assert all(item[2]["Authorization"].split() == [
            "Bearer", "synthetic-http-credential",
        ] for item in requests)
        assert json.loads(requests[1][3])["capability"] == "a" * 64
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*tasks)
