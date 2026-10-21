"""Native snapshot-copy parity and refusal contracts in both engine layouts."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from .installer_test_support import (
    assert_installer_isolated,
    guard_installer_payload,
    isolate_installer_environment,
    snapshot_tree_manifest,
)
from .test_installer_engine_adoption import (
    ENGINE,
    PLUGIN,
    PWSH,
    REPO,
    _assert_codespaces_engine_wiring,
    _env,
    _fake_uv,
)

pytestmark = pytest.mark.timeout(60)


@pytest.fixture(scope="module", params=["source", "materialized"])
def copy_layout(tmp_path_factory, request):
    if PWSH is None:
        if os.name == "nt":
            pytest.fail("The required Windows native copy lane needs PowerShell")
        pytest.skip("PowerShell is unavailable")
    root = tmp_path_factory.mktemp(f"snapshot-copy-{request.param}")
    payload = root / "plugins" / PLUGIN.name
    shutil.copytree(PLUGIN, payload, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    shutil.copytree(ENGINE, root / "libs" / "installer-engine")
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_main
        if request.param == "materialized":
            logs = materialize_main.materialize_installer_engine_refs(root, canonical_root=REPO)
            assert not any(line.startswith("SKIP") for line in logs), "\n".join(logs)
    finally:
        sys.path.pop(0)
    _assert_codespaces_engine_wiring(payload, repo_root=root)
    engine = (
        payload / "scripts" / "installer-engine.ps1"
        if request.param == "materialized"
        else root / "libs" / "installer-engine" / "installer-engine.ps1"
    )
    assert engine.read_bytes() == (ENGINE / "installer-engine.ps1").read_bytes()
    carrier = root / "guard-carrier"
    (carrier / "scripts").mkdir(parents=True)
    installer = carrier / "scripts" / "install.ps1"
    shutil.copyfile(payload / "scripts" / "install.ps1", installer)
    before = installer.read_text(encoding="utf-8")
    guard_installer_payload(carrier)
    after = installer.read_text(encoding="utf-8")
    marker = "Set-StrictMode -Version Latest"
    prefix, suffix = before.split(marker)
    guard = after[len(prefix + marker):-len(suffix)]
    assert before.replace(marker, marker + guard) == after
    return root, payload, engine, guard


def _literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _native(copy_layout, tmp_path, body: str):
    _root, _payload, engine, guard = copy_layout
    env = _env(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    _fake_uv(fake_bin)
    isolate_installer_environment(env, tmp_path / "home", fake_bin, PWSH)
    script = tmp_path / "copy-contract.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n"
        + guard + "\n. " + _literal(engine) + "\n"
        + "try {\n" + body + "\n} catch {\n"
        + "  [Console]::Error.WriteLine('copy contract failed: ' + $_.Exception.Message)\n"
        + "  exit 1\n}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        check=False,
    )
    assert_installer_isolated(fake_bin)
    return result


def _copy(copy_layout, tmp_path, source, destination, *, baseline=False):
    command = (
        f"Copy-Item -LiteralPath {_literal(source)} -Destination {_literal(destination)} "
        "-Recurse -Force -ErrorAction Stop"
        if baseline else
        f"Copy-InstallSnapshotItem -Source {_literal(source)} -Destination {_literal(destination)}"
    )
    return _native(copy_layout, tmp_path, command)


def _tree(root: Path) -> None:
    (root / "nested" / "empty-directory").mkdir(parents=True)
    (root / "nested" / "binary [literal].dat").write_bytes(bytes(range(256)) * 3)
    (root / "nested" / ".hidden-entry").write_bytes(b"hidden\n")
    (root / "empty-file").write_bytes(b"")
    readonly = root / "readonly.bin"
    readonly.write_bytes(b"read-only source")
    readonly.chmod(stat.S_IREAD)


def _assert_complete_copy(source: Path, destination: Path) -> None:
    assert destination.exists(), "copy destination was not produced"
    assert snapshot_tree_manifest(destination) == snapshot_tree_manifest(source)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_snapshot_copy_matches_native_for_fresh_destination(copy_layout, tmp_path, kind):
    source = tmp_path / "input [literal]"
    if kind == "directory":
        _tree(source)
        if os.name == "nt":
            result = _native(
                copy_layout, tmp_path,
                f"[IO.File]::SetAttributes({_literal(source / 'nested' / '.hidden-entry')}, "
                "[IO.FileAttributes]::Hidden)",
            )
            assert result.returncode == 0, result.stdout + result.stderr
    else:
        source.write_bytes(bytes(range(256)))
    before = snapshot_tree_manifest(source)
    baseline = tmp_path / "native"
    actual = tmp_path / "actual"
    for destination, native in ((baseline, True), (actual, False)):
        result = _copy(copy_layout, tmp_path, source, destination, baseline=native)
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_complete_copy(source, destination)
    assert snapshot_tree_manifest(actual) == snapshot_tree_manifest(baseline)
    assert snapshot_tree_manifest(source) == before


@pytest.mark.parametrize("fault", ["missing-source", "destination-file"])
def test_snapshot_copy_errors_are_not_success(copy_layout, tmp_path, fault):
    source = tmp_path / "input"
    if fault == "destination-file":
        _tree(source)
    for name, native in (("baseline", True), ("actual", False)):
        destination = tmp_path / name
        if fault == "destination-file":
            destination.write_bytes(b"destination sentinel")
        result = _copy(copy_layout, tmp_path, source, destination, baseline=native)
        assert result.returncode != 0, result.stdout + result.stderr
        assert "copy contract failed:" in result.stderr
        if fault == "destination-file":
            assert destination.read_bytes() == b"destination sentinel"
        else:
            assert not destination.exists()


@pytest.mark.parametrize("mutation", ["no-op", "drop-entry", "truncate", "wrong-destination"])
def test_manifest_oracle_refuses_defective_copy(copy_layout, tmp_path, mutation):
    root, payload, engine, guard = copy_layout
    text = engine.read_text(encoding="utf-8")
    helper = re.search(r"(?ms)^function Copy-InstallSnapshotItem \{.*?^\}", text)
    assert helper is not None
    original = helper.group()
    if mutation == "no-op":
        damaged = original.replace("$attributes = [System.IO.File]::GetAttributes($Source)", "return")
    elif mutation == "drop-entry":
        damaged = original.replace(
            "            Copy-InstallSnapshotItem -Source $child",
            "            if ([IO.Path]::GetFileName($child) -eq '.hidden-entry') { continue }\n"
            "            Copy-InstallSnapshotItem -Source $child",
        )
    elif mutation == "truncate":
        damaged = original.replace(
            "[System.IO.File]::Copy($Source, $Destination, $true)",
            "[System.IO.File]::WriteAllBytes($Destination, [byte[]]@(0))",
        )
    else:
        damaged = original.replace(
            "$attributes = [System.IO.File]::GetAttributes($Source)",
            "$Destination = $Destination + '.wrong'\n"
            "    $attributes = [System.IO.File]::GetAttributes($Source)",
        )
    assert damaged != original
    mutant = tmp_path / "mutant-engine.ps1"
    mutant.write_text(text.replace(original, damaged, 1), encoding="utf-8")
    source, destination = tmp_path / "input", tmp_path / "actual"
    _tree(source)
    result = _copy((root, payload, mutant, guard), tmp_path, source, destination)
    assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(AssertionError):
        _assert_complete_copy(source, destination)


def _junction(copy_layout, tmp_path, link, target):
    if os.name == "nt":
        result = _native(
            copy_layout, tmp_path,
            f"New-Item -ItemType Junction -Path {_literal(link)} "
            f"-Target {_literal(target)} -ErrorAction Stop | Out-Null",
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def test_root_reparse_keeps_native_copy_behavior(copy_layout, tmp_path):
    target, source = tmp_path / "target", tmp_path / "input-link"
    _tree(target)
    _junction(copy_layout, tmp_path, source, target)
    before = snapshot_tree_manifest(target)
    baseline, actual = tmp_path / "native", tmp_path / "actual"
    for destination, native in ((baseline, True), (actual, False)):
        result = _copy(copy_layout, tmp_path, source, destination, baseline=native)
        assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot_tree_manifest(actual) == snapshot_tree_manifest(baseline)
    assert snapshot_tree_manifest(target) == before


def _canonical_link_fixture(copy_layout, tmp_path):
    root, _payload, _engine, _guard = copy_layout
    working = tmp_path / "fixture"
    shutil.copytree(root, working)
    canonical = working / "libs" / "installer-engine"
    target = working / "canonical-target"
    canonical.rename(target)
    return working, canonical, target


def _validate_human_sources(working):
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import materialize_installer_engine
        return materialize_installer_engine.validate_human_output_sources(
            working, consumers=(working / "plugins" / PLUGIN.name,),
        )
    finally:
        sys.path.pop(0)


def test_materialization_characterizes_native_directory_link_policy(copy_layout, tmp_path):
    working, canonical, target = _canonical_link_fixture(copy_layout, tmp_path)
    _junction(copy_layout, tmp_path, canonical, target)
    before = snapshot_tree_manifest(working)
    if os.name == "nt":
        assert getattr(canonical.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        assert not canonical.is_symlink()
        assert _validate_human_sources(working) == frozenset({(working / "plugins" / PLUGIN.name).resolve()})
    else:
        assert canonical.is_symlink()
        with pytest.raises(ValueError, match="linked human-output source refused"):
            _validate_human_sources(working)
    assert snapshot_tree_manifest(working) == before


def test_materialization_preserves_actual_symbolic_link_refusal(copy_layout, tmp_path):
    working, canonical, target = _canonical_link_fixture(copy_layout, tmp_path)
    try:
        canonical.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.fail(f"Platform blocker: actual symbolic-link fixture unavailable: {error}", pytrace=False)
    assert canonical.is_symlink()
    before = snapshot_tree_manifest(working)
    with pytest.raises(ValueError, match="linked human-output source refused"):
        _validate_human_sources(working)
    assert snapshot_tree_manifest(working) == before


def test_stamp_missing_library_preserves_published_selection(copy_layout, tmp_path):
    root, _payload, _engine, _guard = copy_layout
    working = tmp_path / "fixture"
    shutil.copytree(root, working)
    payload = working / "plugins" / PLUGIN.name
    for parent in (payload / "libs", working / "libs"):
        library = parent / "ssh-manager"
        if library.exists():
            library.rename(parent / "unselected-ssh-manager")
    guard_installer_payload(payload)
    env = _env(tmp_path)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _fake_uv(fake_bin)
    isolate_installer_environment(env, tmp_path / "home", fake_bin, PWSH)
    install = tmp_path / "home" / ".agent-codespaces"
    previous = install / "snapshots" / "previous"
    previous.mkdir(parents=True)
    (previous / "sentinel").write_bytes(b"previous snapshot")
    for name, value in (
        ("payload-dir", str(previous)), ("stamped-version", "previous"),
        ("current-version", "previous"), ("last-known-good", "previous"),
    ):
        (install / name).write_text(value, encoding="utf-8")
    preserved = {
        name: (install / name).read_bytes()
        for name in ("payload-dir", "stamped-version", "current-version", "last-known-good")
    }
    inputs = snapshot_tree_manifest(working)
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(payload / "scripts" / "install.ps1"),
         "stamp", "-InstallDir", str(install)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        check=False,
    )
    assert result.returncode != 0
    assert "Cannot locate required snapshot library: ssh-manager" in result.stdout + result.stderr
    assert snapshot_tree_manifest(working) == inputs
    assert (previous / "sentinel").read_bytes() == b"previous snapshot"
    assert {name: (install / name).read_bytes() for name in preserved} == preserved
    assert not (tmp_path / "uv.jsonl").exists()
    assert_installer_isolated(fake_bin)
