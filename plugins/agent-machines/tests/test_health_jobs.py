"""Pure definition and capability selection contracts; native effects are opt-in."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from agent_machines import health_jobs as jobs
from agent_machines.__main__ import main

ENTRY = Path(__file__).resolve().parents[1] / "scripts" / "health-collection-entry.py"
SID = "S-1-5-21-111-222-333-1001"


def test_windows_definition_is_inert_limited_utc_and_contains_the_normal_policy_action():
    result = jobs.prepare(
        platform="win32", payload=r"C:\Example Tools\payload", store=r"C:\Example Data\store",
        policy_token="a" * 64, user_id=SID, windows_root=r"C:\Windows",
    )
    root = ET.fromstring(result["artifacts"]["agent-machines-health.xml"])

    def text(path):
        return root.find(path, {"t": jobs.NS}).text

    assert text("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text("t:Principals/t:Principal/t:UserId") == SID
    assert text("t:Settings/t:Enabled") == text("t:Triggers/t:TimeTrigger/t:Enabled") == "false"
    assert text("t:Settings/t:StartWhenAvailable") == "true"
    assert text("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text("t:Settings/t:ExecutionTimeLimit") == "PT1M"
    assert text("t:Triggers/t:TimeTrigger/t:StartBoundary").endswith("T00:00:15Z")
    assert text("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT1H"
    assert text("t:Actions/t:Exec/t:Command") == r"C:\Windows\System32\conhost.exe"
    assert text("t:Actions/t:Exec/t:Arguments") == (
        r'--headless C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe '
        r'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File '
        r'"C:\Example Tools\payload\bin\agent-machines.ps1" health run '
        r'--store-dir "C:\Example Data\store" --apply --expect-policy ' + "a" * 64
    )
    assert not result["registered"] and not result["enabled"]
    assert result["qualification"] == "native-scheduler-not-qualified"


def test_linux_units_escape_command_expansions_and_do_not_retry_or_enable_lingering():
    result = jobs.prepare(
        platform="linux", payload='/example/$tools%/payload "one"',
        store="/example/data $home%/store", policy_token="b" * 64,
    )
    service = result["artifacts"]["agent-machines-health.service"]
    timer = result["artifacts"]["agent-machines-health.timer"]
    assert '"/example/$$tools%%/payload \\"one\\"/bin/agent-machines"' in service
    assert '"--store-dir" "/example/data $$home%%/store"' in service
    assert 'WorkingDirectory=/example/$tools%%/payload "one"\n' in service
    assert "TimeoutStartSec=55s\nTimeoutStopSec=5s\n" in service
    assert "KillMode=control-group\nRestart=no\n" in service
    assert "OnCalendar=*-*-* *:00:15 UTC\nPersistent=true\n" in timer
    assert "OnBootSec" not in timer and "OnUnitActiveSec" not in timer
    assert not result["registered"] and not result["enabled"]


def test_linux_working_directory_preserves_literal_characters_without_exec_quoting():
    payload = '/example/a $b%/literal\\quote"'
    result = jobs.prepare(platform="linux", payload=payload, store="/example/store", policy_token="a" * 64)
    service = result["artifacts"]["agent-machines-health.service"]
    directory = next(line for line in service.splitlines() if line.startswith("WorkingDirectory="))
    assert directory == "WorkingDirectory=" + payload.replace("%", "%%")
    for invalid in ("/example/payload ", "/example/payload\\", "/example/../payload"):
        with pytest.raises(jobs.JobError, match="invalid-job-path"):
            jobs.prepare(platform="linux", payload=invalid, store="/example/store", policy_token="a" * 64)


@pytest.mark.parametrize("changes", [
    {"platform": "darwin"}, {"payload": "relative"}, {"store": "/example/\nstore"},
    {"policy_token": "none"}, {"user_id": "S-1-5-18"}, {"windows_root": "relative"},
])
def test_unqualified_requests_are_not_rendered(changes):
    fields = dict(
        platform="win32", payload=r"C:\payload", store=r"C:\store",
        policy_token="a" * 64, user_id=SID, windows_root=r"C:\Windows",
    )
    fields.update(changes)
    with pytest.raises(ValueError):
        jobs.prepare(**fields)


@pytest.fixture
def entry():
    spec = importlib.util.spec_from_file_location("health_collection_entry_fixture", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_selected_slot_declaration_handles_upgrade_and_rollback_without_import(entry, tmp_path, monkeypatch):
    package = tmp_path / "agent_machines"
    package.mkdir()
    path = package / "health-collection-v1.json"
    spec = SimpleNamespace(origin=str(package / "__init__.py"))
    monkeypatch.setattr(entry.importlib.util, "find_spec", lambda name: spec)
    run = ["health", "run", "--apply"]
    prepare = ["health", "prepare-job"]
    assert not entry.supported(run, tmp_path)
    declaration = {"schema": "agent-machines.health.collection", "version": 1, "operations": ["preview", "apply"]}
    path.write_text(json.dumps(declaration))
    assert entry.supported(run, tmp_path) and not entry.supported(prepare, tmp_path)
    declaration["operations"].append("prepare-job")
    path.write_text(json.dumps(declaration))
    assert entry.supported(prepare, tmp_path)
    spec.origin = str(tmp_path / "different" / "__init__.py")
    assert not entry.supported(run, tmp_path)
    spec.origin = str(package / "__init__.py")
    for invalid in (
        b"{", b"x" * 4097,
        json.dumps({**declaration, "version": True}).encode(),
        json.dumps({**declaration, "version": 2}).encode(),
        json.dumps({**declaration, "operations": "apply"}).encode(),
    ):
        path.write_bytes(invalid)
        assert not entry.supported(run, tmp_path)
    path.unlink()
    assert not entry.supported(prepare, tmp_path)


def test_preparation_cli_reads_policy_and_never_touches_store_or_scheduler(tmp_path, monkeypatch, capsys):
    from agent_machines import health_slots as slots

    payload, store = tmp_path / "payload", tmp_path / "store"
    (payload / "bin").mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-machines"}')
    (payload / "bin" / ("agent-machines.ps1" if sys.platform == "win32" else "agent-machines")).touch()
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": slots.POLICY_SCHEMA, "version": 2,
        "publisher": "example-worker", "continuity": "example-generation",
        "collection": {"bridge_port": None, "services": ["queue=agent-dispatch"]},
    }))
    monkeypatch.setenv("AGENT_MACHINES_PAYLOAD_ROOT", str(payload))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    arguments = ["health", "prepare-job", "--store-dir", str(store)]
    if sys.platform == "win32":
        arguments += ["--user-id", SID]
    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "prepared" and not result["registered"] and not result["enabled"]
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    assert sorted(path.name for path in payload.iterdir()) == ["bin", "plugin.json"]
    from agent_machines import cli_health

    monkeypatch.setattr(cli_health, "sys", SimpleNamespace(platform="darwin", stderr=sys.stderr))
    (payload / "bin" / "agent-machines").touch()
    assert main(arguments) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "native-health-job-unsupported"
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
