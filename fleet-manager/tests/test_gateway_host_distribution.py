"""Real offline application installation from an isolated, complete wheel closure."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import zipfile

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name
import pytest

from fleet_manager import gateway_host as host
from test_client_distribution import PROJECT, build, project_at, run
from test_gateway_host import selected as select_host


def _installed_wheel(distribution, output):
    """Keep actual installed public dependency bytes; replace only wheel RECORD."""
    metadata = distribution.metadata
    wheel_metadata = distribution.read_text("WHEEL")
    assert wheel_metadata
    tag = next(line[5:] for line in wheel_metadata.splitlines() if line.startswith("Tag: "))
    name = metadata["Name"].replace("-", "_")
    version = distribution.version.replace("-", "_")
    path = output / f"{name}-{version}-{tag}.whl"
    records = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in distribution.files:
            relative = member.as_posix()
            if (
                ".." in member.parts or member.is_absolute() or relative.endswith(".pyc")
                or member.name in {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
            ):
                continue
            data = distribution.locate_file(member).read_bytes()
            archive.writestr(relative, data)
            records.append((
                relative,
                "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(),
                str(len(data)),
            ))
        record_path = next(
            member.as_posix() for member in distribution.files
            if member.name == "RECORD" and member.parent.name.endswith(".dist-info")
        )
        table = io.StringIO(newline="")
        csv.writer(table, lineterminator="\n").writerows([*records, (record_path, "", "")])
        archive.writestr(record_path, table.getvalue())
    return path


def _closure(first_party, output):
    environment = default_environment()
    archives = dict(first_party)
    selected = {}
    pending = [Requirement("fleet-manager[gateway-host]")]
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        extras = {"", *requirement.extras}
        if name in selected and extras <= selected[name]:
            continue
        selected.setdefault(name, set()).update(extras)
        if name in first_party:
            with zipfile.ZipFile(first_party[name]) as archive:
                member = next(path for path in archive.namelist() if path.endswith(".dist-info/METADATA"))
                import email

                metadata = email.message_from_bytes(archive.read(member))
            requirements = metadata.get_all("Requires-Dist", [])
        else:
            distribution = importlib.metadata.distribution(name)
            if name not in archives:
                archives[name] = _installed_wheel(distribution, output)
            requirements = distribution.requires or []
        for text in requirements:
            child = Requirement(text)
            if child.marker is None or any(
                child.marker.evaluate({**environment, "extra": extra}) for extra in selected[name]
            ):
                pending.append(child)
    return archives


def prepared_application(tmp_path, wheel, monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT.parent / "tools"))
    import local_install_profile as native

    uv = shutil.which("uv")
    assert uv
    original = tmp_path / "source-snapshot"
    original.mkdir()
    source, _ = project_at(original / "source")
    engine = native.source_inputs.engine
    packages = engine._package_roots(PROJECT)
    for package in packages:
        if package != PROJECT:
            shutil.copytree(
                package, source.parent / package.relative_to(PROJECT.parent),
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "build", "dist", "*.egg-info"),
            )
    output = tmp_path / "wheels"
    output.mkdir()
    first_party = {"fleet-manager": wheel[0]}
    for package in packages:
        if package != PROJECT:
            package_output = output / package.name
            result = build(source.parent / package.relative_to(PROJECT.parent), package_output)
            assert result.returncode == 0, result.stderr
            first_party[engine._package_name(package)] = next(package_output.glob("*.whl"))
    archives = _closure(first_party, output)
    version = engine.runtime_source.payload_package_version(source, "fleet-manager")
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        "fleet-manager", version, "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version(version, "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    expected = {
        archive.name: "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
        for archive in archives.values()
    }
    project = {
        "payload": "source/fleet-manager", "source": identity.document(), "wheels": "wheels",
        "artifact": {"wheels": [{
            "name": name, "filename": archive.name, "sha256": expected[archive.name],
        } for name, archive in first_party.items()]},
    }
    selected = native.source_inputs.ProjectSource(source, identity, {
        "schema": native.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"fleet-manager": project},
    }, "fleet-manager")
    deadline = time.monotonic() + 120
    with native.retain_wheel_inputs(
        {archive.name: archive for archive in archives.values()}, expected,
        max_wheels=64, max_archive_bytes=64 << 20, max_total_bytes=256 << 20,
        max_snapshot_bytes=512 << 20, deadline=deadline,
    ) as retained:
        profile = native.complete_profile(
            selected, {"device": "cpu", "environment": default_environment(),
                       "tags": [str(tag) for tag in sys_tags()]},
            [f"fleet-manager[gateway-host]=={version}"], retained,
            policy_id="isolated-installed-dependency-fixture",
            acquisition_id="isolated-installed-dependency-fixture", deadline=deadline,
        )
        prepared = tmp_path / "prepared"
        prepared.mkdir()
        snapshot = native._write_snapshot(selected, prepared, profile, retained, deadline=deadline, uv=uv)
        updated_profile = native.complete_profile(
            selected, {"device": "cpu", "environment": default_environment(),
                       "tags": [str(tag) for tag in sys_tags()]},
            [f"fleet-manager[gateway-host]=={version}"], retained,
            policy_id="isolated-updated-dependency-fixture",
            acquisition_id="isolated-installed-dependency-fixture", deadline=deadline,
        )
        updated = tmp_path / "updated"
        updated.mkdir()
        updated_snapshot = native._write_snapshot(
            selected, updated, updated_profile, retained, deadline=deadline, uv=uv,
        )
    initial = native.source_inputs.select_project(snapshot, "fleet-manager")
    updated = native.source_inputs.select_project(updated_snapshot, "fleet-manager")
    return (
        snapshot, snapshot / initial.project["application"],
        updated_snapshot / updated.project["application"],
    )


@pytest.mark.client_distribution_full
def test_selected_gateway_application_first_touch_repeat_update_and_stop(tmp_path_factory, wheel, monkeypatch):
    tmp_path = tmp_path_factory.mktemp("g")
    snapshot, application, updated_application = prepared_application(tmp_path, wheel, monkeypatch)
    root = tmp_path / "i"
    selection = select_host(root)
    original_config = (root / host.CONFIG).read_bytes()
    original_policy = Path(selection["policy"]).read_bytes()
    declaration = json.loads((application / "application.json").read_text())
    generation = declaration["construction"]["runtime_version"]
    first = root / "versions" / generation
    shutil.copytree(application, first)
    home = tmp_path / "home"
    home.mkdir()
    clean = {
        name: value for name, value in os.environ.items()
        if not name.startswith("UV_")
        and name not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "FLEET_MANAGER_HOST_NONCE"}
    }
    clean.update(
        HOME=str(home), USERPROFILE=str(home), UV_OFFLINE="1", UV_PYTHON=sys.executable,
        UV_PYTHON_DOWNLOADS="never", UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        FLEET_MANAGER_HOME=str(root),
    )
    if os.name == "nt":
        clean.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    extension = ".ps1" if os.name == "nt" else ".sh"

    def invoke(slot, command, *arguments):
        executable = slot / (command + extension)
        launcher = (
            [shutil.which("powershell.exe"), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-File", str(executable)]
            if os.name == "nt" else [str(executable)]
        )
        return run([*launcher, *arguments], cwd=home, env=clean, timeout=90)

    def status(slot):
        result = invoke(slot, "fleet-manager", "gateway-host", "status")
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    selected_slot = first
    try:
        staged = invoke(first, "install", "--stage")
        assert staged.returncode == 0, staged.stdout + staged.stderr
        assert not (root / "current-version").exists()
        assert not (root / host.STATE).exists()
        host.private_directory(root / host.STATE)
        refused_log = root / host.STATE / "host.log"
        refused_log.mkdir()
        refused = invoke(first, "install")
        assert refused.returncode != 0
        assert not (root / host.STATE / "launch.json").exists()
        assert not (root / host.STATE / host.RUNTIME).exists()
        refused_log.rmdir()
        installed = invoke(first, "install")
        if installed.returncode:
            diagnostic = run([
                str(first / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
                "-I", "-B", "-c",
                "from fleet_manager.gateway_host import callback; callback('__source-setup')",
            ], cwd=home, env=clean)
            pytest.fail(installed.stdout + installed.stderr + diagnostic.stdout + diagnostic.stderr)
        assert installed.returncode == 0, installed.stdout + installed.stderr
        initial = status(first)
        assert initial["ready"] and initial["selected"]
        unavailable = snapshot.with_name("unavailable-snapshot")
        snapshot.rename(unavailable)
        assert status(first)["pid"] == initial["pid"]
        repeated = invoke(first, "install")
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        assert status(first)["pid"] == initial["pid"]
        updated_declaration = json.loads((updated_application / "application.json").read_text())
        updated_slot = root / "versions" / updated_declaration["construction"]["runtime_version"]
        assert updated_slot != first
        shutil.copytree(updated_application, updated_slot)
        host.write_private(root / host.CONFIG, selection | {"tls": str(root / "absent-listener.json")})
        failed = invoke(updated_slot, "install")
        assert failed.returncode != 0
        assert (root / "current-version").read_text().strip() == first.name
        assert (root / "last-known-good").read_text().strip() == first.name
        assert status(first)["pid"] == initial["pid"]
        host.write_private(root / host.CONFIG, selection)
        updated = invoke(updated_slot, "install")
        assert updated.returncode == 0, updated.stdout + updated.stderr
        selected_slot = updated_slot
        incoming = status(updated_slot)
        assert incoming["ready"] and incoming["selected"]
        assert incoming["generation"] == updated_slot.name and incoming["pid"] != initial["pid"]
        assert (root / "last-known-good").read_text().strip() == updated_slot.name
        assert (root / host.CONFIG).read_bytes() == original_config
        assert Path(selection["policy"]).read_bytes() == original_policy
        stopped = invoke(updated_slot, "fleet-manager", "gateway-host", "stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert json.loads(stopped.stdout)["native_exit_confirmed"] is True
        assert status(updated_slot)["state"] == "not-running"
    finally:
        if (root / host.STATE / host.RUNTIME).exists():
            result = invoke(selected_slot, "fleet-manager", "gateway-host", "stop")
            assert result.returncode == 0, result.stdout + result.stderr
