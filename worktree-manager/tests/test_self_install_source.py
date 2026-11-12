"""Read-only Manager source selection; construction has a separate native lane."""

from __future__ import annotations

import json
import hashlib
import shutil
from contextlib import contextmanager
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import self_install as si


def _payload(directory: Path, commit: str, version: str = "1.2.3") -> tuple[Path, object]:
    directory.mkdir()
    package = directory / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (package / "__main__.py").write_text("")
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "copilot-extensions-worktree-manager"\nversion = "{version}"\n',
    )
    source = si._runtime_installer().runtime_source
    digest = source.assembly_hash(directory)
    identity = source.SourceIdentity(
        "worktree-manager", version, commit, "c" * 40, digest,
        source.runtime_version(version, commit, digest),
    )
    (directory / source.SOURCE_FILE).write_text(json.dumps(identity.document()))
    return directory, identity


@pytest.fixture
def selection(tmp_path: Path, tmp_path_factory, monkeypatch: pytest.MonkeyPatch):
    root = Path(si._runtime_installer().engine._native_retained_path(tmp_path_factory.mktemp("manager") / "root"))
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "bin")
    monkeypatch.setattr(si, "control_plane_providers_dir", lambda: tmp_path / "providers")
    return root


def _select(root: Path, payload: Path, identity, *, repair: bool = False, profile: bool = False) -> Path:
    adapter = si._runtime_installer()
    generation = identity.runtime_version
    receipt = None
    local = ("copilot-extensions-worktree-manager",)
    if profile:
        binding = {
            "component": "worktree-manager", "mode": "source",
            "release": {
                "identity": "source:" + identity.source_commit,
                "sha256": "sha256:" + identity.assembly_hash,
            },
        }
        lock = "sha256:" + hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        receipt = {
            "construction": "closed-wheels", "lock_id": lock, "binding": binding,
            "distributions": {local[0]: {
                "version": identity.package_version,
                "filename": f"copilot_extensions_worktree_manager-{identity.package_version}-py3-none-any.whl",
                "sha256": "sha256:" + "1" * 64,
            }},
        }
        generation += ".profile." + lock[7:23]
    if repair:
        generation += ".repair." + "f" * 32
    slot = si.version_slot(generation, root)
    shutil.copytree(payload, slot / "payload")
    python = slot / ("Scripts/python.exe" if si.os.name == "nt" else "bin/python")
    python.parent.mkdir(exist_ok=True)
    python.write_text("fixture interpreter")
    payload_hash = "a" * 64
    if receipt is not None:
        (slot / ".runtime-input-origins.json").write_text(json.dumps({
            "schema": "installer-engine.frozen-input-origins", "version": 2,
            "wheel_inputs": receipt, "local_packages": list(local),
        }))
        reuse = adapter.engine.WheelReuseInputs(
            generation, json.dumps(receipt, sort_keys=True, separators=(",", ":")),
            time.monotonic() + 30,
        )
        payload_hash = adapter.engine._wheel_construction_hash(
            adapter.engine._construction_hash(
                adapter.engine._source_files(adapter.engine._package_roots(payload)), (), local,
            ), reuse,
        )
    adapter.runtime.mark_complete(root, generation, payload_hash=payload_hash)
    si._write_marker(root, generation)
    si._deploy_binstubs()
    si._write_control_plane_provider_manifest(payload, root=root)
    return slot


def _bytes(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.parent.rglob("*") if path.is_file()}


@pytest.mark.parametrize("repair", [False, True])
def test_exact_source_plan_reports_actual_selected_generation(selection, tmp_path, repair):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, repair=repair)
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "already-current", result
    assert result.version == "1.2.3"
    assert result.slot == str(slot)
    assert result.marker == slot.name
    assert _bytes(selection) == before


@pytest.mark.parametrize("repair", [False, True])
def test_profile_source_plan_reuses_bound_selected_generation(selection, tmp_path, repair):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, profile=True, repair=repair)
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "already-current", result
    assert result.slot == str(slot) and result.marker == slot.name
    reused, local = si._runtime_installer()._source_profile_reuse(payload, identity, slot)
    assert reused.generation == slot.name
    assert local == ("copilot-extensions-worktree-manager",)
    assert _bytes(selection) == before


