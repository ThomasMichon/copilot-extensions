"""Receipt-only reservation lifecycle and cross-runner refusal contracts."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_snapshot_provenance import (
    PARITY_RUNNERS, POWERSHELL, _flag, _load_python_module, _provision_slot_with_python,
    _receipt_layout, _run_slot, _stamp_with_python, _write_json,
)


def reservation(tmp_path: Path, *, hidden: bool = False):
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    owned = _provision_slot_with_python(layout)
    slot = Path(owned["slotRoot"])
    ownership = slot / ".runtime-slot-ownership.json"
    record = json.loads(ownership.read_text(encoding="utf-8"))
    ownership.unlink()
    record.update(schema="copilot-extensions.runtime-slot-reservation", generation=0)
    if hidden:
        digest = hashlib.sha256(str(slot).encode()).hexdigest()[:16]
        target = slot.parent.parent / f".runtime-slot-{digest}-{'a' * 16}"
        slot.rename(target)
        slot = target
    receipt = slot / ".runtime-slot-reservation.json"
    _write_json(receipt, record)
    arguments = dict(
        context=str(layout["install"]), durable_home=str(layout["durable"]),
        expected_marketplace_id=layout["marketplace_id"], expected_plugin_id=layout["plugin_id"],
        runtime_version="3.4.5", reservation_root=str(slot), expected_reservation_generation=0,
        expected_reservation_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        expected_namespace_generation=1, expected_install_generation=2,
    )
    return layout, slot, receipt, arguments


def run_release(runner, arguments):
    _, prefix, style = runner
    command = [*prefix, "slot-release"]
    for key, value in arguments.items():
        command.extend([_flag(style, key.replace("_", "-")), str(value)])
    env = {k: v for k, v in os.environ.items() if k not in {
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
    }}
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)


@pytest.mark.installation_context_smoke
@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda r: r[0])
@pytest.mark.parametrize("hidden", [False, True])
def test_release_and_absent_replay(runner, tmp_path, hidden):
    layout, slot, receipt, arguments = reservation(tmp_path, hidden=hidden)
    before = Path(layout["install"]).read_bytes()
    for released in (True, False):
        result = run_release(runner, arguments)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value == {
            "action": "slot-release", "status": "ready",
            "reason": "runtime-slot-reservation-released" if released else "runtime-slot-reservation-absent",
            "released": released, "slotRoot": str(slot), "runtimeVersion": "3.4.5",
            "reservationGeneration": 0, "namespaceGeneration": 1, "installGeneration": 2,
            "activated": False, "operative": False,
        }
        assert not slot.exists()
    assert Path(layout["install"]).read_bytes() == before
    assert Path(layout["snapshot_root"]).is_dir()


CASES = (
    "markerless", "malformed", "foreign", "linked-receipt", "linked-root",
    "nonempty", "completed", "owned", "current", "lkg", "generation", "digest",
    "context", "root", "receipt-generation", "unknown-field", "negative-generation",
)


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda r: r[0])
def test_release_refusals_preserve_targets(runner, tmp_path):
    for number, case in enumerate(CASES):
        layout, slot, receipt, arguments = reservation(tmp_path / str(number))
        record = json.loads(receipt.read_bytes())
        if case == "markerless":
            receipt.unlink()
        elif case == "malformed":
            receipt.write_text("{", encoding="utf-8")
        elif case in {"foreign", "unknown-field", "negative-generation"}:
            record[{"foreign": "pluginId", "unknown-field": "extra", "negative-generation": "generation"}[case]] = (
                -1 if case == "negative-generation" else "foreign"
            )
            _write_json(receipt, record)
            arguments["expected_reservation_sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
        elif case.startswith("linked"):
            target = receipt if case == "linked-receipt" else slot
            moved = target.with_name(target.name + "-real")
            target.rename(moved)
            try:
                target.symlink_to(moved, target_is_directory=moved.is_dir())
            except OSError:
                if case == "linked-root" and os.name == "nt" and POWERSHELL:
                    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
                    created = subprocess.run(
                        [POWERSHELL, "-NoProfile", "-Command",
                         "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                         f"-Path {quote(target)} -Target {quote(moved)} | Out-Null"],
                        capture_output=True, text=True, timeout=30,
                    )
                    assert created.returncode == 0, created.stderr
                else:
                    moved.rename(target)
                    continue
        elif case in {"nonempty", "completed", "owned"}:
            (slot / {"nonempty": "data", "completed": ".runtime-slot-completion.json", "owned": ".runtime-slot-ownership.json"}[case]).write_text("{}", encoding="utf-8")
        elif case in {"current", "lkg"}:
            (Path(layout["plugin_root"]) / ("current-version" if case == "current" else "last-known-good")).write_text("3.4.5\n", encoding="utf-8")
        else:
            key, value = {
                "generation": ("expected_install_generation", 1),
                "digest": ("expected_reservation_sha256", "0" * 64),
                "context": ("expected_marketplace_id", "other--0123456789abcdef"),
                "root": ("reservation_root", str(Path(layout["plugin_root"]))),
                "receipt-generation": ("expected_reservation_generation", 1),
            }[case]
            arguments[key] = value
        result = run_release(runner, arguments)
        if case == "generation":
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout)["status"] == "revalidation-required"
        else:
            assert result.returncode != 0, (case, result.stdout)
        assert os.path.lexists(slot), case


@pytest.mark.parametrize("runner", PARITY_RUNNERS, ids=lambda r: r[0])
def test_successful_provision_leaves_only_immutable_ownership(runner, tmp_path):
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    result = _run_slot(runner, "slot-provision", layout)
    slot = Path(json.loads(result.stdout)["slotRoot"])
    assert {p.name for p in slot.iterdir()} == {".runtime-slot-ownership.json"}
    (slot / ".runtime-slot-reservation.json").write_text("{}", encoding="utf-8")
    refused = _run_slot(runner, "slot-validate", layout, check=False)
    assert refused.returncode != 0
    assert "unfinished reservation evidence" in refused.stderr


def test_python_hidden_reservation_is_published_before_ownership_and_rename(tmp_path, monkeypatch):
    layout = _receipt_layout(tmp_path)
    _stamp_with_python(layout)
    module = _load_python_module()
    original = module._write_private_json
    published = []

    def witness(path, value):
        if path.name == module.RUNTIME_SLOT_RESERVATION_FILE:
            assert not list(path.parent.iterdir())
            assert value["schema"] == module.RUNTIME_SLOT_RESERVATION_SCHEMA
            assert type(value["generation"]) is int and 0 <= value["generation"] <= (1 << 63) - 1
        original(path, value)
        published.append(path.name)

    monkeypatch.setattr(module, "_write_private_json", witness)
    _provision_slot_with_python(layout, module=module)
    assert published.index(module.RUNTIME_SLOT_RESERVATION_FILE) < published.index(module.RUNTIME_SLOT_OWNERSHIP_FILE)


@pytest.mark.parametrize("drift", ["receipt", "directory", "context", "marker", "content"])
def test_release_revalidates_immediately_before_delete(tmp_path, monkeypatch, drift):
    layout, slot, receipt, arguments = reservation(tmp_path)
    module = _load_python_module()
    original = module._validated_runtime_slot_ownership

    def replace(*args, **kwargs):
        result = original(*args, **kwargs)
        if drift in {"receipt", "context"}:
            target = receipt if drift == "receipt" else Path(layout["install"])
            replacement = target.with_suffix(".new")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        elif drift == "directory":
            old = slot.with_name("replaced")
            slot.rename(old)
            slot.mkdir()
            receipt.write_bytes((old / receipt.name).read_bytes())
        elif drift == "marker":
            (slot.parent.parent / "current-version").write_text("3.4.5\n", encoding="utf-8")
        else:
            (slot / "new-content").write_text("keep", encoding="utf-8")
        return result

    monkeypatch.setattr(module, "_validated_runtime_slot_ownership", replace)
    with pytest.raises(module.InstallationContextError):
        module.release_runtime_slot(**arguments)
    assert slot.exists() and receipt.exists()


def test_named_and_opened_file_metadata_use_consistent_api_clocks(tmp_path):
    module = _load_python_module()
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"synthetic executable")
    data, metadata = module._read_regular_file(
        executable, label="Executable", require_stable_identity=True
    )
    assert data == executable.read_bytes()
    assert module._stat_identity(metadata) == module._stat_identity(executable.lstat())
    assert module._snapshot_content_sha256(tmp_path)
    chunks = []
    content, _ = module._read_regular_file(
        executable, label="Streamed evidence", require_stable_identity=True,
        consume_chunk=chunks.append,
    )
    assert content == b""
    assert b"".join(chunks) == data
