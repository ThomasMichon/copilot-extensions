"""Partial phase items; only the full acceptance runner qualifies both journeys."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_local_install_source import complete_native_source, prepare_native_source_inputs

_SPEC = importlib.util.spec_from_file_location(
    "_application_adapter_acceptance", Path(__file__).with_name("run-manager-runtime-acceptance.py"),
)
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)
PHASES = acceptance.APPLICATION_ADAPTER_PHASES


@pytest.fixture(scope="class")
def adapter_journey(request, tmp_path_factory):
    return SimpleNamespace(
        variant=request.param,
        directory=tmp_path_factory.mktemp(request.param[:1]),
        completed=[],
    )


def _environment(home, monkeypatch):
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    if os.name == "nt":
        monkeypatch.setenv("HOMEDRIVE", home.drive)
        monkeypatch.setenv("HOMEPATH", str(home)[len(home.drive):])


def _run_application_adapter_phase(adapter_journey, phase, monkeypatch):
    journey = adapter_journey
    expected = list(PHASES[:PHASES.index(phase)])
    assert journey.completed == expected, (
        f"{journey.variant}/{phase} requires completed ordered phases {expected}; "
        "an independently selected phase is not full journey coverage"
    )
    tmp_path = journey.directory
    if phase == "inputs":
        journey.inputs = prepare_native_source_inputs(tmp_path, SimpleNamespace(param={
            "entry": True, "standalone": journey.variant == "standalone",
        }))
        journey.completed.append(phase)
        return
    if phase == "prepare":
        source, snapshot = complete_native_source(tmp_path, journey.inputs)
        journey.source, journey.snapshot = source, snapshot
        journey.completed.append(phase)
        return
    source, snapshot = journey.source, journey.snapshot
    if phase == "selection":
        with source.project_inputs(snapshot, "demo-app") as (selected, inputs):
            selection = source.native_selection(selected, inputs)
        application = snapshot / selected.project["application"]
        path = application / "application.json"
        record = json.loads(path.read_text())
        record.update(setup=["--setup"], start=["--start"])
        path.write_text(json.dumps(record))
        home = tmp_path / "home"
        home.mkdir()
        _environment(home, monkeypatch)
        root = tmp_path / "runtime"
        options = dict(
            uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
            health_module="demo_app", expected_version=selection["runtime_version"],
            expected_hash=selection["payload_hash"],
        )
        journey.selected, journey.selection = selected, selection
        journey.path, journey.record, journey.home = path, record, home
        journey.root, journey.options = root, options
        journey.completed.append(phase)
        return
    selected, selection = journey.selected, journey.selection
    path, record, home = journey.path, journey.record, journey.home
    root, options = journey.root, journey.options
    _environment(home, monkeypatch)
    if phase == "install":
        result = source.install_application(snapshot, "demo-app", selected.payload, root, **options)
        slot = root / "versions" / selection["runtime_version"]
        assert result == {"state": "installed", "slot": str(slot)}
        before = {name: (root / name).read_bytes() for name in ("current-version", "last-known-good")}
        assert json.loads((root / "setup.json").read_text()) == ["--setup"]
        assert json.loads((root / "started.json").read_text()) == ["--start"]
        marker = source.engine.runtime.marker_path(root, slot.name).read_bytes()
        journey.result, journey.slot = result, slot
        journey.before, journey.marker = before, marker
    elif phase == "repeat":
        result, slot, marker = journey.result, journey.slot, journey.marker
        assert source.install_application(snapshot, "demo-app", selected.payload, root, **options) == result
        assert source.engine.runtime.marker_path(root, slot.name).read_bytes() == marker
        installed_declaration = (slot / "application.json").read_bytes()
        callbacks = {name: (root / name).read_bytes() for name in ("setup.json", "started.json")}
        journey.installed_declaration, journey.callbacks = installed_declaration, callbacks
    elif phase == "refusal":
        slot, before = journey.slot, journey.before
        installed_declaration, callbacks = journey.installed_declaration, journey.callbacks
        record.update(setup=["--setup", "--new-setup"], start=["--start", "--new-start"])
        path.write_text(json.dumps(record))
        with pytest.raises(source.engine.ContentRepairRequired, match="distinct repair generation"):
            source.install_application(snapshot, "demo-app", selected.payload, root, **options)
        assert {name: (root / name).read_bytes() for name in callbacks} == callbacks
        assert {name: (root / name).read_bytes() for name in before} == before
        assert (slot / "application.json").read_bytes() == installed_declaration
    elif phase == "fail-start":
        slot, before = journey.slot, journey.before
        with pytest.raises(subprocess.CalledProcessError):
            source.install_application(
                snapshot, "demo-app", selected.payload, root,
                arguments=("--fail-start",), repair=True, **options,
            )
        failed = [path for path in (root / "versions").iterdir() if path != slot]
        assert len(failed) == 1 and not (failed[0] / "install-success.json").exists()
        assert (root / "current-version").read_text().strip() == failed[0].name
        assert (root / "last-known-good").read_bytes() == before["last-known-good"]
    elif phase == "retry":
        slot, installed_declaration = journey.slot, journey.installed_declaration
        repaired = source.install_application(
            snapshot, "demo-app", selected.payload, root, repair=True, **options,
        )
        repaired_slot = Path(repaired["slot"])
        assert repaired_slot != slot and ".repair." in repaired_slot.name
        assert (root / "current-version").read_text().strip() == repaired_slot.name
        assert json.loads((root / "setup.json").read_text()) == ["--setup", "--new-setup"]
        assert json.loads((root / "started.json").read_text()) == ["--start", "--new-start"]
        assert (slot / "application.json").read_bytes() == installed_declaration
    elif phase == "command":
        launcher = home / ".local" / "bin" / ("demo-app.ps1" if os.name == "nt" else "demo-app")
        command = (
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-File", str(launcher)]
            if os.name == "nt" else [str(launcher)]
        )
        probe = subprocess.run(
            command, cwd=tmp_path, env=dict(os.environ), capture_output=True, text=True,
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert probe.stdout.strip() == "prepared application ready"
    else:
        pytest.fail("unknown application adapter phase")
    journey.completed.append(phase)
    if phase == PHASES[-1]:
        assert journey.completed == list(PHASES)


@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "adapter_journey", acceptance.APPLICATION_ADAPTER_VARIANTS,
    ids=acceptance.APPLICATION_ADAPTER_VARIANTS, indirect=True, scope="class",
)
class TestApplicationAdapterJourney:
    def test_inputs(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "inputs", monkeypatch)

    def test_prepare(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "prepare", monkeypatch)

    def test_selection(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "selection", monkeypatch)

    def test_install(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "install", monkeypatch)

    def test_repeat(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "repeat", monkeypatch)

    def test_refusal(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "refusal", monkeypatch)

    def test_fail_start(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "fail-start", monkeypatch)

    def test_retry(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "retry", monkeypatch)

    def test_command(self, adapter_journey, monkeypatch):
        _run_application_adapter_phase(adapter_journey, "command", monkeypatch)
