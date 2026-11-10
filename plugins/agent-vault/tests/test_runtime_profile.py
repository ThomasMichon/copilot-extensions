"""Explicit dependency selection does not alter Vault or completed base runtimes."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "vault_runtime_profile", PLUGIN / "scripts" / "runtime_profile.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def payload(tmp_path):
    path = tmp_path / "payload"
    (path / "src").mkdir(parents=True)
    (path / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname="agent-vault"\nversion="1.2.3"\n'
        '[project.optional-dependencies]\nssh=["cryptography>=50.0.0"]\n',
        encoding="utf-8",
    )
    return path


def test_profile_selection_preserves_desire_across_updates(profile, payload, tmp_path):
    root = tmp_path / "runtime"
    assert profile.selected_profile(root) == "base" and not root.exists()
    profile.persist_profile(root, "ssh")
    first = profile.selection(root, payload)
    assert first["runtime_version"] == "1.2.3+ssh"
    desire = root / profile.PROFILE_FILE
    before = (desire.read_bytes(), desire.stat().st_mtime_ns)
    profile.persist_profile(root, "ssh")
    assert (desire.read_bytes(), desire.stat().st_mtime_ns) == before
    project = payload / "pyproject.toml"
    project.write_text(project.read_text().replace('version="1.2.3"', 'version="1.2.4"'))
    updated = profile.selection(root, payload)
    assert updated["profile"] == "ssh" and updated["runtime_version"] == "1.2.4+ssh"
    assert (desire.read_bytes(), desire.stat().st_mtime_ns) == before
    assert profile.selection(root, payload, "base")["runtime_version"] == "1.2.4+base"
    assert profile.selected_profile(root) == "ssh"


def test_profile_identity_binds_extras_without_rewriting_source_receipt(
    profile, payload, tmp_path, monkeypatch,
):
    import versioned_runtime_source as source

    monkeypatch.delenv(source.SOURCE_PIN_ENV, raising=False)
    identity = source.SourceIdentity(
        "agent-vault", "1.2.3", "a" * 40, "b" * 40, source.assembly_hash(payload),
        source.runtime_version("1.2.3", "a" * 40, source.assembly_hash(payload)),
    )
    receipt = payload / source.SOURCE_FILE
    receipt.write_text(json.dumps(identity.document()), encoding="utf-8")
    original = receipt.read_bytes()
    selected = profile.selection(tmp_path / "runtime", payload, "ssh")
    assert selected["source_identity"] == source.manifest_fields(identity)
    assert selected["source_version"] == identity.runtime_version
    assert selected["package_version"] == "1.2.3"
    assert selected["runtime_version"] == source.runtime_version(
        "1.2.3+ssh", identity.source_commit, identity.assembly_hash,
    )
    assert profile.fingerprint(payload, "ssh") != profile.fingerprint(payload, "base")
    assert receipt.read_bytes() == original


@pytest.mark.parametrize("content", [
    "{", "[]", '{"schema":"agent-vault.runtime-profile","version":true,"profile":"ssh"}',
    '{"schema":"agent-vault.runtime-profile","version":1,"profile":"unknown"}',
    '{"schema":"agent-vault.runtime-profile","version":1,"profile":"ssh","extra":0}',
    '{"schema":"agent-vault.runtime-profile","version":1,"profile":"base","profile":"ssh"}',
])
def test_malformed_desire_is_not_silently_replaced(profile, tmp_path, content):
    path = tmp_path / profile.PROFILE_FILE
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        profile.persist_profile(tmp_path, "ssh")
    assert path.read_text(encoding="utf-8") == content


def test_profile_file_cannot_redirect_writes(profile, tmp_path):
    target = tmp_path / "other.json"
    target.write_text("retained", encoding="utf-8")
    link = tmp_path / profile.PROFILE_FILE
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("creating symbolic links is unavailable")
    with pytest.raises(ValueError, match="ordinary file"):
        profile.persist_profile(tmp_path, "ssh")
    assert target.read_text() == "retained"


def test_bootstrap_currentness_requires_matching_profile_and_actual_slot(profile, payload, tmp_path):
    profile.persist_profile(tmp_path, "ssh")
    slot = tmp_path / "versions" / "1.2.3+ssh"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter metadata fixture")
    (tmp_path / "current-version").write_text(slot.name)
    manifest = {"source": {"version": "1.2.3"}, "runtime_profile": "ssh", "runtime_version": slot.name}
    (tmp_path / "deploy-manifest.json").write_text(json.dumps(manifest))
    assert profile.current(tmp_path, "1.2.3", payload)
    assert not profile.current(tmp_path, "1.2.4", payload)
    profile.persist_profile(tmp_path, "base")
    assert not profile.current(tmp_path, "1.2.3", payload)
    profile.persist_profile(tmp_path, "ssh")
    (tmp_path / "current-version").write_text("1.2.3")
    assert not profile.current(tmp_path, "1.2.3", payload)
    (tmp_path / "current-version").write_text(slot.name)
    python.unlink()
    assert not profile.current(tmp_path, "1.2.3", payload)


@pytest.mark.parametrize("desired", [None, "base", "ssh"])
def test_bootstrap_currentness_binds_incoming_source_and_full_receipt(
    profile, payload, tmp_path, monkeypatch, desired,
):
    import runtime_source_target as target
    import versioned_runtime_source as source

    monkeypatch.delenv(source.SOURCE_PIN_ENV, raising=False)
    root = tmp_path / "runtime"
    root.mkdir()
    if desired:
        profile.persist_profile(root, desired)

    def prepare(commit):
        digest = source.assembly_hash(payload)
        identity = source.SourceIdentity(
            "agent-vault", "1.2.3", commit * 40, "b" * 40, digest,
            source.runtime_version("1.2.3", commit * 40, digest),
        )
        (payload / source.SOURCE_FILE).write_text(json.dumps(identity.document()))
        return identity

    incumbent = prepare("a")
    selected = target.selected(root, payload)
    slot = root / "versions" / selected["runtime_version"]
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter metadata fixture")
    marker = root / "current-version"
    marker.write_text(slot.name)
    manifest = {
        "source": {"version": "1.2.3"}, "runtime_profile": desired or "base",
        "runtime_version": slot.name, "runtime_source": incumbent.document(),
    }
    deployment = root / "deploy-manifest.json"
    deployment.write_text(json.dumps(manifest))
    assert profile.current(root, "1.2.3", payload)
    monkeypatch.setenv(source.SOURCE_PIN_ENV, "{}")
    with pytest.raises(ValueError, match="transaction selection"):
        profile.current(root, "1.2.3", payload)
    monkeypatch.delenv(source.SOURCE_PIN_ENV)
    manifest["runtime_source"]["source_tree"] = "c" * 40
    deployment.write_text(json.dumps(manifest))
    assert not profile.current(root, "1.2.3", payload)
    manifest["runtime_source"] = incumbent.document()
    deployment.write_text(json.dumps(manifest))
    incoming = prepare("d")
    assert not profile.current(root, "1.2.3", payload)
    manifest["runtime_source"] = incoming.document()
    deployment.write_text(json.dumps(manifest))
    assert not profile.current(root, "1.2.3", payload)


def test_legacy_currentness_preserves_unselected_base(profile, payload, tmp_path):
    root = tmp_path / "runtime"
    python = root / "versions" / "1.2.3" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter metadata fixture")
    (root / "current-version").write_text("1.2.3")
    (root / "deploy-manifest.json").write_text(json.dumps({"source": {"version": "1.2.3"}}))
    assert profile.current(root, "1.2.3", payload)
    assert not (root / profile.PROFILE_FILE).exists()


def test_native_driver_retains_profile_through_request_handoff(profile, monkeypatch, tmp_path):
    import installer_driver as adapter
    from update_driver import core

    monkeypatch.setenv("AGENT_VAULT_INSTALL_PROFILE", "ssh")
    source_target = json.dumps({
        "schema": "copilot-extensions.runtime-source-target", "version": 1,
        "plugin": "agent-vault", "runtime_root": str(tmp_path / "runtime"),
        "source": None, "profile": "ssh", "runtime_version": "1.0.0+ssh",
    })
    monkeypatch.setenv("COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET", source_target)
    for name in ("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(adapter, "source_digest", lambda _payload: "a" * 64)
    args = SimpleNamespace(
        payload=PLUGIN, root=tmp_path / "runtime", plugin="agent-vault",
        shell=tmp_path / "powershell.exe", action="provision", options=["NoService"],
    )
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **options: queued.append((spec, options["runner"]))
        or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    assert adapter.foreground(args, driver) == 0
    spec, runner = queued[0]
    assert dict(spec.context)["AGENT_VAULT_INSTALL_PROFILE"] == "ssh"
    assert "AGENT_VAULT_INSTALL_PROFILE" in runner.context_keys
    assert dict(spec.context)["COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET"] == source_target
    assert "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET" in runner.context_keys


def test_source_target_preserves_legacy_base_and_refuses_profile_drift_before_write(
    profile, payload, tmp_path, monkeypatch,
):
    import runtime_source_target as target
    import versioned_runtime_source as source

    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "agent-vault", "1.2.3", "a" * 40, "b" * 40, digest,
        source.runtime_version("1.2.3", "a" * 40, digest),
    )
    (payload / source.SOURCE_FILE).write_text(json.dumps(identity.document()))
    root = tmp_path / "runtime"
    original = target.selected(root, payload)
    assert original["runtime_version"] == identity.runtime_version
    assert original["source_identity"] == source.manifest_fields(identity)
    profile.persist_profile(root, "base")
    planned = target.document(root, target.selected(root, payload))
    assert planned["runtime_version"] == source.runtime_version("1.2.3+base", "a" * 40, digest)
    monkeypatch.setenv(target.TARGET_ENV, json.dumps(planned))
    monkeypatch.setattr(profile, "SCRIPTS", payload / "scripts")
    before = (root / profile.PROFILE_FILE).read_bytes()
    with pytest.raises(ValueError, match="changed after target planning"):
        profile.main(["--root", str(root), "select", "--profile", "ssh", "--persist"])
    assert (root / profile.PROFILE_FILE).read_bytes() == before


def test_source_profile_receipt_is_written_only_before_completion(
    profile, payload, tmp_path, monkeypatch,
):
    import runtime_source_target as target
    import versioned_runtime as runtime

    root = tmp_path / "runtime"
    profile.persist_profile(root, "ssh")
    selected = target.selected(root, payload)
    slot = root / "versions" / selected["runtime_version"]
    slot.mkdir(parents=True)
    complete = False
    monkeypatch.setattr(runtime, "is_complete", lambda *_a: complete)
    target.record_profile(slot, selected, "a" * 64)
    receipt = slot / "runtime-profile.json"
    before = (receipt.read_bytes(), receipt.stat().st_mtime_ns)
    complete = True
    target.record_profile(slot, selected, "a" * 64)
    assert (receipt.read_bytes(), receipt.stat().st_mtime_ns) == before
    with pytest.raises(ValueError, match="evidence changed"):
        target.record_profile(slot, selected, "b" * 64)
    assert (receipt.read_bytes(), receipt.stat().st_mtime_ns) == before
    receipt.unlink()
    with pytest.raises(ValueError, match="refusing mutation"):
        target.record_profile(slot, selected, "a" * 64)
    assert not receipt.exists()
