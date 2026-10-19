"""Contained installer-only contracts; no live installation or daemon operations."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PAYLOAD = Path(__file__).resolve().parents[1]
SCRIPTS = PAYLOAD / "scripts"


def load_adapter():
    spec = importlib.util.spec_from_file_location("cell_installer_slice", SCRIPTS / "cell_install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cell(tmp_path, monkeypatch):
    adapter = load_adapter()
    api = adapter.foundation()
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    version = json.loads((PAYLOAD / "plugin.json").read_text("utf-8"))["version"]
    receipt = api.stamp_context(
        payload_root=PAYLOAD, plugin_id="agent-pull-requests",
        payload_version=version, payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        source_descriptor={"source": "github", "repo": "example/marketplace"},
        marketplace_key="example", durable_home=tmp_path / "durable", environment={},
    )
    arguments = [
        "--context", receipt["installReceipt"],
        "--durable-home", str(tmp_path / "durable"),
        "--expected-marketplace-id", receipt["marketplaceId"],
        "--snapshot-id", "snapshot-1", "--runtime-version", "runtime-1",
    ]
    snapshot = Path(receipt["snapshotsRoot"]) / "snapshot-1"
    snapshot.mkdir(parents=True)
    (snapshot / "content.txt").write_text("immutable input\n", encoding="utf-8")
    stamp = adapter.parse([
        "snapshot-stamp", *arguments,
        "--expected-namespace-generation", str(receipt["namespaceGeneration"]),
        "--expected-install-generation", str(receipt["generation"]),
    ])
    adapter.execute(stamp, api)
    return adapter, api, receipt, arguments, snapshot, home


def invoke(arguments, environment=None):
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell")
        if not host:
            pytest.skip("Native PowerShell unavailable")
        command = [host, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "install.ps1")]
    else:
        host = shutil.which("bash")
        if not host:
            pytest.skip("Native Bash unavailable")
        command = [host, str(SCRIPTS / "install.sh")]
    return subprocess.run(
        [*command, *arguments], env=environment, text=True, capture_output=True,
        timeout=20, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def native_flags(arguments):
    if os.name != "nt":
        return arguments
    return [
        "-" + "".join(part.capitalize() for part in value[2:].split("-"))
        if value.startswith("--") else value
        for value in arguments
    ]


@pytest.mark.parametrize("arguments,fragment", [
    (["slot-provision"], "explicit"),
    (["install", "--context", ""], "context"),
    (["install", "--context", "missing", "--install-dir", "elsewhere"], "cannot be combined"),
    (["install", "--context", "missing", "--force"], "immutable"),
    (["uninstall", "--context", "missing"], "invalid choice"),
])
def test_cell_entry_refuses_before_legacy_writes(tmp_path, monkeypatch, arguments, fragment):
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       COPILOT_EXTENSIONS_CONTEXT=str(home / "ambient.json"))
    result = invoke(native_flags(arguments), environment)
    assert result.returncode != 0
    assert fragment.lower() in (result.stderr + result.stdout).lower()
    assert not (home / ".agent-pull-requests").exists()
    assert not (home / ".local" / "bin").exists()
    assert not (home / "ambient.json").exists()


def test_cell_native_metadata_adapter(cell):
    _, _, _, arguments, _, home = cell
    result = invoke(native_flags(["slot-provision", *arguments]))
    assert result.returncode == 0, result.stderr
    ownership = json.loads(result.stdout)
    assert ownership["slotEmpty"] is True
    assert ownership["activated"] is False
    assert {p.name for p in Path(ownership["slotRoot"]).iterdir()} == {
        ".runtime-slot-ownership.json",
    }
    assert not (home / ".agent-pull-requests").exists()
    assert not (home / ".local" / "bin").exists()


def test_cell_ambient_pointer_does_not_change_legacy_status(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment.update(
        HOME=str(home), USERPROFILE=str(home),
        COPILOT_EXTENSIONS_CONTEXT=str(home / "not-authority.json"),
    )
    result = invoke(["status"], environment)
    assert result.returncode == 0, result.stderr
    assert "agent-pull-requests status" in result.stdout
    assert not (home / ".agent-pull-requests").exists()
    assert not (home / ".local" / "bin").exists()


@pytest.mark.parametrize("action", [
    "snapshot-validate", "slot-provision", "slot-validate", "slot-complete",
    "slot-completion-validate",
])
def test_cell_missing_or_foreign_context_fails_without_legacy_fallback(cell, action):
    adapter, api, receipt, arguments, _, home = cell
    args = adapter.parse([action, *arguments])
    args.expected_marketplace_id = "foreign--0123456789abcdef"
    with pytest.raises(ValueError):
        adapter.execute(args, api)
    args.expected_marketplace_id = receipt["marketplaceId"]
    args.context = str(home / "missing.json")
    with pytest.raises(ValueError):
        adapter.execute(args, api)
    assert not (home / ".agent-pull-requests").exists()
    assert not (home / ".local" / "bin").exists()


def test_cell_metadata_completion_is_canonical_and_immutable(cell):
    adapter, api, receipt, arguments, snapshot, _ = cell
    ownership = adapter.execute(adapter.parse(["slot-provision", *arguments]), api)
    slot = Path(ownership["slotRoot"])
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "runtime-1", "completed_at": "2026-10-16T00:00:00Z",
        "pid": 1, "payload_hash": api._snapshot_content_sha256(snapshot),
    }), encoding="utf-8")
    completed = adapter.execute(adapter.parse(["slot-complete", *arguments]), api)
    marker = Path(completed["completion"])
    before = marker.read_bytes()
    assert completed["activated"] is False
    assert completed["operative"] is False
    (slot / ".install-complete.json").unlink()
    validated = adapter.execute(adapter.parse(["slot-completion-validate", *arguments]), api)
    assert validated["receipt"] == completed["receipt"]
    assert marker.read_bytes() == before
    assert not (Path(receipt["pluginRoot"]) / "current-version").exists()
    assert not (Path(receipt["pluginRoot"]) / "deploy-manifest.json").exists()


@pytest.mark.parametrize("action", ["install", "update", "provision"])
def test_cell_build_is_default_off_and_does_not_migrate(cell, action):
    adapter, api, receipt, arguments, _, home = cell
    legacy = home / ".agent-pull-requests"
    legacy.mkdir()
    (legacy / "state").write_text("do not adopt", encoding="utf-8")
    with pytest.raises(ValueError, match="governed namespaced activation"):
        adapter.execute(adapter.parse([action, *arguments]), api)
    assert (legacy / "state").read_text("utf-8") == "do not adopt"
    assert not Path(receipt["versionsRoot"]).exists()


def activate_fixture(cell, monkeypatch):
    adapter, api, receipt, _, _, home = cell
    policy = home / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    }), encoding="utf-8")
    api.compare_and_swap_activation(
        context=receipt["installReceipt"],
        expected_marketplace_id=receipt["marketplaceId"],
        expected_plugin_id="agent-pull-requests",
        expected_namespace_generation=receipt["namespaceGeneration"],
        expected_install_generation=receipt["generation"],
        expected_activation_generation=0, activation_mode="namespaced",
        activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
        durable_home=home.parent / "durable",
        os_profile=home,
    )
    resolve_mode = api.resolve_installation_mode

    def fixture_mode(**arguments):
        return resolve_mode(**{**arguments, "os_profile": home})

    monkeypatch.setattr(api, "resolve_installation_mode", fixture_mode)


def test_cell_build_refuses_unmaterialized_snapshot(cell, monkeypatch):
    adapter, api, _, arguments, _, _ = cell
    activate_fixture(cell, monkeypatch)
    arguments[arguments.index("--runtime-version") + 1] = json.loads(
        (PAYLOAD / "plugin.json").read_text("utf-8"),
    )["version"]
    with pytest.raises(ValueError, match="materialized libs"):
        adapter.execute(adapter.parse(["provision", *arguments]), api)


def test_cell_build_refuses_partial_slot_without_repair(cell, monkeypatch):
    adapter, api, _, arguments, _, _ = cell
    activate_fixture(cell, monkeypatch)
    arguments[arguments.index("--runtime-version") + 1] = json.loads(
        (PAYLOAD / "plugin.json").read_text("utf-8"),
    )["version"]
    ownership = adapter.execute(adapter.parse(["slot-provision", *arguments]), api)
    slot = Path(ownership["slotRoot"])
    (slot / "partial").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete non-empty"):
        adapter.execute(adapter.parse(["provision", *arguments]), api)
    assert (slot / "partial").read_text("utf-8") == "preserve"


def test_cell_builder_uses_engine_not_legacy_selection():
    source = (SCRIPTS / "cell_install.py").read_text("utf-8")
    assert "New-SignedVenv" in source and "new_signed_venv" in source
    assert "Invoke-UvPipInstallResilient" in source
    assert "invoke_uv_pip_install_resilient" in source
    assert "agent_pull_requests.watch_install" in source
    assert "Write-DeployManifest" not in source
    assert "current-version" not in source
    assert "daemon start" not in source
    compile(source, str(SCRIPTS / "cell_install.py"), "exec")


def test_cell_builder_binds_policy_helper_to_disposable_package_root(tmp_path, monkeypatch):
    adapter = load_adapter()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    materialize_build_fixture(snapshot)
    slot = tmp_path / "slot"
    slot.mkdir()
    calls = []

    def capture(command, *, environment, cwd=None):
        source = Path(environment["APR_CELL_SOURCE"])
        helper = Path(environment["APR_CELL_ENGINE"])
        assert helper.parent.parent == source
        assert helper.read_bytes() == (snapshot / "scripts" / helper.name).read_bytes()
        assert helper.is_relative_to(slot / ".installer-build")
        assert not helper.is_relative_to(snapshot)
        assert json.loads(environment["APR_CELL_PACKAGES"])[-1] == str(source)
        for variable in ("TEMP", "TMP", "TMPDIR"):
            assert environment.get(variable) == os.environ.get(variable)
        if len(calls) < (2 if os.name == "nt" else 1):
            assert cwd == source
        calls.append(command)

    monkeypatch.setattr(adapter, "run", capture)
    adapter.build(slot, snapshot, "fixture-version")
    assert len(calls) == (3 if os.name == "nt" else 2)
    assert not list((slot / ".installer-build").iterdir())


@pytest.mark.parametrize("outcome", ["refusal", "incomplete", "malformed"])
def test_actual_target_activation_never_turns_failure_into_success(outcome):
    adapter = load_adapter()
    code = {
        "refusal": "print('{\"status\":\"ready\",\"activated\":true}'); raise SystemExit(7)",
        "incomplete": "print('{\"status\":\"ready\",\"activated\":false}')",
        "malformed": "print('fixture malformed target completion')",
    }[outcome]
    with pytest.raises(ValueError, match=r"failed \(7\)" if outcome == "refusal" else None):
        adapter.activate([sys.executable, "-I", "-c", code])


def materialize_build_fixture(snapshot):
    spec = importlib.util.spec_from_file_location(
        "apr_build_fixture_materializer", SCRIPTS / "cell_snapshot.py",
    )
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    prepared = snapshot.parent / "prepared-payload"
    producer.materialize(PAYLOAD, prepared, load_adapter().foundation())
    shutil.copytree(prepared, snapshot, dirs_exist_ok=True)


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.timeout(180)
def test_cell_real_package_build_and_completed_replay(cell, monkeypatch):
    """Real engine/uv package construction, never a shared-profile installation."""
    adapter, api, receipt, arguments, snapshot, home = cell
    activate_fixture(cell, monkeypatch)
    arguments[arguments.index("--runtime-version") + 1] = json.loads(
        (PAYLOAD / "plugin.json").read_text("utf-8"),
    )["version"]
    materialize_build_fixture(snapshot)
    before = api._snapshot_content_sha256(snapshot)
    completed = adapter.execute(adapter.parse(["provision", *arguments]), api)
    slot = Path(completed["slotRoot"])
    assert completed["activated"] is False
    assert completed["operative"] is False
    assert completed["payloadSha256"] == before
    assert api._snapshot_content_sha256(snapshot) == before
    assert (slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).is_file()
    assert list((slot / ".installer-build").iterdir()) == []
    marker = Path(completed["completion"]).read_bytes()
    monkeypatch.setattr(adapter, "build", lambda *_: pytest.fail("Completed slot rebuilt"))
    replay = adapter.execute(adapter.parse(["provision", *arguments]), api)
    assert replay["receipt"] == completed["receipt"]
    assert Path(replay["completion"]).read_bytes() == marker
    for name in ("current-version", "last-known-good", "deploy-manifest.json"):
        assert not (Path(receipt["pluginRoot"]) / name).exists()
    assert not (home / ".agent-pull-requests").exists()
    assert not (home / ".local" / "bin").exists()


def test_cell_failed_build_never_completes_or_retries_in_place(cell, monkeypatch):
    adapter, api, _, arguments, snapshot, _ = cell
    activate_fixture(cell, monkeypatch)
    arguments[arguments.index("--runtime-version") + 1] = json.loads(
        (PAYLOAD / "plugin.json").read_text("utf-8"),
    )["version"]
    materialize_build_fixture(snapshot)
    def fail(*args, **kwargs):
        raise ValueError("construction failed")
    monkeypatch.setattr(adapter, "run", fail)
    with pytest.raises(ValueError, match="construction failed"):
        adapter.execute(adapter.parse(["provision", *arguments]), api)
    ownership = adapter.execute(adapter.parse(["slot-validate", *arguments]), api)
    slot = Path(ownership["slotRoot"])
    assert not (slot / ".install-complete.json").exists()
    assert not (slot / ".runtime-slot-completion.json").exists()
    with pytest.raises(ValueError, match="Incomplete non-empty"):
        adapter.execute(adapter.parse(["provision", *arguments]), api)
