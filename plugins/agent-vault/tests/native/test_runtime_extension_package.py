"""Closed native construction discovers a separately packaged provider without activation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from .test_runtime_profile import (
    _backend,
    _closed_wheels,
    _external_wheel,
    _isolated_install_env,
    _run,
    _stage_payload,
)

PROVIDER = '''
def provider(context):
    # Escape the registry's fail-open Exception handler.
    raise SystemExit(93)

def register(registry):
    registry.register_unlock_provider(provider, name="example-adapter", priority=1)
'''

PROBE = '''
import importlib.metadata, json, pathlib, sys
import agent_vault
from agent_vault.extensions import UnlockContext, get_registry
slot = pathlib.Path(sys.prefix).resolve()
assert slot == pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(agent_vault.__file__).resolve().is_relative_to(slot)
distribution = importlib.metadata.distribution("example-vault-provider")
assert pathlib.Path(distribution.locate_file("")).resolve().is_relative_to(slot)
registry = get_registry()
assert not registry._load_failed
providers = [item for item in registry.unlock_providers if item.name == "example-adapter"]
assert len(providers) == 1
module = sys.modules[providers[0].fn.__module__]
assert pathlib.Path(module.__file__).resolve().is_relative_to(slot)
if len(sys.argv) > 2:
    registry.provide_unlock(UnlockContext("synthetic-vault"), lambda _value: True)
    raise AssertionError("registry swallowed the provider-invocation sentinel")
print(json.dumps({"installed_provider": True, "unlock_invoked": False}))
'''

CONSTRUCT = '''
import json, pathlib, subprocess, sys
payload, home, profile, wheels, uv, base_python, probe_file = map(pathlib.Path, sys.argv[1:])
sys.path.insert(0, str(payload / "scripts"))
import installer_engine as engine
import install_source
import runtime_profile
from agent_procutil import no_window_kwargs
root = home / ".agent-vault"
with install_source.wheel_inputs(profile, wheels) as inputs:
    admitted = install_source.wheel_selection(payload, "agent-vault", inputs)
    selected = runtime_profile.wheel_selection(root, payload, admitted, "ssh")
    assert "example-vault-provider" in inputs.receipt()["distributions"]
    assert "example-vault-provider" not in admitted["local_packages"]
    observations = []
    for expected_reuse in (False, True):
        with engine.numbered_candidate(
            root, selected["runtime_version"], plugin=payload, uv=uv, base_python=base_python,
            health_module="agent_vault.extensions", health_callable="get_registry",
            extras=tuple(admitted["extras"]), local_packages=tuple(admitted["local_packages"]),
            wheel_inputs=inputs,
        ) as candidate:
            assert candidate.reused is expected_reuse
            python = engine.runtime.slot_python(root, candidate.slot.name)
            assert python is not None
            probe = subprocess.run(
                [str(python), "-I", "-B", str(probe_file), str(candidate.slot)],
                cwd=home, capture_output=True, text=True, timeout=30, check=True,
                **no_window_kwargs(),
            )
            observations.append(json.loads(probe.stdout))
            if not expected_reuse:
                sentinel = subprocess.run(
                    [str(python), "-I", "-B", str(probe_file), str(candidate.slot), "invoke"],
                    cwd=home, capture_output=True, text=True, timeout=30,
                    **no_window_kwargs(),
                )
                assert sentinel.returncode == 93, "registry swallowed the provider-invocation sentinel"
            assert not (root / "current-version").exists()
            assert not (root / "deploy-manifest.json").exists()
            assert not (home / ".local" / "bin").exists()
    assert observations == [{"installed_provider": True, "unlock_invoked": False}] * 2
print(json.dumps({"constructed": True, "reused": True, "activated": False}))
'''


@pytest.mark.timeout(120)
def test_closed_native_profile_installs_an_independent_provider_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / "tools"))
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    for name in tuple(env):
        if name.startswith(("AGENT_VAULT_", "VAULT_", "KPDB")):
            env.pop(name)
    env.update({
        "AGENT_VAULT_HOME": str(home / ".agent-vault"),
        "AGENT_VAULT_CONFIG": str(home / "activation.json"),
        "UV_NO_INDEX": "1", "UV_NO_CACHE": "1",
        "UV_DEFAULT_INDEX": "https://fixture.invalid/simple",
        "PATH": str(Path(getattr(sys, "_base_executable")).parent) + os.pathsep + env["PATH"],
    })
    uv = shutil.which("uv")
    assert uv
    pool = tmp_path / "artifacts"
    pool.mkdir()
    _external_wheel(tmp_path, "psutil", "psutil", pool, uv, env)
    _external_wheel(tmp_path, "pycryptodomex", "Cryptodome", pool, uv, env)
    for package in [payload, *(payload / "libs").iterdir()]:
        if (package / "pyproject.toml").is_file():
            _backend(package)
    adapter = tmp_path / "adapter"
    (adapter / "src" / "example_vault_provider").mkdir(parents=True)
    (adapter / "src" / "example_vault_provider" / "__init__.py").write_text(PROVIDER, encoding="utf-8")
    generated = adapter / "src" / "example_vault_provider.egg-info"
    generated.mkdir()
    (generated / "PKG-INFO").write_text(
        "Metadata-Version: 2.1\nName: example-vault-provider\nVersion: 0.1.0\n",
        encoding="utf-8",
    )
    (adapter / "pyproject.toml").write_text(
        '[project]\nname="example-vault-provider"\nversion="0.1.0"\n'
        '[project.entry-points."agent_vault.extensions"]\nexample="example_vault_provider:register"\n'
        '[build-system]\nrequires=[]\nbuild-backend="offline_backend"\nbackend-path=["."]\n',
        encoding="utf-8",
    )
    _backend(adapter)
    _run([uv, "build", "--offline", "--wheel", "--out-dir", pool, adapter], env, home)
    profile, wheels, base_python, selected_uv = _closed_wheels(
        payload, env, home, "ssh", additional_roots=("example-vault-provider==0.1.0",),
    )
    probe_file = tmp_path / "provider-probe.py"
    probe_file.write_text(PROBE, encoding="utf-8")
    result = _run(
        [sys.executable, "-I", "-B", "-c", CONSTRUCT, payload, home, profile, wheels,
         selected_uv, base_python, probe_file],
        env, home,
    )
    assert json.loads(result.stdout) == {"constructed": True, "reused": True, "activated": False}
