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
def test_native_windows_protected_cache_supports_synthetic_unreadable_caller(tmp_path: Path) -> None:
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
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from agent_vault import cache, cli, config, credential_reads, extensions, kek, service
from agent_vault.cache_dpapi import CacheDecryptionUnavailable
from agent_vault.extensions import ExtensionRegistry

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
""", str(tmp_path), receipt],
        env=env, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    if result.returncode == 77:
        pytest.skip(f"native DPAPI unavailable in this logon: {result.stdout.strip()}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == receipt, "native child did not return its unique qualification receipt"
