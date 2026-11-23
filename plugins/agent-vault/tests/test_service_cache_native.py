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
@pytest.mark.parametrize(
    "population", ["get", "unlock-warm", "request-warm", "request-read", "query-show",
                   "client-add", "client-unlock", "client-legacy"],
)
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
import contextlib
import io
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
from agent_vault.invocation import use_configuration_root
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
vault.cli.show_entry = lambda *_args, **_kw: "synthetic-query-output"
vault.cli.add_entry = lambda *_args, **_kw: (True, "fixture-created")
if sys.argv[3] in ("unlock-warm", "request-warm", "request-read", "query-show", "client-add", "client-unlock", "client-legacy"):
    request = {
        "action": "unlock", "kpdb": kpdb, "group": "", "password": "synthetic-master",
    }
    if sys.argv[3] in ("request-warm", "request-read", "query-show", "client-add", "client-unlock", "client-legacy"):
        root = Path(sys.argv[1])
        selected, resident = root / "selected", root / "resident"
        targets = (
            ((selected, "requested-only"), (resident, "entry"))
            if sys.argv[3] == "client-legacy"
            else ((selected, "entry"), (resident, "WRONG"))
        )
        for directory, target in targets:
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
    if sys.argv[3] in ("request-read", "query-show", "client-add"):
        request = {**request, "action": "get", "entry": "direct", "field": "password"}
        if sys.argv[3] == "query-show":
            request["action"] = "show"
            request.pop("field")
        if sys.argv[3] == "client-add":
            request["action"] = "add"
            request.pop("field")
        request.pop("password")
        Path(kpdb).touch()
        vault.cli.clear_password(kpdb)
        vault._password_set_at.pop(kpdb)
        registry.register_unlock_provider(lambda _context: "synthetic-master")
    if sys.argv[3] == "client-legacy":
        request.pop(FIELD)
        Path(kpdb).touch()
        vault.cli.clear_password(kpdb)
        vault._password_set_at.pop(kpdb)
        registry.register_unlock_provider(lambda _context: "synthetic-master")
        generation_health = vault._generation_health
        vault._generation_health = lambda: {
            key: value for key, value in generation_health().items()
            if key != "cache_warm_selection_versions"
        }
        dispatch = vault._dispatch_request
        def legacy_dispatch(request: dict[str, object], peer: str, transport: str) -> dict:
            assert FIELD not in request, "caller emitted an unnegotiated field"
            return dispatch(request, peer, transport)
        vault._dispatch_request = legacy_dispatch
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
            if sys.argv[3] == "client-legacy":
                assert "cache_warm_selection_versions" not in metadata
            else:
                assert metadata["cache_warm_selection_versions"] == [1]
            if sys.argv[3] == "request-read":
                assert metadata["cache_warm_read_selection_versions"] == [1]
            if sys.argv[3] in ("client-unlock", "client-legacy", "request-read", "query-show", "client-add"):
                def no_prompt(*_args: object) -> str:
                    raise AssertionError("already unlocked client prompted")
                def no_verify(*_args: object) -> bool:
                    raise AssertionError("already unlocked client verified a new password")
                config.resolve_context = lambda **_kw: config.ResolvedVault(
                    None, kpdb, "", port, {"port": "flag"},
                )
                if sys.argv[3] not in ("request-read", "query-show", "client-add"):
                    cli.ensure_service = lambda: True
                cli._discover_endpoint = lambda *_a, **_kw: None
                cli._read_password_from_tty = no_prompt
                cli.prompt_password = no_prompt
                if sys.argv[3] == "client-unlock":
                    vault.cli.verify_password = no_verify
                os.environ["AGENT_VAULT_HOST"] = "127.0.0.1"
                clock[0] = 1001.0
                output = io.StringIO()
                errors = io.StringIO()
                with use_configuration_root(str(selected)), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    if sys.argv[3] == "request-read":
                        code = await asyncio.to_thread(
                            cli.cmd_get, SimpleNamespace(entry="direct", field="password"),
                        )
                    elif sys.argv[3] == "query-show":
                        code = await asyncio.to_thread(
                            cli.cmd_show, SimpleNamespace(entry="direct", show_protected=True),
                        )
                    elif sys.argv[3] == "client-add":
                        code = await asyncio.to_thread(cli.cmd_add, SimpleNamespace(
                            entry="direct", username="", url="", generate=False,
                            password="fixture-entry-password",
                        ))
                    else:
                        code = await asyncio.to_thread(cli.cmd_unlock, SimpleNamespace())
                expected_output = {
                    "request-read": "synthetic-native-value\\n", "query-show": "synthetic-query-output",
                    "client-add": "fixture-created\\n",
                }.get(sys.argv[3], "Vault available\\n")
                assert code == 0 and output.getvalue() == expected_output
                if sys.argv[3] == "client-legacy":
                    assert "legacy warming policy" in errors.getvalue()
                    assert vault._password_set_at[kpdb] == 1001.0
                elif sys.argv[3] == "client-unlock":
                    assert errors.getvalue() == ""
                    assert vault._password_set_at[kpdb] == 1000.0
                else:
                    assert errors.getvalue() == ""
                    accepted = await send({"action": "ping", FIELD: request[FIELD], "kpdb": kpdb, "group": ""})
                    assert accepted["cache_warm"]["state"] == "queued", accepted
                    assert accepted["cache_warm"]["request_selection_version"] == 1
            else:
                accepted = await send(request)
                assert accepted["ok"], accepted
                if FIELD in request:
                    assert accepted["cache_warm"]["state"] == "queued", accepted
            runtime.tick()
            assert runtime.future is not None
            await asyncio.wait_for(asyncio.shield(runtime.future), 5)
            status_request = {key: value for key, value in request.items() if key != "password"}
            status_request["action"] = "ping"
            status = (await send(status_request))["cache_warm"]
            assert status["state"] == "complete" and status["cached"] == 1, status
            if sys.argv[3] in ("client-unlock", "client-legacy"):
                output = io.StringIO()
                with use_configuration_root(str(selected)), contextlib.redirect_stdout(output):
                    code = await asyncio.to_thread(cli.cmd_ping, SimpleNamespace())
                assert code == 0
                expected = "unknown" if sys.argv[3] == "client-legacy" else "complete"
                assert "cache warm=" + expected in output.getvalue(), output.getvalue()
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
