"""Normal cell snapshot production shares the release materializer."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_cell_installer_slice import activate_fixture, cell as cell  # noqa: F401 -- fixture

pytestmark = pytest.mark.timeout(90)


def _arguments(receipt):
    return [
        "snapshot-prepare", "--context", receipt["installReceipt"],
        "--expected-marketplace-id", receipt["marketplaceId"],
        "--durable-home", str(Path(receipt["pluginRoot"]).parents[3]),
    ]


def test_prepare_materializes_canonical_dev_input_without_runtime_or_owner(cell):
    adapter, api, receipt, _, _, _ = cell
    prepared = adapter.execute(adapter.parse(_arguments(receipt)), api)
    snapshot = Path(prepared["snapshotRoot"])
    assert snapshot.parent == Path(receipt["snapshotsRoot"])
    for library in adapter.LIBRARIES:
        assert (snapshot / "libs" / library / "pyproject.toml").is_file()
    project = (snapshot / "pyproject.toml").read_text()
    assert "../../libs/" not in project and "editable = true" not in project
    for suffix in ("ps1", "sh"):
        assert (snapshot / "scripts" / f"installer-engine.{suffix}").is_file()
    assert not Path(receipt["versionsRoot"]).exists()
    assert not Path(receipt["stateRoot"]).exists()
    assert not Path(receipt["runRoot"]).exists()
    assert not (Path(receipt["pluginRoot"]) / "current-version").exists()
    before = api._snapshot_content_sha256(snapshot)
    replay = adapter.execute(adapter.parse(_arguments(receipt)), api)
    assert all(replay[key] == prepared[key] for key in (
        "snapshotId", "snapshotRoot", "marketplaceId", "pluginId",
    ))
    assert api._snapshot_content_sha256(snapshot) == before
    assert not list(snapshot.parent.glob(".prepare-*"))


@pytest.mark.parametrize("damage", ["unattributed", "content"])
def test_prepare_never_repairs_or_overwrites_existing_snapshot(cell, damage):
    adapter, api, receipt, _, _, _ = cell
    prepared = adapter.execute(adapter.parse(_arguments(receipt)), api)
    snapshot = Path(prepared["snapshotRoot"])
    if damage == "unattributed":
        (snapshot / api.SNAPSHOT_PROVENANCE_FILE).unlink()
    else:
        (snapshot / "README.md").write_text("fixture changed immutable input\n")
    before = api._snapshot_content_sha256(snapshot)
    with pytest.raises((PermissionError, ValueError)):
        adapter.execute(adapter.parse(_arguments(receipt)), api)
    assert api._snapshot_content_sha256(snapshot) == before
    assert not list(snapshot.parent.glob(".prepare-*"))


def test_prepare_refuses_receipt_change_before_publication(cell, monkeypatch):
    adapter, api, receipt, _, _, _ = cell
    resolve = api.resolve_context
    calls = 0

    def advance(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            path = Path(receipt["installReceipt"])
            document = json.loads(path.read_text())
            document["generation"] += 1
            path.write_text(json.dumps(document))
        return resolve(*args, **kwargs)

    monkeypatch.setattr(api, "resolve_context", advance)
    with pytest.raises((PermissionError, ValueError)):
        adapter.execute(adapter.parse(_arguments(receipt)), api)
    snapshots = Path(receipt["snapshotsRoot"])
    assert not list(snapshots.glob(".prepare-*"))
    assert [path.name for path in snapshots.iterdir()] == ["snapshot-1"]


def test_prepare_refuses_package_receipt_version_mismatch_before_writing(cell):
    adapter, api, receipt, _, _, _ = cell
    path = Path(receipt["installReceipt"])
    document = json.loads(path.read_text())
    document["payload"]["version"] = "foreign"
    path.write_text(json.dumps(document))
    with pytest.raises(PermissionError, match="package version differs"):
        adapter.execute(adapter.parse(_arguments(receipt)), api)
    assert [path.name for path in Path(receipt["snapshotsRoot"]).iterdir()] == ["snapshot-1"]
    assert not Path(receipt["versionsRoot"]).exists()


def test_interrupted_snapshot_copy_is_unpublished_and_requires_explicit_repair(cell, monkeypatch):
    adapter, api, receipt, _, _, _ = cell
    snapshots = Path(receipt["snapshotsRoot"])
    copy = shutil.copytree

    def interrupt(source, destination, *args, **kwargs):
        destination = Path(destination)
        if destination.parent == snapshots:
            (destination / "partial").write_text("fixture interrupted copy\n")
            raise OSError("fixture snapshot copy interrupted")
        return copy(source, destination, *args, **kwargs)

    with monkeypatch.context() as crash:
        crash.setattr(shutil, "copytree", interrupt)
        with pytest.raises(OSError, match="copy interrupted"):
            adapter.execute(adapter.parse(_arguments(receipt)), api)
    target, = [path for path in snapshots.iterdir() if path.name != "snapshot-1"]
    assert not (target / api.SNAPSHOT_PROVENANCE_FILE).exists()
    before = (target / "partial").read_bytes()
    with pytest.raises(ValueError):
        adapter.execute(adapter.parse(_arguments(receipt)), api)
    assert (target / "partial").read_bytes() == before
    assert not Path(receipt["versionsRoot"]).exists()
    assert not Path(receipt["stateRoot"]).exists()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.timeout(180)
@pytest.mark.skipif(os.name != "nt", reason="Native POSIX activation requires an isolated OS account")
def test_normal_install_prepares_builds_and_cold_activates_without_manual_snapshot(cell):
    adapter, api, receipt, _, _, home = cell
    activate_fixture(cell)
    arguments = _arguments(receipt)
    arguments[0] = "install"
    result = subprocess.run(
        [sys.executable, "-I", str(adapter.PAYLOAD / "scripts" / "cell_install.py"), *arguments],
        capture_output=True, text=True, timeout=150, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "ready", "activated": True, "ownerStarted": False,
    }
    root = Path(receipt["pluginRoot"])
    version = json.loads((adapter.PAYLOAD / "plugin.json").read_text())["version"]
    assert (root / "current-version").read_text().strip() == version
    assert (root / "last-known-good").read_text().strip() == version
    assert not Path(receipt["stateRoot"]).exists()
    assert not (Path(receipt["runRoot"]) / "active.json").exists()
    assert not (home / ".agent-pull-requests").exists()
    assert not (home / ".local" / "bin").exists()
    transaction = json.loads((Path(receipt["runRoot"]) / "watch-owner-transaction.json").read_text())
    assert transaction["phase"] == "complete" and transaction["successor"] is None
