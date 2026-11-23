"""Native protection is qualified in a fresh process, without synthetic crypto."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from agent_procutil import no_window_kwargs

from agent_vault import cache


@pytest.mark.skipif(os.name != "nt", reason="native Windows protection")
@pytest.mark.parametrize("population", ["get", "unlock-warm", "request-warm"])
def test_native_windows_protected_cache_supports_synthetic_unreadable_caller(
    tmp_path: Path, population: str,
) -> None:
    receipt = "native-cache-qualified:" + uuid.uuid4().hex
    env = dict(os.environ)
    env.update(
        AGENT_VAULT_CACHE="1",
        AGENT_VAULT_CACHE_DIR=str(tmp_path / "cache"),
        AGENT_VAULT_HOME=str(tmp_path / "home"),
        PYTHONPATH=str(Path(cache.__file__).resolve().parents[1]),
    )
    env.pop("AGENT_VAULT_PINNED_CONTEXT", None)
    result = subprocess.run(
        [sys.executable, "-c", """
import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from agent_vault import cache, cache_warm, cli, config, configuration, configuration_scope, credential_reads, extensions, kek, service
from agent_vault.cache_dpapi import CacheDecryptionUnavailable
from agent_vault.cache_warm_selection import CONTRACT, FIELD
from agent_vault.extensions import ExtensionRegistry
from agent_vault.service_connections import VaultConnections

try:
    kek._dpapi(True, b"synthetic-protection-preflight")
except kek.KekError as exc:
    print(str(exc))
    sys.exit(77)
registry = ExtensionRegistry()
registry._loaded = True
extensions._REGISTRY = registry
kpdb = str(Path(sys.argv[1]) / "fixture.kdbx")
clock = [1000.0]
service.time.time = lambda: clock[0]
service.PASSWORD_TTL = 10
service.resolve_kpdb = lambda **_kw: kpdb
config.resolve_context = lambda **_kw: SimpleNamespace(group="")
vault = service.VaultService()
vault.cli.set_password(kpdb, "synthetic-master")
vault._password_set_at[kpdb] = clock[0]
vault.cli.get_entry = lambda *_args: "synthetic-native-value"
if sys.argv[3] in ("unlock-warm", "request-warm"):
    request = {
        "action": "unlock", "kpdb": kpdb, "group": "", "password": "synthetic-master",
    }
    if sys.argv[3] == "request-warm":
        root = Path(sys.argv[1])
        selected, resident = root / "selected", root / "resident"
        for directory, target in ((selected, "entry"), (resident, "WRONG")):
            directory.mkdir()
            (directory / ".agent-vault.json").write_text(json.dumps({"cache_entries": [target]}))
        configuration_scope.bind_service_configuration(str(resident))
        configuration.owner_report = lambda _root: None
        os.environ["AGENT_VAULT_CONFIG"] = str(root / "native.json")
        request[FIELD] = {
            "contract": CONTRACT, "version": 1, "root": str(selected), "native_default": False,
        }
    else:
        registry.register_cache_source(lambda _machine: ["entry"])
    vault.cli.verify_password = lambda *_args: True
    async def warm() -> None:
        connections = VaultConnections(vault, service.handle_client)
        listener = await asyncio.start_server(connections.callback("tcp"), "127.0.0.1", 0)
        host, port = listener.sockets[0].getsockname()[:2]
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, connections)
        async def send(payload: dict[str, object]) -> dict[str, object]:
            reader, writer = await asyncio.open_connection(host, port)
            try:
                writer.write((json.dumps(payload) + "\\n").encode())
                await writer.drain()
                response = json.loads(await asyncio.wait_for(reader.readline(), 5))
                assert isinstance(response, dict)
                return response
            finally:
                writer.close()
                await writer.wait_closed()
        try:
            metadata = await send({"action": "ping", "health_only": True})
            assert metadata["cache_warm_selection_versions"] == [1]
            accepted = await send(request)
            assert accepted["ok"], accepted
            if FIELD in request:
                assert accepted["cache_warm"]["state"] == "queued", accepted
            runtime.tick()
            assert runtime.future is not None
            await asyncio.wait_for(asyncio.shield(runtime.future), 5)
            status = (await send({**request, "action": "ping"}))["cache_warm"]
            assert status["state"] == "complete" and status["cached"] == 1, status
        finally:
            await runtime.close()
            await connections.close([listener], [], lambda: None)
            assert not connections.clients and not connections.workers and not connections.work
    asyncio.run(warm())
    def no_backend(*_args: object) -> str:
        raise AssertionError("warmed native hit reached the backend")
    vault.cli.get_entry = no_backend
first = vault.handle_request({"action": "get", "entry": "entry", "group": ""})
assert first["ok"] and "cache_warning" not in first, first
blob = (Path(sys.argv[1]) / "cache" / "credential-cache.enc").read_bytes()
assert b"synthetic-native-value" not in blob
clock[0] += 10
def no_unlock(*_a, **_kw):
    raise AssertionError("native cache hit tried to unlock")
vault.ensure_unlocked = no_unlock
class UnreadableCaller:
    enabled = True
    def get(self, *_a, **_kw):
        raise CacheDecryptionUnavailable("synthetic caller cannot decrypt")
cache.get_cache = UnreadableCaller
cli.ensure_service = lambda: True
with ThreadPoolExecutor(max_workers=1) as owner:
    cli.send_command = lambda request, timeout=None: owner.submit(
        vault.handle_request, {**request, "group": ""},
    ).result(timeout=5)
    response = credential_reads.read_credential(SimpleNamespace(entry="entry", field="password"))
assert response.code == 0 and response.value == first["value"], response
assert not vault.cli.has_password(kpdb)
print(sys.argv[2])
""", str(tmp_path), receipt, population],
        env=env, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    if result.returncode == 77:
        pytest.skip(f"native DPAPI unavailable in this logon: {result.stdout.strip()}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == receipt, "native child did not return its unique qualification receipt"
