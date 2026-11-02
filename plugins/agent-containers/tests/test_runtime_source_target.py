from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