@pytest.mark.parametrize("difference", [None, "profile", "locals"])
def test_explicit_profile_plan_does_not_reuse_another_selected_profile(selection, tmp_path, difference):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, profile=True)
    adapter = si._runtime_installer()
    incoming, local = adapter._source_profile_reuse(payload, identity, slot)
    if difference == "profile":
        receipt = incoming.receipt()
        receipt["binding"]["acquisition_policy"] = "different-admitted-profile"
        receipt["lock_id"] = "sha256:" + hashlib.sha256(json.dumps(
            receipt["binding"], sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        incoming = adapter.engine.WheelReuseInputs(
            identity.runtime_version + ".profile." + receipt["lock_id"][7:23],
            json.dumps(receipt, sort_keys=True, separators=(",", ":")), time.monotonic() + 30,
        )
    before = _bytes(selection)
    result = si.self_install(
        payload, root=selection, dry_run=True,
        _wheel_inputs=incoming, _local_packages=() if difference == "locals" else local,
    )
    if difference is None:
        assert result.action == "already-current" and result.slot == str(slot)
    else:
        assert result.action == "planned"
        expected = identity.runtime_version + ".profile." + incoming.receipt()["lock_id"][7:23]
        assert result.slot == str(si.version_slot(expected, selection))
        assert result.marker is None
    assert _bytes(selection) == before


@pytest.mark.parametrize("separator", [".", "+"])
@pytest.mark.parametrize("same_profile", [False, True])
def test_native_wheel_candidate_reuses_selected_repair_without_skipping_health(
    tmp_path, monkeypatch, separator, same_profile,
):
    from packaging.markers import default_environment
    from installer_wheel_snapshot import retain_wheel_inputs

    adapter = si._runtime_installer()
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    (payload / adapter.runtime_source.SOURCE_FILE).unlink()
    uv = tmp_path / "uv"
    uv.touch()
    monkeypatch.setattr(adapter.engine, "_select_uv_executable", lambda _value: str(uv))
    archive = tmp_path / "copilot_extensions_worktree_manager-1.2.3-py3-none-any.whl"
    archive.write_bytes(b"retained already-inspected native fixture")
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    name = "copilot-extensions-worktree-manager"
    binding = json.dumps({
        "wheels": [{"name": name, "selected_extras": []}],
        "target": {"environment": default_environment()},
    }, sort_keys=True, separators=(",", ":"))
    root = tmp_path / "runtime"
    with retain_wheel_inputs(
        {archive.name: archive}, {archive.name: digest},
        max_wheels=1, max_archive_bytes=1024, max_total_bytes=1024,
        max_snapshot_bytes=2048, deadline=time.monotonic() + 30,
    ) as retained:
        wheels = adapter.engine.WheelInstallInputs(
            retained, binding, ((name, "1.2.3", archive.name, digest),),
        )
        generation = identity.package_version + ".profile." + wheels.receipt()["lock_id"][7:23]
        previous = generation if same_profile else identity.package_version + ".profile." + "0" * 16
        previous += separator + "repair." + "d" * 32
        (root / "versions" / previous).mkdir(parents=True)
        adapter.runtime.mark_complete(root, previous, payload_hash="a" * 64)
        si._write_marker(root, previous)

        class CandidateAdmissionReached(Exception):
            pass

        def candidate(requested, version, **arguments):
            assert requested == root
            assert version == (previous if same_profile else generation)
            assert arguments["wheel_inputs"] is wheels
            assert arguments["reuse_generations"] is False
            assert arguments["health_callable"] == "verify_runtime_candidate"
            raise CandidateAdmissionReached

        monkeypatch.setattr(adapter.engine, "numbered_candidate", candidate)
        with pytest.raises(CandidateAdmissionReached):
            with adapter.candidate(
                payload, root, assemble=shutil.copytree, uv=uv, native_wheel_inputs=wheels,
            ):
                pytest.fail("candidate admission was bypassed")


@pytest.mark.parametrize("fault", ["binding", "locals", "completion-hash", "encoding", "record"])
def test_profile_source_plan_ignores_unusable_old_constructor_binding(selection, tmp_path, fault):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, profile=True)
    if fault == "completion-hash":
        marker = slot / ".install-complete.json"
        data = json.loads(marker.read_text())
        data["payload_hash"] = "b" * 64
        marker.write_text(json.dumps(data))
    elif fault in {"encoding", "record"}:
        (slot / ".runtime-input-origins.json").write_bytes(
            b"\xff\n" if fault == "encoding" else b'{"schema":"unsupported"}\n',
        )
    else:
        path = slot / ".runtime-input-origins.json"
        data = json.loads(path.read_text())
        if fault == "binding":
            data["wheel_inputs"]["binding"]["release"]["identity"] = "source:" + "b" * 40
        else:
            data["local_packages"] = []
        path.write_text(json.dumps(data))
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert result.slot == str(si.version_slot(identity.runtime_version, selection))
    assert result.marker is None
    assert _bytes(selection) == before


@pytest.mark.parametrize("operation", ["_package_roots", "_construction_hash"])
def test_old_profile_fallback_does_not_swallow_incoming_input_io(
    selection, tmp_path, monkeypatch, operation,
):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, profile=True)
    adapter = si._runtime_installer()
    before = _bytes(selection)
    io_error = PermissionError("incoming source read denied")

    def failed_input(*args, **kwargs):
        raise io_error

    monkeypatch.setattr(adapter.engine, operation, failed_input)
    with pytest.raises(PermissionError, match="incoming source") as caught:
        adapter._source_profile_reuse(payload, identity, slot)
    assert caught.value is io_error
    assert _bytes(selection) == before


