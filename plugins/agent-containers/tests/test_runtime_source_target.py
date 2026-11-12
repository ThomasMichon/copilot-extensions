from __future__ import annotations

import importlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def profiles(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    return importlib.import_module("runtime_profile")


@pytest.fixture
def prepared(tmp_path, profiles):
    payload = tmp_path / "prepared"
    payload.mkdir()
    (payload / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-containers", "version": "1.0.0"}), encoding="utf-8",
    )
    digest = profiles.source.assembly_hash(payload)
    identity = profiles.source.SourceIdentity(
        "agent-containers", "1.0.0", "a" * 40, "b" * 40, digest,
        profiles.source.runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    return payload, identity


def _plan(profiles, root, prepared):
    payload, identity = prepared
    return profiles.plan_source(
        root, payload, expected_source_version=identity.runtime_version,
        expected_source_identity=json.dumps(profiles.source.manifest_fields(identity), sort_keys=True),
    )


def _snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


def _intent(profiles, root, profile):
    root.mkdir(parents=True, exist_ok=True)
    (root / profiles.PROFILE_FILE).write_text(
        json.dumps({"schema": profiles.PROFILE_SCHEMA, "version": 1, "profile": profile}),
        encoding="utf-8",
    )


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_source_target_plan_uses_owned_profile_without_writes(
    profiles, prepared, tmp_path, profile,
):
    root = tmp_path / "runtime"
    _intent(profiles, root, profile)
    (root / "config.json").write_text('{"keep":true}\n', encoding="utf-8")
    before = _snapshot(tmp_path)
    plan = _plan(profiles, root, prepared)
    _, identity = prepared
    assert plan == {
        "schema": profiles.TARGET_SCHEMA, "version": 1, "plugin": "agent-containers",
        "runtime_root": str(root), "source": profiles.source.manifest_fields(identity),
        "profile": profile,
        "runtime_version": profiles.source.runtime_version(
            f"1.0.0+{profile}", identity.source_commit, identity.assembly_hash,
        ),
    }
    assert plan["runtime_version"] != identity.runtime_version
    assert _snapshot(tmp_path) == before


def test_fresh_target_plan_does_not_create_the_runtime_root(profiles, prepared, tmp_path):
    root = tmp_path / "missing"
    assert _plan(profiles, root, prepared)["profile"] == "base"
    assert not root.exists()

def test_source_planning_cli_does_not_forward_native_wheel_keywords(
    profiles, prepared, tmp_path, monkeypatch, capsys,
):
    root = tmp_path / "runtime"
    payload, identity = prepared
    expected = _plan(profiles, root, prepared)
    monkeypatch.setattr(sys, "argv", [
        "runtime_profile.py", "--root", str(root), "--payload", str(payload), "--plan-source",
        "--expected-source-version", identity.runtime_version,
        "--expected-source-identity", json.dumps(profiles.source.manifest_fields(identity), sort_keys=True),
    ])
    assert profiles.main() == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert not root.exists()


def test_native_wheel_cli_passes_inputs_to_the_existing_provision_owner(
    profiles, tmp_path, monkeypatch, capsys,
):
    calls = []

    def provision(root, payload, **options):
        calls.append(options)
        return {"profile": "base"}

    monkeypatch.setattr(profiles, "provision", provision)
    monkeypatch.setattr(sys, "argv", [
        "runtime_profile.py", "--root", str(tmp_path / "runtime"),
        "--payload", str(tmp_path / "payload"), "--python", sys.executable,
        "--uv", str(tmp_path / "uv"), "--wheel-profile", str(tmp_path / "profile.json"),
        "--wheel-directory", str(tmp_path / "wheels"),
    ])
    assert profiles.main() == 0
    assert json.loads(capsys.readouterr().out) == {"profile": "base"}
    assert calls[0]["wheel_profile"] == tmp_path / "profile.json"
    assert calls[0]["wheel_directory"] == tmp_path / "wheels"


@pytest.mark.parametrize("reused", [False, True])
@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_native_wheel_profile_health_gates_completion(
    profiles, tmp_path, monkeypatch, reused, profile,
):
    import install_source

    root, payload = tmp_path / "runtime", tmp_path / "payload"
    slot = root / "versions" / "1.0.profile.fixture"
    slot.mkdir(parents=True)
    marker = slot / ".install-complete.json"
    expected = {
        "packageVersion": "1.0", "profile": profile, "payloadHash": "fixture",
        "executionContract": profiles._execution_contract(),
    }
    if reused:
        profiles._write_json(slot / profiles.PROFILE_FILE, expected)
        marker.write_bytes(b"prior completion")
    monkeypatch.setattr(profiles, "_select_target", lambda *_a: SimpleNamespace(
        package_version="1.0", identity=None, profile=profile, runtime_version="1.0",
    ))
    monkeypatch.setattr(profiles.runtime, "is_complete", lambda *_a: marker.exists())

    @contextmanager
    def inputs(*_a):
        yield SimpleNamespace(verify=lambda: None)

    @contextmanager
    def candidate(*_a, validate_candidate, **_kw):
        validate_candidate(slot)
        marker.write_bytes(b"new completion")
        yield SimpleNamespace(slot=slot, reused=reused)

    monkeypatch.setattr(install_source, "wheel_inputs", inputs)
    monkeypatch.setattr(install_source, "wheel_selection", lambda *_a: {
        "extras": ["provider-exec"] if profile == "provider-exec" else [],
        "runtime_version": slot.name, "payload_hash": "fixture", "local_packages": [],
    })
    monkeypatch.setitem(sys.modules, "installer_engine", SimpleNamespace(numbered_candidate=candidate))

    def reject(*_a, **_kw):
        raise RuntimeError("profile health failed")

    monkeypatch.setattr(profiles, "_health", reject)
    with pytest.raises(RuntimeError, match="profile health failed"):
        profiles.provision(
            root, payload, python=sys.executable, uv="fixture",
            wheel_profile=tmp_path / "profile.json", wheel_directory=tmp_path / "wheels",
        )
    assert marker.read_bytes() == b"prior completion" if reused else not marker.exists()
    assert not (root / profiles.PROFILE_FILE).exists()


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_native_wheel_provision_selects_and_retains_its_exact_profile(
    profiles, tmp_path, monkeypatch, profile,
):
    import install_source

    root, payload = tmp_path / "r", tmp_path / "p"
    extras = ["provider-exec"] if profile == "provider-exec" else []
    binding = {"wheels": [{"name": "agent-containers", "selected_extras": extras}]}
    lock = "sha256:" + hashlib.sha256(json.dumps(
        binding, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    generation = "1.0.profile." + lock[7:23]
    fingerprint = "a" * 64
    receipt = {
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": {"agent-containers": "file:///inspected.whl"}, "extras": extras,
        "wheel_inputs": {
            "construction": "closed-wheels", "lock_id": lock, "binding": binding,
            "distributions": {"agent-containers": {"version": "1.0"}},
        },
    }
    monkeypatch.setattr(profiles, "_select_target", lambda *_a: SimpleNamespace(
        package_version="1.0", identity=None, profile=profile, runtime_version="1.0",
    ))

    @contextmanager
    def inputs(*_a):
        yield SimpleNamespace(verify=lambda: None)

    @contextmanager
    def candidate(*_a, validate_candidate, **_kw):
        with profiles.runtime.construction_lease(root, generation):
            slot = profiles.runtime.slot(root, generation)
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"fixture interpreter")
            profiles._write_json(slot / ".runtime-input-origins.json", receipt)
            validate_candidate(slot)
            profiles.runtime.mark_complete(root, generation, payload_hash=fingerprint)
        yield SimpleNamespace(slot=slot, reused=False)

    monkeypatch.setattr(install_source, "wheel_inputs", inputs)
    monkeypatch.setattr(install_source, "wheel_selection", lambda *_a: {
        "extras": extras, "runtime_version": generation,
        "payload_hash": fingerprint, "local_packages": ["agent-containers"],
    })
    monkeypatch.setitem(sys.modules, "installer_engine", SimpleNamespace(numbered_candidate=candidate))
    monkeypatch.setattr(profiles, "_health", lambda *_a, **_kw: None)
    result = profiles.provision(
        root, payload, python=sys.executable, uv="fixture",
        wheel_profile=tmp_path / "profile.json", wheel_directory=tmp_path / "wheels",
    )
    selected = profiles.select_execution(root)
    assert selected.runtime_version == result["runtimeVersion"] == generation
    assert selected.profile == profile
    with profiles.retain_execution(root, selected, timeout=0) as (retained, descriptor):
        assert retained == selected
        os.fstat(descriptor)
    receipt["extras"] = [] if extras else ["provider-exec"]
    profiles._write_json(selected.python.parent.parent / ".runtime-input-origins.json", receipt)
    with pytest.raises(RuntimeError, match="wheel receipt conflicts"):
        profiles.select_execution(root)
    with pytest.raises(RuntimeError, match="wheel receipt conflicts"):
        with profiles.retain_execution(root, selected, timeout=0):
            pytest.fail("changed receipt cannot retain execution")


def test_old_transaction_caller_cannot_ignore_required_profile_target(
    profiles, prepared, tmp_path, monkeypatch,
):
    root = tmp_path / "runtime"
    monkeypatch.setenv(
        profiles.source.SOURCE_PIN_ENV,
        json.dumps(profiles.source.manifest_fields(prepared[1]), sort_keys=True),
    )
    monkeypatch.delenv(profiles.TARGET_ENV, raising=False)
    with pytest.raises(RuntimeError, match="requires a frozen source target"):
        profiles.provision(root, prepared[0], python=sys.executable, uv="unused")
    assert not root.exists()


def test_native_old_transaction_refuses_before_installation_state(tmp_path):
    shell = shutil.which("pwsh" if os.name == "nt" else "bash")
    if shell is None:
        pytest.skip("native shell unavailable")
    root = tmp_path / "missing-runtime"
    command = (
        [shell, "-NoProfile", "-File", str(SCRIPTS / "init.ps1"), "provision",
         "-InstallDir", str(root)]
        if os.name == "nt" else
        [shell, str(SCRIPTS / "init.sh"), "provision", "--install-dir", str(root)]
    )
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET", None)
    environment["COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"] = "{}"
    result = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    assert "requires a frozen source target" in result.stderr
    assert not root.exists()


@pytest.mark.parametrize("paramiko", [False, True])
def test_source_target_preserves_legacy_metadata_without_importing_app(
    profiles, prepared, tmp_path, paramiko,
):
    root = tmp_path / "runtime"
    slot = profiles.runtime.slot(root, "0.9.0")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"damaged legacy interpreter must not execute")
    if paramiko:
        metadata = slot / "Lib" / "site-packages" / "paramiko-4.0.0.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Name: paramiko\nVersion: 4.0.0\n", encoding="utf-8")
    profiles.runtime.mark_complete(root, "0.9.0")
    profiles.runtime.activate(root, "0.9.0", link_free=True)
    before = _snapshot(tmp_path)
    assert _plan(profiles, root, prepared)["profile"] == ("provider-exec" if paramiko else "base")
    assert _snapshot(tmp_path) == before
    assert not (root / profiles.PROFILE_FILE).exists()


@pytest.mark.parametrize("mutation", ["version", "identity", "payload", "profile"])
def test_invalid_plan_refuses_without_runtime_changes(
    profiles, prepared, tmp_path, mutation,
):
    payload, identity = prepared
    root = tmp_path / "runtime"
    expected = profiles.source.manifest_fields(identity)
    version = identity.runtime_version
    if mutation == "version":
        version = "other"
    elif mutation == "identity":
        expected["runtime_source"]["source_tree"] = "c" * 40
    elif mutation == "payload":
        (payload / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    else:
        _intent(profiles, root, "unknown")
    before = _snapshot(tmp_path)
    with pytest.raises((ValueError, RuntimeError)):
        profiles.plan_source(
            root, payload, expected_source_version=version,
            expected_source_identity=json.dumps(expected, sort_keys=True),
        )
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("mutation", [
    "profile", "runtime_root", "source", "runtime_version", "version", "extra",
])
def test_frozen_source_target_refuses_changes_before_construction(
    profiles, prepared, tmp_path, monkeypatch, mutation,
):
    root = tmp_path / "runtime"
    _intent(profiles, root, "provider-exec")
    plan = _plan(profiles, root, prepared)
    if mutation == "profile":
        _intent(profiles, root, "base")
    elif mutation == "version":
        plan["version"] = True
    elif mutation == "source":
        plan["source"]["runtime_source"]["source_tree"] = "c" * 40
    else:
        plan[mutation] = "changed"
    monkeypatch.setenv(profiles.TARGET_ENV, json.dumps(plan))
    before = _snapshot(tmp_path)
    with pytest.raises(RuntimeError, match="source target changed"):
        profiles.provision(root, prepared[0], python=sys.executable, uv="unused")
    assert _snapshot(tmp_path) == before
    assert not (root / "versions").exists()


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_frozen_source_target_and_provision_share_the_same_identity(
    profiles, prepared, tmp_path, monkeypatch, profile,
):
    root = tmp_path / "runtime"
    _intent(profiles, root, profile)
    plan = _plan(profiles, root, prepared)
    monkeypatch.setenv(profiles.TARGET_ENV, json.dumps(plan))
    monkeypatch.setattr(profiles, "_library_roots", lambda *args, **kwargs: [])

    def run(arguments, *, cwd):
        if "venv" in arguments:
            slot = Path(arguments[-1])
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"fixture interpreter")

    monkeypatch.setattr(profiles, "_run", run)
    first = profiles.provision(root, prepared[0], python=sys.executable, uv="fixture")
    assert first["runtimeVersion"] == plan["runtime_version"]
    assert first["runtimeSource"] == plan["source"]["runtime_source"]
    again = profiles.provision(root, prepared[0], python=sys.executable, uv="fixture")
    assert again["reused"]
    assert again["runtimeVersion"] == plan["runtime_version"]


@pytest.mark.parametrize("arguments,success", [
    ([], True), (["--profile", "base"], False), (["--python", "unused"], False),
    (["--uv", "unused"], False), (["--tsv"], False), (["--unknown"], False),
    (["--stamp-source"], False),
])
def test_real_source_target_parser_is_read_only_and_strict(
    profiles, prepared, tmp_path, arguments, success,
):
    root = tmp_path / "runtime"
    payload, identity = prepared
    before = _snapshot(tmp_path)
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", str(SCRIPTS / "runtime_profile.py"),
            "--root", str(root), "--payload", str(payload), "--plan-source",
            "--expected-source-version", identity.runtime_version,
            "--expected-source-identity",
            json.dumps(profiles.source.manifest_fields(identity), sort_keys=True),
            *arguments,
        ],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    if success:
        assert json.loads(result.stdout) == _plan(profiles, root, prepared)
        assert result.stderr == ""
    else:
        assert result.stderr
    assert _snapshot(tmp_path) == before
    assert not root.exists()
