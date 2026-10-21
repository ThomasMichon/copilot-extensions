"""Real payload shims retain only validated database/cell identity-cache pins."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import site
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from agent_vault.cache import PersistentCache
from agent_vault.identity_context import identity_cache_digest, validate_payload_cache

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prepare_payload(root, mode):
    home = root / "home"
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-vault"
    for directory in ("bin", "scripts", "src"):
        shutil.copytree(PLUGIN / directory, payload / directory,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(PLUGIN / "plugin.json", payload / "plugin.json")
    write_json(home / ".copilot" / "settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    })
    legacy = home / ".agent-vault"
    environment = {"HOME": str(home), "USERPROFILE": str(home)}
    if os.environ.get("WSL_DISTRO_NAME"):
        environment["WSL_DISTRO_NAME"] = os.environ["WSL_DISTRO_NAME"]
    runtime = legacy
    installation_id = ""
    context = ""
    if mode == "namespaced":
        primitive = load_module(
            payload / "scripts" / "installation-context" / "installation_context.py",
            "vault_gate_installation_primitive",
        )
        stamped = primitive.stamp_context(
            payload_version="1.0.0", payload_origin="installed",
            expected_namespace_generation=0, expected_install_generation=0,
            payload_root=payload, plugin_id="agent-vault", environment=environment,
            copilot_home=home / ".copilot", durable_home=home / ".copilot-extensions",
        )
        primitive.compare_and_swap_activation(
            context=stamped["installReceipt"], expected_marketplace_id=stamped["marketplaceId"],
            expected_plugin_id="agent-vault",
            expected_namespace_generation=stamped["namespaceGeneration"],
            expected_install_generation=stamped["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=legacy, environment=environment,
            durable_home=home / ".copilot-extensions",
        )
        installation_id = stamped["marketplaceId"] + "/agent-vault"
        context = str(stamped["installReceipt"])
        write_json(home / ".copilot-extensions" / "installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": True, "marketplaces": {}},
        })
        resolved = primitive.resolve_installation_mode(
            legacy_root=legacy, payload_root=payload, plugin_id="agent-vault",
            context=context, durable_home=home / ".copilot-extensions", environment=environment,
        )
        runtime = Path(resolved["runtimeRoot"])
    return home, payload, runtime, installation_id, context


def prepare_runtime(runtime, payload):
    slot = runtime / "versions" / "fixture"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", "--system-site-packages", str(slot)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    package_dir = (
        slot / "Lib" / "site-packages" if os.name == "nt"
        else next((slot / "lib").glob("python*/site-packages"))
    )
    sources = [
        payload / "src", REPO / "libs" / "agent-procutil" / "src",
        REPO / "libs" / "zdd" / "src", REPO / "libs" / "single-instance-lease" / "src",
        *map(Path, site.getsitepackages()),
    ]
    (package_dir / "fixture-sources.pth").write_text(
        "\n".join(map(str, sources)) + "\n", encoding="utf-8",
    )
    write_json(slot / ".install-complete.json", {
        "version": "fixture", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
    })
    (runtime / "current-version").write_text("fixture\n", encoding="utf-8")


def launcher(payload):
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c",
                str(payload / "bin" / "agent-vault.cmd")]
    return [str(payload / "bin" / "agent-vault")]


def invoke(payload, env, cwd, *args):
    return subprocess.run(
        [*launcher(payload), *args], env=env, cwd=cwd,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
    )


def test_shared_payload_pin_validator_rejects_foreign_cell_and_invalid_json(tmp_path):
    runtime = tmp_path / "runtime"
    database = tmp_path / "fixture.kdbx"
    pin = json.dumps({
        "version": 1, "kpdb": str(database), "group": "", "vault_name": "", "port": 0,
    })
    installation_id = "fixture/agent-vault"
    directory = runtime / "cache" / "identity" / identity_cache_digest(
        str(database), str(runtime), installation_id,
    )
    arguments = (pin, "1", str(directory), str(runtime), installation_id,
                 str(runtime), installation_id)
    assert validate_payload_cache(*arguments) == str(directory.resolve())
    with pytest.raises(RuntimeError):
        validate_payload_cache(pin, "1", str(directory), str(runtime), "foreign",
                               str(runtime), installation_id)
    with pytest.raises(RuntimeError):
        validate_payload_cache("1", *arguments[1:])


@pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_IDENTITY_PAYLOAD_GATE") != "1",
    reason="opt-in native payload-launcher contract",
)
@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_actual_payload_cache_only_reader_preserves_two_database_scopes(tmp_path, monkeypatch, mode):
    if os.name != "nt":
        pytest.importorskip("cryptography.fernet", reason="POSIX cache reads require the cache extra")
    home, payload, runtime, installation_id, context = prepare_payload(tmp_path, mode)
    prepare_runtime(runtime, payload)
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("AGENT_VAULT", "VAULT_", "IDENTITY_", "COPILOT_EXTENSIONS_"))
        and name not in ("KPDB", "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT")
    }
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "COPILOT_PLUGIN_ROOT": str(payload),
        "AGENT_VAULT_HOME": str(runtime), "AGENT_VAULT_CACHE": "1",
    })
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = context
    contexts = []
    values = ("fixture-first-database-value", "fixture-second-database-value")
    for index, value in enumerate(values):
        database = tmp_path / f"database-{index}.kdbx"
        database.write_bytes(b"disposable metadata fixture")
        candidate = dict(
            env, KPDB=str(database),
            AGENT_VAULT_CACHE_DIR=str(tmp_path / "unvalidated-cache-override"),
        )
        result = invoke(payload, candidate, tmp_path, "identity-key", "context", "--json")
        assert result.returncode == 0, result.stderr
        metadata = json.loads(result.stdout)
        contexts.append(metadata)
        directory = metadata["env"]["AGENT_VAULT_CACHE_DIR"]
        assert Path(directory).parent.parent == runtime / "cache"
        assert not Path(directory).exists()
        monkeypatch.setenv("AGENT_VAULT_CACHE", "1")
        assert PersistentCache(directory).put("Identity/PR-attribution", "password", value)
    assert contexts[0]["env"]["AGENT_VAULT_CACHE_DIR"] != contexts[1]["env"]["AGENT_VAULT_CACHE_DIR"]
    for metadata, value in zip(contexts, values, strict=True):
        pinned = dict(env, **metadata["env"])
        result = invoke(payload, pinned, tmp_path,
                        "get", "Identity/PR-attribution", "password", "--cache-only")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == value
    invalid = dict(env, **contexts[0]["env"])
    invalid["AGENT_VAULT_CACHE_DIR"] = str(tmp_path / "foreign-cache")
    refused = invoke(payload, invalid, tmp_path,
                     "get", "Identity/PR-attribution", "password", "--cache-only")
    assert refused.returncode == 126
    assert refused.stdout == ""


@pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_IDENTITY_PAYLOAD_GATE") != "1",
    reason="opt-in native payload-launcher contract",
)
@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_actual_first_touch_require_running_never_provisions(tmp_path, mode):
    home, payload, runtime, _installation_id, context = prepare_payload(tmp_path, mode)
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("AGENT_VAULT", "VAULT_", "IDENTITY_", "COPILOT_EXTENSIONS_"))
        and name not in ("KPDB", "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT")
    }
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_PLUGIN_ROOT=str(payload),
               AGENT_VAULT_HOME=str(runtime))
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = context
    result = invoke(payload, env, tmp_path,
                    "identity-key", "setup", "--require-running", "--json")
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["error"] == "service_unavailable"
    assert not (runtime / ".provision-status").exists()
    assert not (runtime / "versions").exists()


@pytest.mark.skipif(
    os.environ.get("AGENT_VAULT_IDENTITY_REAL_KEEPASS") != "1",
    reason="opt-in actual payload/KeePass proof",
)
@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_actual_payload_live_setup_and_stopped_service_refusal(tmp_path, mode):
    live = load_module(PLUGIN / "tests" / "test_identity_key_keepass_live.py", "vault_gate_live")
    home, payload, runtime, installation_id, context = prepare_payload(tmp_path, mode)
    prepare_runtime(runtime, payload)
    suffix = hashlib.sha256(
        str(runtime).replace("\\", "/").lower().rstrip("/").encode()
    ).hexdigest()[:12]
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    write_json(tmp_path / "vault-config.json", {"port": port})
    updates = {
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "COPILOT_EXTENSIONS_CONTEXT": context,
        "AGENT_VAULT_HOME": str(runtime), "AGENT_VAULT_INSTALLATION_ID": installation_id,
        "AGENT_VAULT_RUN_DIR": str(runtime / "run"),
        "AGENT_VAULT_CACHE_DIR": str(runtime / "cache"),
        "AGENT_VAULT_CONFIG": str(tmp_path / "vault-config.json"),
        "AGENT_VAULT_PORT": "0" if installation_id else str(port),
        "AGENT_VAULT_PIPE": rf"\\.\pipe\agent-vault-{suffix}",
        "AGENT_VAULT_SOCKET": str(tmp_path / "vault.sock"),
        "AGENT_VAULT_PID": str(runtime / "run" / "agent-vault-service.pid"),
        "AGENT_VAULT_LOG": str(runtime / "logs" / "agent-vault-service.log"),
    }
    with live.disposable_vault(tmp_path, runtime_environment=updates) as fixture:
        metadata_result = invoke(payload, fixture.env, tmp_path, "identity-key", "context", "--json")
        live.require(metadata_result.returncode == 0, "Actual payload context failed")
        metadata = json.loads(metadata_result.stdout)
        selected = dict(fixture.env, **metadata["env"])
        setup = invoke(payload, selected, tmp_path,
                       "identity-key", "setup", "--entry", fixture.entry, "--require-running", "--json")
        live.require(setup.returncode == 0, "Actual payload existing-key setup failed")
        live.require(json.loads(setup.stdout)["status"] == "reused", "Actual payload rotated owner")
        read = invoke(payload, selected, tmp_path,
                      "get", fixture.entry, "password", "--cache-only")
        live.require(read.returncode == 0 and read.stdout.strip() == fixture.expected_key,
                     "Actual payload fresh reader did not select populated scoped cache")
        live.require(fixture.process.poll() is None, "Existing independent resident was killed")
        fixture.request({"action": "stop"})
        fixture.process.wait(timeout=10)
        stopped = invoke(payload, selected, tmp_path,
                         "identity-key", "setup", "--entry", fixture.entry,
                         "--require-running", "--json")
        live.require(stopped.returncode == 1, "Stopped resident was implicitly activated")
        live.require(json.loads(stopped.stdout)["error"] == "service_unavailable",
                     "Stopped resident did not return actionable refusal")
        live.require(fixture.process.poll() is not None, "Stopped resident changed process state")