@pytest.mark.parametrize("version", ["1.2.3", "2.0.0"])
def test_different_source_is_planned_even_with_matching_package(selection, tmp_path, version):
    previous, identity = _payload(tmp_path / "previous", "a" * 40, version)
    _select(selection, previous, identity)
    payload, desired = _payload(tmp_path / "payload", "b" * 40)
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert result.slot == str(si.version_slot(desired.runtime_version, selection))
    assert result.marker is None
    assert _bytes(selection) == before


@pytest.mark.parametrize("field", ["version", "completed_at"])
def test_source_selection_requires_valid_completion(selection, tmp_path, field):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity)
    marker = slot / ".install-complete.json"
    document = json.loads(marker.read_text())
    document.pop(field)
    marker.write_text(json.dumps(document))
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert _bytes(selection) == before


@pytest.mark.parametrize("selected,profile", [(False, False), (True, False), (True, True)])
def test_incoming_source_bytes_remain_strict_while_old_bytes_cannot_veto(
    selection, tmp_path, selected, profile,
):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    slot = _select(selection, payload, identity, profile=profile)
    damaged = slot / "payload" if selected else payload
    (damaged / "src" / "worktree_manager" / "__main__.py").write_text("changed bytes\n")
    before = _bytes(selection)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == ("planned" if selected else "error"), result
    if selected:
        assert result.slot == str(si.version_slot(identity.runtime_version, selection))
    else:
        assert "bytes do not match" in result.reason
    assert _bytes(selection) == before


def test_first_source_plan_names_immutable_target_without_creating_root(selection, tmp_path):
    payload, identity = _payload(tmp_path / "payload", "a" * 40)
    result = si.self_install(payload, root=selection, dry_run=True)
    assert result.action == "planned", result
    assert result.slot == str(si.version_slot(identity.runtime_version, selection))
    assert not selection.exists()


@pytest.mark.parametrize("failure", ["content", "health", "io"])
def test_candidate_selects_distinct_generation_for_repair_not_io_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    adapter = si._runtime_installer()
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text(
        '[project]\nname="copilot-extensions-worktree-manager"\nversion="1.2.3"\n',
        encoding="utf-8",
    )
    root = tmp_path / "root"
    previous = root / "versions" / "1.2.3"
    previous.mkdir(parents=True)
    sentinel = previous / "retained-code"
    sentinel.write_bytes(b"untouched predecessor")
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    uv = tmp_path / "uv"
    uv.touch()
    monkeypatch.setattr(adapter.engine, "_select_uv_executable", lambda _: str(uv))
    monkeypatch.setattr(adapter.runtime, "is_complete", lambda *a, **kw: True)
    entries = []

    @contextmanager
    def candidate(actual_root: Path, generation: str, **kwargs: object):
        assert actual_root == root
        entries.append(generation)
        if len(entries) == 1:
            if failure == "content":
                adapter.engine._refuse(adapter.runtime.ADMIT_CONTENT_CONFLICT, generation)
            if failure == "health":
                raise adapter.engine.HealthRepairRequired("fixture installed health failure")
            raise OSError("fixture write denied")
        assert kwargs["reuse_generations"] is False
        assert generation.startswith("1.2.3+repair.")
        yield SimpleNamespace(slot=root / "versions" / generation)

    monkeypatch.setattr(adapter.engine, "numbered_candidate", candidate)
    if failure == "io":
        with pytest.raises(OSError, match="write denied"):
            with adapter.candidate(payload, root, assemble=shutil.copytree, uv=uv):
                pytest.fail("No constructed candidate")
        assert entries == ["1.2.3"]
    else:
        with adapter.candidate(payload, root, assemble=shutil.copytree, uv=uv) as ready:
            assert ready.slot.name == entries[-1] != previous.name
        assert len(entries) == 2
    assert sentinel.read_bytes() == b"untouched predecessor"
    assert (root / "current-version").read_text(encoding="utf-8") == "1.2.3\n"
