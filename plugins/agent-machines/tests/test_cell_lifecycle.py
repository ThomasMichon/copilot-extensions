"""Agent Machines explicit lifecycle adapters share one receipt-authorized engine."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cell_lifecycle.py"
spec = importlib.util.spec_from_file_location("machines_cell_lifecycle_test", SCRIPT)
assert spec and spec.loader
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
ic = engine.ic


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def fixture(tmp_path, *, name="alpha", versions=("1.0.0",)):
    payload = tmp_path / name
    payload.mkdir(parents=True)
    (payload / "content").write_text("payload", encoding="utf-8")
    write(payload / "plugin.json", {"name": "agent-machines", "version": versions[0]})
    stamped = ic.stamp_context(
        payload_root=payload, plugin_id="agent-machines", payload_version=versions[0], payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        durable_home=tmp_path / "d", source_descriptor={"source": "github", "repo": f"example-org/{name}"},
        marketplace_key=name, environment={},
    )
    root = Path(stamped["pluginRoot"])
    for version in versions:
        snapshot = root / "snapshots" / version
        snapshot.mkdir(parents=True)
        (snapshot / "content").write_text("snapshot", encoding="utf-8")
        common = dict(context=stamped["installReceipt"], expected_marketplace_id=stamped["marketplaceId"],
                      expected_plugin_id="agent-machines", durable_home=tmp_path / "d",
                      snapshot_id=version, environment={})
        ic.stamp_snapshot_provenance(**common, expected_namespace_generation=stamped["namespaceGeneration"],
                                     expected_install_generation=stamped["generation"])
        ic.provision_runtime_slot(**common, runtime_version=version)
        slot = root / "versions" / version
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir()
        python.write_text("synthetic interpreter", encoding="utf-8")
        (slot / "pyvenv.cfg").write_text("home = synthetic\n", encoding="utf-8")
        write(slot / ".install-complete.json", {
            "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": 0,
            "payload_hash": ic._snapshot_content_sha256(snapshot),
        })
        ic.complete_runtime_slot(**common, runtime_version=version,
                                 expected_payload_root=payload, expected_payload_version=versions[0])
    args = argparse.Namespace(
        action="cell-repair", context=stamped["installReceipt"], durable_home=str(tmp_path / "d"),
        expected_marketplace_id=stamped["marketplaceId"], expected_payload_root=str(payload),
        expected_payload_version=versions[0], snapshot_id=versions[0], runtime_version=versions[0],
        expected_namespace_generation=stamped["namespaceGeneration"], expected_install_generation=stamped["generation"],
        expected_current_version=None, expect_current_absent=True,
        expected_last_known_good_version=None, expect_last_known_good_absent=True,
    )
    return root, args


def select(args, version="1.0.0"):
    args.expected_current_version = args.expected_last_known_good_version = version
    args.expect_current_absent = args.expect_last_known_good_absent = version is None


def legacy_state(profile: Path) -> tuple[Path, Path]:
    root = profile / ".agent-machines"
    root.mkdir(parents=True, exist_ok=True)
    (root / "legacy.txt").write_text("legacy state\n", encoding="utf-8")
    command = profile / ".local" / "bin" / "agent-machines"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("legacy wrapper\n", encoding="utf-8")
    return root, command


def adapter(style, args, *, omit=(), env=None):
    command = (
        [shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile", "-File", str(SCRIPT.with_name("init.ps1")), "-Action", args.action]
        if style == "powershell" else ["bash", str(SCRIPT.with_name("init.sh")), args.action]
    )
    for key, value in vars(args).items():
        if key == "action" or key in omit or value is None or value is False:
            continue
        name = "--" + key.replace("_", "-") if style == "bash" else "-" + "".join(p.capitalize() for p in key.split("_"))
        command.append(name)
        if value is not True:
            command.append(str(value))
    return subprocess.run(command, text=True, encoding="utf-8", capture_output=True, timeout=30, env=env)


STYLES = (["bash"] if os.name != "nt" and shutil.which("bash") else []) + (
    ["powershell"] if shutil.which("pwsh") or shutil.which("powershell") else []
)


@pytest.mark.parametrize("style", STYLES)
def test_adapters_repair_uninstall_preserve_isolate_replay(tmp_path, style):
    root, args = fixture(tmp_path, versions=("1.0.0", "2.0.0"))
    peer, _ = fixture(tmp_path, name="beta")
    immutable = {p: p.read_bytes() for p in root.rglob("*.json")}
    state = root / "state" / "inventory.json"
    write(state, {"keep": True})
    peer_before = {p: p.read_bytes() for p in peer.rglob("*") if p.is_file()}
    first = adapter(style, args)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["reason"] == "cell-repaired"
    select(args)
    second = adapter(style, args)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["reason"] == "cell-repair-healthy"
    assert all(p.read_bytes() == content for p, content in immutable.items())
    for name in ("run", "logs", "cache", "launchers"):
        write(root / name / "derived.json", {"generated": True})
    args.action = "cell-uninstall"
    result = adapter(style, args)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["reason"] == "cell-uninstalled", report
    assert report["removed"][:2] == ["current-version", "last-known-good"]
    assert not list((root / "versions").iterdir())
    assert not list((root / "snapshots").iterdir())
    assert state.read_text().strip() == '{"keep": true}'
    assert all(p.read_bytes() == content for p, content in peer_before.items())
    assert (root / "install.json").read_bytes() == immutable[root / "install.json"]
    select(args, None)
    replay = adapter(style, args)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["status"] == "preserved"


@pytest.mark.parametrize("style", STYLES)
def test_ambient_context_cannot_authorize_lifecycle(tmp_path, monkeypatch, style):
    root, args = fixture(tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", args.context)
    for action in ("cell-repair", "cell-uninstall"):
        args.action = action
        result = adapter(style, args, omit=("context",))
        assert result.returncode != 0
    assert not (root / "deploy-manifest.json").exists()


@pytest.mark.parametrize("style", STYLES)
def test_lifecycle_requires_matching_plugin_maintenance_token(tmp_path, style):
    root, args = fixture(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    entered = engine.ic.enter_maintenance(
        scope="plugin",
        owner="test-owner",
        reason="upgrade",
        expected_duration_seconds=300,
        durable_home=args.durable_home,
        context=args.context,
        expected_marketplace_id=args.expected_marketplace_id,
        expected_plugin_id="agent-machines",
        environment={},
        os_profile=profile,
        platform="windows" if os.name == "nt" else "posix",
        wsl_distro=None,
    )

    result = adapter(style, args)
    assert result.returncode != 0
    assert "--maintenance-token" in result.stderr

    args.maintenance_token = "wrong-token"
    wrong = adapter(style, args)
    assert wrong.returncode != 0
    assert "does not match" in wrong.stderr

    args.maintenance_token = entered["token"]
    allowed = adapter(style, args)
    assert allowed.returncode == 0, allowed.stderr
    assert json.loads(allowed.stdout)["reason"] == "cell-repaired"
    assert root.joinpath("maintenance").exists()


@pytest.mark.parametrize("style", STYLES)
def test_legacy_attribution_adapters_publish_tombstone_once(tmp_path, monkeypatch, style):
    if os.name != "nt":
        pytest.skip("subprocess adapter attribution uses the real POSIX profile; direct governance tests cover POSIX")
    root, args = fixture(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    legacy, command = legacy_state(profile)
    env = os.environ.copy()
    env.update({"HOME": str(profile), "USERPROFILE": str(profile)})
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    args.action = "cell-attribute-legacy"
    args.maintenance_token = None

    first = adapter(style, args, env=env)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["reason"] == "legacy-attributed"
    assert first_payload["tombstoneChanged"] is True
    assert first_payload["activationChanged"] is True
    assert {item["identity"] for item in first_payload["attributedItems"]} == {
        ".agent-machines",
        ".local/bin/agent-machines",
    }
    tombstone = legacy / ".installation-ownership.json"
    activation = root / "installation-activation.json"
    assert tombstone.exists()
    assert activation.exists()
    preserved_tombstone = tombstone.read_bytes()
    preserved_activation = activation.read_bytes()

    second = adapter(style, args, env=env)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["reason"] == "already-attributed"
    assert second_payload["tombstoneChanged"] is False
    assert second_payload["activationChanged"] is False
    assert tombstone.read_bytes() == preserved_tombstone
    assert activation.read_bytes() == preserved_activation
    assert command.read_text(encoding="utf-8") == "legacy wrapper\n"


def test_legacy_attribution_refuses_without_legacy_lock_or_install_lock(tmp_path, monkeypatch):
    _root, args = fixture(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    legacy_state(profile)
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    args.action = "cell-attribute-legacy"

    @contextmanager
    def failing_lock(_path: Path):
        raise ic.InstallationContextError("legacy lock remained busy")
        yield

    monkeypatch.setattr(engine, "provisioning_lock", failing_lock)
    with pytest.raises(ic.InstallationContextError, match="legacy lock remained busy"):
        engine.lifecycle(args)

    monkeypatch.undo()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    legacy_state(profile)
    original_acquire = ic._DirectoryLock.acquire

    def fail_install(self):
        if self.kind == "install":
            raise ic.InstallationContextError("Installation lock remained busy.")
        return original_acquire(self)

    monkeypatch.setattr(ic._DirectoryLock, "acquire", fail_install)
    with pytest.raises(ic.InstallationContextError, match="remained busy"):
        engine.lifecycle(args)


@pytest.mark.parametrize("case", ["missing-completion", "bad-completion", "missing-snapshot", "changed-payload", "foreign-marketplace", "linked-manifest"])
def test_repair_refuses_missing_or_foreign_immutable_evidence(tmp_path, case):
    root, args = fixture(tmp_path)
    completion = root / "versions" / "1.0.0" / ".runtime-slot-completion.json"
    before = completion.read_bytes()
    if case == "missing-completion":
        completion.unlink()
    elif case == "bad-completion":
        completion.write_text("{}", encoding="utf-8")
    elif case == "missing-snapshot":
        (root / "snapshots" / "1.0.0" / "content").unlink()
    elif case == "changed-payload":
        args.expected_payload_version = "foreign"
    elif case == "foreign-marketplace":
        args.expected_marketplace_id = "foreign--0123456789abcdef"
    else:
        try:
            (root / "deploy-manifest.json").symlink_to(completion)
        except OSError:
            pytest.skip("symlink creation unavailable")
    with pytest.raises((ic.InstallationContextError, OSError)):
        engine.lifecycle(args)
    assert not (root / "current-version").exists()
    if case not in {"missing-completion", "bad-completion"}:
        assert completion.read_bytes() == before


@pytest.mark.parametrize("marker", ["current", "lkg", "generation"])
def test_generation_and_selection_cas_refuse_without_mutation(tmp_path, marker):
    root, args = fixture(tmp_path)
    if marker == "generation":
        args.expected_install_generation += 1
    else:
        (root / ("current-version" if marker == "current" else "last-known-good")).write_text("2.0.0\n", encoding="utf-8")
    for action in ("cell-repair", "cell-uninstall"):
        args.action = action
        result = engine.lifecycle(args)
        assert result["status"] == "revalidation-required"
        assert not result["changed"]
    assert (root / "versions" / "1.0.0").is_dir()


@pytest.mark.parametrize("case", ["unowned-slot", "foreign-snapshot", "unknown-artifact", "linked-cache", "live", "foreign-manifest", "invalid-manifest", "foreign-selection", "malformed-source"])
def test_uninstall_preflight_preserves_selection_on_ambiguity(tmp_path, monkeypatch, case):
    root, args = fixture(tmp_path)
    engine.lifecycle(args)
    select(args)
    if case == "unowned-slot":
        (root / "versions" / "2.0.0").mkdir()
    elif case == "foreign-snapshot":
        write(root / "snapshots" / "1.0.0" / "snapshot-provenance.json", {})
    elif case == "unknown-artifact":
        (root / "unknown").write_text("preserve", encoding="utf-8")
    elif case == "linked-cache":
        try:
            (root / "cache").symlink_to(tmp_path, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation unavailable")
    elif case == "live":
        monkeypatch.setattr(engine.vr, "_versions_with_live_process", lambda root: {"1.0.0"})
    elif case == "invalid-manifest":
        (root / "deploy-manifest.json").write_text("{", encoding="utf-8")
    else:
        manifest = json.loads((root / "deploy-manifest.json").read_bytes())
        if case == "foreign-selection":
            manifest["runtime"]["selectedBy"]["version"] = "foreign"
        elif case == "malformed-source":
            manifest["source"]["kind"] = []
        else:
            manifest["installation"]["pluginId"] = "foreign"
        write(root / "deploy-manifest.json", manifest)
    args.action = "cell-uninstall"
    with pytest.raises((ic.InstallationContextError, OSError)):
        engine.lifecycle(args)
    assert (root / "current-version").read_text().strip() == "1.0.0"
    assert (root / "versions" / "1.0.0").is_dir()


def test_uninstall_revalidates_before_each_delete(tmp_path, monkeypatch):
    root, args = fixture(tmp_path)
    engine.lifecycle(args)
    select(args)
    args.action = "cell-uninstall"
    original = Path.unlink

    def drift(path, *a, **kw):
        result = original(path, *a, **kw)
        if path == root / "current-version":
            document = json.loads((root / "install.json").read_bytes())
            document["generation"] += 1
            write(root / "install.json", document)
        return result

    monkeypatch.setattr(Path, "unlink", drift)
    result = engine.lifecycle(args)
    assert result["status"] == "revalidation-required"
    assert (root / "last-known-good").exists()
    assert (root / "versions" / "1.0.0").is_dir()


def test_repair_recreates_invalid_derived_manifest_only(tmp_path):
    root, args = fixture(tmp_path)
    (root / "deploy-manifest.json").write_text("{", encoding="utf-8")
    state = root / "state" / "config"
    state.parent.mkdir()
    state.write_text("preserve", encoding="utf-8")
    assert engine.lifecycle(args)["reason"] == "cell-repaired"
    assert state.read_text() == "preserve"
    assert not (root / "launchers").exists()


def test_uninstall_preserves_external_hardlinks_and_removes_all_owned_slots(tmp_path):
    root, args = fixture(tmp_path, versions=("1.0.0", "2.0.0"))
    cache = tmp_path / "external-cache"
    cache.write_bytes(b"shared package bytes")
    for version in ("1.0.0", "2.0.0"):
        os.link(cache, root / "versions" / version / "shared-package")
    engine.lifecycle(args)
    select(args)
    args.action = "cell-uninstall"
    result = engine.lifecycle(args)
    assert result["reason"] == "cell-uninstalled"
    assert not list((root / "versions").iterdir())
    assert cache.read_bytes() == b"shared package bytes"


@pytest.mark.skipif(not (shutil.which("pwsh") or shutil.which("powershell")), reason="PowerShell unavailable")
def test_uninstall_accepts_actual_powershell_installer_manifest(tmp_path):
    root, args = fixture(tmp_path)
    engine.lifecycle(args)
    select(args)
    source = SCRIPT.with_name("init.ps1").read_text(encoding="utf-8")
    body = source.split("function Write-CellDeployManifest {", 1)[1].split("\nfunction Get-CellSnapshotOwnerText", 1)[0]
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    harness = tmp_path / "manifest.ps1"
    harness.write_text(
        "function Write-CellDeployManifest {" + body + "\n"
        "Write-CellDeployManifest "
        f"-PluginRoot {quote(root)} -SourcePluginDir {quote(args.expected_payload_root)} "
        f"-SourceVersion '1.0.0' -RuntimeSlot {quote(root / 'versions' / '1.0.0')} "
        f"-RuntimeVersion '1.0.0' -ContextPath {quote(args.context)} "
        f"-MarketplaceId {quote(args.expected_marketplace_id)}\n",
        encoding="utf-8",
    )
    produced = subprocess.run([shutil.which("pwsh") or shutil.which("powershell"), "-NoProfile", "-File", str(harness)],
                              capture_output=True, text=True, timeout=30)
    assert produced.returncode == 0, produced.stderr
    args.action = "cell-uninstall"
    assert engine.lifecycle(args)["reason"] == "cell-uninstalled"
