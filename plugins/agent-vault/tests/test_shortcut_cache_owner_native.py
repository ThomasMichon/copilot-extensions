"""Composed native shortcuts retain their addressed protected-cache owner."""
from __future__ import annotations

from collections.abc import Callable
import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import ModuleType
from typing import cast

import pytest
from agent_procutil import no_window_kwargs
from agent_vault.cache import PersistentCache

PLUGIN = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("process")]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_fixtures = _load(PLUGIN / "tests" / "test_identity_payload_gate.py", "shortcut_identity_fixtures")
_environment = _load(
    PLUGIN / "tests" / "test_bootstrap_check_reconcile_always_on.py", "shortcut_environment_fixtures",
)
_prepare_payload = cast(Callable[[Path, str], tuple[Path, Path, Path, str, str]], _fixtures.prepare_payload)
_prepare_runtime = cast(Callable[[Path, Path], None], _fixtures.prepare_runtime)
_clean_env = cast(Callable[[dict[str, str]], dict[str, str]], _environment._clean_env)


def _build_shortcut(
    home: Path, payload: Path, runtime: Path, local_bin: Path, env: dict[str, str],
) -> list[str]:
    engine = payload / "scripts"
    if os.name == "nt":
        host = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")

        def quote(value: object) -> str:
            return "'" + str(value).replace("'", "''") + "'"

        command = (
            "$ErrorActionPreference='Stop'; . " + quote(engine / "installer-engine.ps1") + "; "
            "function Write-Ok { param([string]$Message) }; "
            "Write-SimpleBinstub -CommandName agent-vault -ModuleName agent_vault "
            f"-RuntimeRoot {quote(runtime)} -LocalBin {quote(local_bin)} "
            f"-InstallBinDir {quote(runtime / 'bin')} -SnapshotInstallerPath 'scripts\\install.ps1' "
            "-NoSelfProvisionEnv AGENT_VAULT_NO_SELFPROVISION "
            f"-ResolverPs1Source {quote(engine / 'resolve-runtime.ps1')} "
            f"-ResolverShSource {quote(engine / 'resolve-runtime.sh')}"
        )
        build = [host, "-NoProfile", "-NonInteractive", "-Command", command]
        launch = [host, "-NoProfile", "-NonInteractive", "-File", str(local_bin / "agent-vault.ps1")]
    else:
        values = (
            "agent-vault", "agent_vault", runtime, local_bin, runtime / "bin",
            "scripts/install.sh", "AGENT_VAULT_NO_SELFPROVISION",
            engine / "resolve-runtime.ps1", engine / "resolve-runtime.sh",
        )
        command = (
            "set -e; _ok() { :; }; _fail() { printf '%s\\n' \"$*\" >&2; return 1; }; . "
            + shlex.quote(str(engine / "installer-engine.sh"))
            + "; write_simple_binstub " + " ".join(shlex.quote(str(value)) for value in values)
        )
        build = ["bash", "-c", command]
        launch = ["bash", str(local_bin / "agent-vault")]
    result = subprocess.run(
        build, cwd=home, env=env, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (runtime / "payload-dir").write_text(str(payload), encoding="utf-8")
    return launch


@pytest.mark.parametrize("explicit_cache", [False, True])
def test_ready_shortcut_retains_cache_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_cache: bool,
) -> None:
    home, payload, runtime, _identity, _context = _prepare_payload(tmp_path, "legacy")
    repository = PLUGIN.parents[1]
    monkeypatch.syspath_prepend(str(repository / "tools"))
    materializer = _load(repository / "tools" / "materialize_installer_engine.py", "shortcut_materializer")
    candidate = tmp_path / "release"
    composed = candidate / "plugins" / "agent-vault"
    shutil.copytree(payload, composed)
    log = materializer.materialize_refs(candidate, canonical_root=repository)
    assert not any(line.startswith("SKIP") for line in log), log
    assert not (candidate / "libs").exists()
    payload = composed
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_VAULT_CONFIG": str(home / "native.json"),
        "AGENT_VAULT_CACHE": "1", "AGENT_VAULT_NO_SELFPROVISION": "1",
    })
    (home / "native.json").write_text('{"enabled": true}', encoding="utf-8")
    launch = _build_shortcut(home, payload, runtime, tmp_path / "local-bin", env)
    _prepare_runtime(runtime, payload)
    monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
    assert PersistentCache(runtime / "cache").put("Example/item", "password", "runtime-cache")
    expected = "runtime-cache"
    if explicit_cache:
        env["AGENT_VAULT_CACHE_DIR"] = str(tmp_path / "explicit-cache")
        expected = "explicit-cache"
        assert PersistentCache(env["AGENT_VAULT_CACHE_DIR"]).put("Example/item", "password", expected)
    directories = (runtime / "cache", tmp_path / "explicit-cache")
    before = {path: path.read_bytes() for root in directories if root.exists()
              for path in root.rglob("*") if path.is_file()}

    for entry, code, value in (("Example/item", 0, expected), ("Example/missing", 1, "")):
        result = subprocess.run(
            [*launch, "get", entry, "password", "--cache-only"], cwd=home, env=env,
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert result.returncode == code, result.stdout + result.stderr
        assert result.stdout.strip() == value
    assert {path: path.read_bytes() for root in directories if root.exists()
            for path in root.rglob("*") if path.is_file()} == before
    assert not (runtime / "run").exists()
    assert not (home / "cache").exists()
