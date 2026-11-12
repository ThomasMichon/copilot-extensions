"""Actor-free composition; native observation and storage stay in their lane."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import json
import sys
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from types import ModuleType, SimpleNamespace
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 9, 12, 30, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)


@pytest.fixture
def owners(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("an actor-free composition test reached a native owner")

    def package(name):
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
        return module

    def load(name, source):
        spec = importlib.util.spec_from_file_location(name, ROOT / source)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    telemetry = package("process_telemetry")
    vocabulary = package(telemetry.__name__ + ".measurement")
    vocabulary.STAGES = frozenset({"preparation"})
    vocabulary.OUTCOMES = frozenset({"ok", "error", "cancelled", "unavailable"})
    load(telemetry.__name__ + ".report", "libs/process-telemetry/src/process_telemetry/report.py")
    load(telemetry.__name__ + ".hourly", "libs/process-telemetry/src/process_telemetry/hourly.py")
    machines = package("promotion_health_composition")
    lock = package(machines.__name__ + ".killswitch_mutation")
    lock.mutation_lock = forbidden
    lock._lock_byte = lock._unlock_byte = forbidden
    sources = "plugins/agent-machines/src/agent_machines/"
    modules = {
        name: load(machines.__name__ + "." + name, sources + name + ".py")
        for name in (
            "health_slots", "health_hourly", "health_collect", "health_run",
            "health_report", "health_chart", "health_jobs", "cli_health",
        )
    }
    peer = package(machines.__name__ + "._peer_launch")
    peer.retained_runtime_identity = peer.retained_runtime_prefix = forbidden
    proc = package("agent_procutil")
    owned = package(proc.__name__ + ".owned_process")
    owned.OwnedCleanupError = type("OwnedCleanupError", (RuntimeError,), {})
    owned.run_caller_owned = forbidden
    budget = package(proc.__name__ + ".owned_budget")
    budget.OwnedExecutionBudget = SimpleNamespace
    for name in ("health_task_native", "health_enrollment", "health_job"):
        modules[name] = load(machines.__name__ + "." + name, sources + name + ".py")
    modules["runtime_launcher"] = load(
        machines.__name__ + ".runtime_launcher", "libs/installer-engine/runtime_launcher.py",
    )
    modules["task_desktop"] = load(
        machines.__name__ + ".task_desktop",
        "plugins/agent-machines/tests/native/health_task_desktop.py",
    )
    services = package(machines.__name__ + ".service_reports")
    services.collect = lambda names, hour, **fields: [
        {"instance": name, "service": name, "hour": hour, "state": "unsupported"}
        for name in names
    ]
    observer = package(machines.__name__ + ".health_observe")
    observer.TIMEOUT_SECONDS = 2
    observer.observe_bridge = forbidden
    monkeypatch.setattr(modules["health_slots"], "inspect", forbidden)
    parser = argparse.ArgumentParser(allow_abbrev=False)
    modules["cli_health"].add_parser(parser.add_subparsers(required=True))
    return modules, parser


def test_installed_enrollment_preview_binds_installation_and_refuses_foreign_state_without_effects(owners):
    modules, _ = owners
    enrollment = modules["health_enrollment"]
    desired = {
        "store": str(ROOT / "fixture-store"), "policy_token": "a" * 64,
        "user_id": "S-1-5-21-1-2-3-1000", "enabled": True,
    }
    native = {"configured": False, "enabled": False, "instances": [], "digest": "absent"}
    owner = object.__new__(enrollment.Enrollment)
    owner.root = ROOT
    owner.owner = "urn:copilot-extensions:health-job:" + "a" * 64
    owner.backend = SimpleNamespace(call=lambda operation: copy.deepcopy(native))
    owner.read = enrollment.empty
    owner._policy = lambda *_args: None
    owner.selector = lambda *_args: None
    owner.converge = lambda **_fields: pytest.fail("preview reached convergence")
    preview = owner.configure(desired)
    assert preview["status"] == "preview" and not preview["configured"]
    owner.owner = "urn:copilot-extensions:health-job:" + "b" * 64
    with pytest.raises(ValueError, match="preview-changed"):
        owner.configure(desired, apply=True, expected=preview["preview_token"])
    with pytest.raises(ValueError, match="confirmation-requires-apply"):
        owner.configure(desired, expected=preview["preview_token"])
    with pytest.raises(ValueError, match="invalid-local-job-enrollment"):
        owner.configure({**desired, "executable": "not-collection-authority"})
    with pytest.raises(ValueError, match="foreign-or-drifted"):
        owner._owned({**native, "configured": True}, None)


def test_installed_job_native_defaults_preserve_matching_but_not_changed_execution(owners):
    modules, _ = owners
    enrollment, jobs = modules["health_enrollment"], modules["health_jobs"]
    xml = jobs.installed_definition(
        slot=r"C:\fixture\versions\1.0", python=r"C:\fixture\versions\1.0\Scripts\python.exe",
        gui_python=r"C:\Python\pythonw.exe", identity="a" * 32 + ":" + "b" * 64,
        store=r"C:\fixture\store", policy_token="c" * 64,
        user_id="S-1-5-21-1-2-3-1000", windows_root=r"C:\Windows",
        owner="urn:copilot-extensions:health-job:" + "d" * 64, enabled=True,
    )
    root = ElementTree.fromstring(xml.replace("2000-01-01T00:00:15Z", "2000-01-01T00:00:15+00:00"))
    ns = {"t": jobs.NS}
    settings = root.find("t:Settings", ns)
    settings.remove(settings.find("t:Enabled", ns))
    normalized = ElementTree.tostring(root, encoding="unicode")
    assert enrollment.signature(normalized) == enrollment.signature(xml)
    assert enrollment.signature(enrollment.disabled(normalized))["Settings/Enabled"] == "false"
    disabled_xml = jobs.installed_definition(
        slot=r"C:\fixture\versions\1.0", python=r"C:\fixture\versions\1.0\Scripts\python.exe",
        gui_python=r"C:\Python\pythonw.exe", identity="a" * 32 + ":" + "b" * 64,
        store=r"C:\fixture\store", policy_token="c" * 64,
        user_id="S-1-5-21-1-2-3-1000", windows_root=r"C:\Windows",
        owner="urn:copilot-extensions:health-job:" + "d" * 64, enabled=False,
    )
    assert enrollment.signature(disabled_xml) == enrollment.signature(enrollment.disabled(xml))
    root.find("t:Actions/t:Exec/t:Command", ns).text = r"C:\Python\python.exe"
    changed = ElementTree.tostring(root, encoding="unicode")
    assert enrollment.signature(changed) != enrollment.signature(xml)
    owner = object.__new__(enrollment.Enrollment)
    owner.root = ROOT
    owner.owner = "urn:copilot-extensions:health-job:" + "d" * 64
    owner.selector = lambda *_args: SimpleNamespace(
        identity="selected-build", definition=lambda *_args, **_fields: xml,
    )
    owner._policy = owner.qualifier = lambda *_args: pytest.fail("unqualified cached action")
    with pytest.raises(ValueError, match="recovery-closure-changed"):
        owner._qualify_record({
            "version": "1.0", "identity": "selected-build", "definition": changed,
            "desired": {"enabled": True},
        }, {})


def test_installed_lifecycle_cli_uses_retained_manager_without_collector_readiness(owners, monkeypatch):
    modules, parser = owners
    enrollment = modules["health_enrollment"]
    selections = []
    monkeypatch.setattr(enrollment, "select", lambda **fields: selections.append(fields) or "manager")
    monkeypatch.setattr(enrollment, "Enrollment", lambda selected: SimpleNamespace(
        disable=lambda: {"status": "disabled"},
    ))
    args = parser.parse_args(["health", "job", "disable"])
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    assert args.func(args) == 0
    assert selections == [{"executing": True, "for_collection": False}]
    assert json.loads(output.getvalue())["status"] == "disabled"


def test_withdrawal_recovery_cannot_select_or_enable_an_incumbent(owners):
    module = owners[0]["health_enrollment"]
    owner = object.__new__(module.Enrollment)
    owner.root = ROOT
    owner.path = ROOT / "unused-state"
    owner.leases = SimpleNamespace(begin_runtime_reference_transaction=lambda *_args: {
        "token": "a" * 64, "owners": {},
    })
    writes, switches = [], []
    module.slots._write = lambda *args: writes.append(args)
    owner._qualify_record = lambda *_args: pytest.fail("withdrawal qualified an incumbent")
    owner._switch = lambda native, candidate: switches.append(candidate) or native
    owner._settle = lambda *_args, **fields: fields
    state = {"pending": {
        "before": {"retained": "incumbent"}, "candidate": {"retained": "replacement"},
        "references": [], "token": "a" * 64, "intent": "replace",
    }}
    result = owner._recover(state, {"configured": False}, rollback=True, remove=True)
    assert result["status"] == "removed" and switches == [None] and len(writes) == 1
    assert writes[0][1]["pending"]["intent"] == "remove"


def test_removed_enrollment_convergence_is_noop_after_reference_settlement(owners):
    module = owners[0]["health_enrollment"]
    owner = object.__new__(module.Enrollment)
    owner.root = ROOT
    owner.read = module.empty
    native = {"configured": False, "enabled": False, "instances": []}
    owner.backend = SimpleNamespace(call=lambda operation: native)
    owner._owned = owner._recover_reference_fence = lambda *_args: None
    owner.leases = SimpleNamespace(
        reference_transaction_custody=lambda *_args, **_fields: nullcontext(),
        read_runtime_references=lambda *_args: {},
    )
    assert owner.converge(remove=True)["changed"] is False


@pytest.mark.parametrize("outcome,expected", [("unsupported", 2), ("failed", 2), ("timeout", 124), ("cleanup", 125)])
def test_installed_job_support_and_result_propagation_use_substituted_native_owners(owners, monkeypatch, outcome, expected):
    modules, _ = owners
    job = modules["health_job"]
    peer = sys.modules[job.__package__ + "._peer_launch"]
    native = sys.modules["agent_procutil.owned_process"]

    class VirtualPath(type(Path())):
        def exists(self):
            return False

    monkeypatch.setattr(job, "Path", VirtualPath)
    monkeypatch.setattr(job, "sys", SimpleNamespace(
        platform="win32", prefix=str(ROOT / "versions" / "1.0" / ".venv"),
        executable=str(ROOT / "versions" / "1.0" / ".venv" / "Scripts" / "python.exe"),
    ))
    monkeypatch.setattr(job, "os", SimpleNamespace(environ={}))
    monkeypatch.setattr(job, "time", SimpleNamespace(monotonic=lambda: 100))
    monkeypatch.setattr(modules["health_slots"], "_read", lambda *_args, **_fields: ({
        "schema": "agent-machines.health.collection", "version": 1,
        "operations": ["preview", "apply"] + ([] if outcome == "unsupported" else ["installed-job"]),
    }, "source"))
    peer.retained_runtime_identity = lambda *_args: "selected-build"
    peer.retained_runtime_prefix = lambda *_args, **_fields: ["selected-runtime", "-m", "agent_machines"]
    peer.legacy_peer_environment = lambda environment: dict(environment)
    calls = []

    def run(argv, **fields):
        calls.append(argv)
        assert fields["budget"].deadline == 155
        assert argv[-3:] == ["--apply", "--expect-policy", "a" * 64]
        if outcome == "timeout":
            raise job.subprocess.TimeoutExpired(argv, 1)
        if outcome == "cleanup":
            raise native.OwnedCleanupError("unsettled")
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(native, "run_caller_owned", run)
    assert job.main(["--store", str(ROOT / "fixture-store"), "--expect-policy", "a" * 64]) == expected
    assert len(calls) == (0 if outcome == "unsupported" else 1)


def test_native_job_adapter_frames_exact_request_and_refuses_oversize_or_invalid_reply(owners, monkeypatch):
    import base64
    import re

    module = owners[0]["health_task_native"]
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(module, "os", SimpleNamespace(environ={"SystemRoot": str(ROOT / "Windows")}))
    calls = []
    snapshot = {"configured": False, "enabled": False, "instances": [], "digest": "absent"}

    def run(argv, **_fields):
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        encoded = re.search(r"FromBase64String\('([A-Za-z0-9+/=]+)'\)", script).group(1)
        calls.append(json.loads(base64.b64decode(encoded)))
        return SimpleNamespace(returncode=0, stdout=json.dumps(snapshot))

    monkeypatch.setattr(module, "run_caller_owned", run)
    task = module.NativeTask("ce-health-" + "a" * 32, "urn:copilot-extensions:health-job:" + "b" * 64)
    assert task.call("read") == snapshot
    assert calls == [{
        "name": task.name, "owner": task.owner, "operation": "read", "expected": "", "definition": "",
    }]
    with pytest.raises(ValueError, match="oversize"):
        task.call("create", expected="absent", definition="x" * 20000)
    assert len(calls) == 1
    monkeypatch.setattr(module, "run_caller_owned", lambda *_args, **_fields: SimpleNamespace(
        returncode=0, stdout="[]",
    ))
    with pytest.raises(RuntimeError, match="response-invalid"):
        task.call("read")


def test_gui_bootstrap_binds_only_its_matching_base_and_retained_environment(owners, monkeypatch):
    module = owners[0]["runtime_launcher"]

    class VirtualPath(PureWindowsPath):
        def samefile(self, other):
            return self == other

    home = VirtualPath(r"C:\Python")
    slot = VirtualPath(r"C:\fixture\versions\1.0")
    environment = slot / ".venv"
    python = environment / "Scripts" / "python.exe"
    context = SimpleNamespace(
        platform="win32", executable=str(home / "pythonw.exe"),
        base_prefix=str(home), prefix=str(home), exec_prefix=str(home),
    )
    monkeypatch.setattr(module, "Path", VirtualPath)
    monkeypatch.setattr(module, "sys", context)
    monkeypatch.setattr(module, "_identity_bytes", lambda *_args: (
        b"home = C:\\Python\ninclude-system-site-packages = false\n"
    ))
    runtime = SimpleNamespace(slot_python=lambda *_args: python)
    module._gui_environment(runtime, slot.parent.parent, slot)
    assert context.executable == str(python)
    assert context.prefix == context.exec_prefix == str(environment)
    context.executable = str(home / "python.exe")
    with pytest.raises(RuntimeError, match="does not match"):
        module._gui_environment(runtime, slot.parent.parent, slot)
    context.executable = str(home / "pythonw.exe")
    context.base_prefix = r"C:\DifferentPython"
    with pytest.raises(RuntimeError, match="does not match"):
        module._gui_environment(runtime, slot.parent.parent, slot)


def test_optional_desktop_admission_accepts_active_console_and_rdp_without_changing_capture(owners):
    modules, _ = owners
    require = modules["task_desktop"].require_observable_session
    facts = {
        "elevated": False, "station": "WinSta0", "station_visible": True,
        "thread_desktop": "Default", "input_desktop": "Default",
        "session": 2, "console_session": 1, "foreground_visible": True, "same_session": True,
    }
    for name in ("Console", "RDP-Tcp#1", "rdp-sxs123#0"):
        assert require(facts, station_name=name, connection_state=0, inherited_job=True) is facts
    for changed, state, contained in (
        ({}, 4, True), ({"same_session": False}, 0, True),
        ({"foreground_visible": False}, 0, True), ({"input_desktop": "Winlogon"}, 0, True),
        ({}, 0, False),
    ):
        with pytest.raises(ValueError, match="optional-desktop-unavailable"):
            require(
                {**facts, **changed}, station_name="RDP-Tcp#1",
                connection_state=state, inherited_job=contained,
            )
    classify = modules["task_desktop"].task_owned_visual_violations
    incomplete = {
        "passed": False, "violations": [{"kind": "sampled-foreground", "identity": [11, 22, 33]}],
    }
    for kind in (
        "sampled-foreground", "foreground-capture-changed",
        "foreground-enumeration-identity-mismatch",
    ):
        changed = {"passed": False, "violations": [{"kind": kind, "identity": [11, 22, 33]}]}
        assert classify(changed, {(11, 33)}, [])
    assert classify({
        "passed": False, "violations": [{
            "kind": "foreground-capture-changed", "identity": [12, 22, 34],
            "previous_identity": [11, 22, 33],
        }],
    }, {(11, 33)}, [])
    assert not classify(incomplete, {(11, 34)}, [])
    assert not classify(incomplete, {(12, 33)}, [])
    assert classify({"passed": False}, set(), [{"kind": "task-visible-window"}])
    pin = SimpleNamespace(pid=11, birth=33, gone=lambda: False)
    closed = []
    pin.close = lambda: closed.append(True)
    api = SimpleNamespace(
        pin=lambda pid: pin, user=SimpleNamespace(IsWindowVisible=lambda hwnd: True),
        window_identity=lambda hwnd: (11, 22),
    )
    observe = modules["task_desktop"].confirm_visible_task_window
    assert observe(api, {99: (11, 22)}, {(11, 33)})["kind"] == "task-visible-window"
    assert closed == [True]
    assert observe(api, {99: (11, 22)}, {(11, 34)}) is None
    assert closed == [True, True]


def test_native_job_preparation_is_actor_free_and_inert_on_both_platforms(owners):
    modules, _ = owners
    jobs = modules["health_jobs"]
    token = "a" * 64
    windows = jobs.prepare(
        platform="win32", payload=r"C:\Example Tools\payload", store=r"C:\Example Data\store",
        policy_token=token, user_id="S-1-5-21-111-222-333-1001", windows_root=r"C:\Windows",
        gui_python=r"C:\Example Python\pythonw.exe",
        runtime_site=r"C:\Example Runtime\Lib\site-packages",
    )
    root = ElementTree.fromstring(windows["artifacts"]["agent-machines-health.xml"])
    nodes = {"t": jobs.NS}
    assert root.find("t:Settings/t:Enabled", nodes).text == "false"
    assert root.find("t:Principals/t:Principal/t:LogonType", nodes).text == "InteractiveToken"
    assert root.find("t:Principals/t:Principal/t:RunLevel", nodes).text == "LeastPrivilege"
    assert root.find("t:Settings/t:MultipleInstancesPolicy", nodes).text == "IgnoreNew"
    assert root.find("t:Settings/t:ExecutionTimeLimit", nodes).text == "PT1M"
    arguments = root.find("t:Actions/t:Exec/t:Arguments", nodes).text
    assert root.find("t:Actions/t:Exec/t:Command", nodes).text == r"C:\Example Python\pythonw.exe"
    assert '"C:\\Example Tools\\payload\\scripts\\health-job-entry.py"' in arguments
    assert '--store "C:\\Example Data\\store"' in arguments
    assert f"--expect-policy {token}" in arguments
    assert '--site "C:\\Example Runtime\\Lib\\site-packages"' in arguments
    linux = jobs.prepare(
        platform="linux", payload="/example/payload $one%", store="/example/store",
        policy_token=token,
    )
    assert '"/example/payload $$one%%/bin/agent-machines"' in linux["artifacts"]["agent-machines-health.service"]
    assert "WorkingDirectory=/example/payload $one%%\n" in linux["artifacts"]["agent-machines-health.service"]
    assert "TimeoutStartSec=55s\nTimeoutStopSec=5s\n" in linux["artifacts"]["agent-machines-health.service"]
    assert "OnCalendar=*-*-* *:00:15 UTC\nPersistent=true" in linux["artifacts"]["agent-machines-health.timer"]
    for result in (windows, linux):
        assert not result["registered"] and not result["enabled"]
        assert result["qualification"] == "native-scheduler-not-qualified"


def test_native_job_preparation_refuses_unsupported_platforms_and_unsafe_definition_values(owners):
    modules, _ = owners
    jobs = modules["health_jobs"]
    fields = dict(platform="linux", payload="/example/payload", store="/example/store", policy_token="a" * 64)
    for change in (
        {"platform": "darwin"}, {"payload": "relative"}, {"store": "/example/\nstore"},
        {"policy_token": "none"}, {"user_id": "S-1-5-18"}, {"windows_root": "/wrong"},
    ):
        with pytest.raises(ValueError):
            jobs.prepare(**(fields | change))


def test_native_job_preparation_preserves_the_original_path_for_owner_validation(owners, monkeypatch, capsys):
    modules, parser = owners
    requested = Path("/example/link/../store")

    def policy_preview(store):
        assert store == requested
        raise modules["health_slots"].HealthError("absolute-plain-path-required")

    monkeypatch.setattr(modules["health_run"], "policy_preview", policy_preview)
    args = parser.parse_args(["health", "prepare-job", "--store-dir", str(requested)])
    assert args.func(args) == 2
    result = json.loads(capsys.readouterr().err)
    assert result["error"] == "absolute-plain-path-required"
    assert not result["registered"] and not result["enabled"]


def view(slots):
    record = slots.validate_record({
        "schema": slots.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": STAMP // slots.HOUR_MS,
        "revision": 1, "parent": None, "observed_at_ms": STAMP,
        "domains": {"local_listener": {
            "state": "ok", "evidence": "listener-check", "reason": "none",
            "observed_at_ms": STAMP - 2 * slots.HOUR_MS,
        }},
    })
    hours = [
        {"hour": hour, "state": "missing"}
        for hour in range(record["hour"] - 167, record["hour"] + 1)
    ]
    hours[-1].update(state="observed", sample=slots._sample(record, STAMP))
    hours[-2]["state"] = "invalid"
    return {
        "schema": "agent-machines.health.view", "version": 1, "generated_at_ms": STAMP,
        "publisher": record["publisher"], "continuity": record["continuity"],
        "hours": hours, "observed_hours": 1, "view": "last-known",
        "probe_execution": "not-performed", "carrier_receipt": "not-observed",
        "observation_qualification": "input-supplied-not-verified",
    }


def test_normal_report_composes_actual_age_projection_without_promoting_health_to_events(
    owners, monkeypatch, capsys,
):
    modules, parser = owners
    slots = modules["health_slots"]
    expected = view(slots)
    selected = Path("explicit-local-store")
    calls = []

    def inspect(store, *, now_ms):
        calls.append((store, now_ms))
        return expected

    monkeypatch.setattr(slots, "inspect", inspect)
    args = parser.parse_args(["health", "report", "--store-dir", str(selected), "--now", NOW.isoformat()])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [(selected, STAMP)]
    assert result["health"] == {"state": "available", "view": expected}
    assert result["coverage"]["accepted_records"] == 0
    assert all(hour["state"] == "no-observation" for hour in result["hours"])
    sample = result["health"]["view"]["hours"][-1]["sample"]
    assert sample["domains"]["local_listener"]["state"] == "stale"
    assert sample["domains"]["local_listener"]["observed_state"] == "ok"
    assert sample["domains"]["ssh_authenticated"]["direction"] is None
    assert sample["domains"]["logical_agents"]["count"] is None
    chart = modules["health_chart"].render(result)
    ElementTree.fromstring(chart)
    assert "1/168 observed slots" in chart and "input-supplied-not-verified" in chart
    assert "observed ok; source age ms 7200000" in chart
    assert "slot invalid" in chart and "slot missing" in chart
    assert "<script" not in chart and "href=" not in chart


@pytest.mark.parametrize("failure", ["invalid", "unavailable"])
def test_store_failure_is_a_visible_gap_without_suppressing_independent_service_report(
    owners, monkeypatch, capsys, failure,
):
    modules, parser = owners
    slots = modules["health_slots"]

    def inspect(*args, **kwargs):
        raise slots.HealthError("private-input") if failure == "invalid" else OSError("private-path")

    monkeypatch.setattr(slots, "inspect", inspect)
    args = parser.parse_args([
        "health", "report", "--store-dir", "explicit-store", "--service", "agent-dispatch",
        "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 0
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result["health"] == {"state": "unavailable", "reason": "local-store-unavailable"}
    assert result["services"][0]["state"] == "unsupported"
    assert "private" not in raw
    chart = modules["health_chart"].render(result)
    ElementTree.fromstring(chart)
    assert "Local health store unavailable" in chart
    assert "agent-dispatch/agent-dispatch" in chart


def test_old_report_selection_is_unchanged_and_unknown_new_syntax_is_rejected(owners, capsys):
    modules, parser = owners
    args = parser.parse_args(["health", "report", "--service", "agent-dispatch", "--now", NOW.isoformat()])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert "health" not in result and result["services"][0]["state"] == "unsupported"
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["health", "report", "--store-directory", "not-supported"])
    assert error.value.code == 2
    capsys.readouterr()
    args = parser.parse_args([
        "health", "report", "--service", "agent-dispatch", "--hour", "", "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 2
    assert not capsys.readouterr().out


def test_chart_inside_selected_store_refuses_before_reading_or_writing(owners, monkeypatch, capsys):
    _modules, parser = owners
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    args = parser.parse_args([
        "health", "report", "--store-dir", "store", "--chart", str(Path("store") / "new.svg"),
        "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "health report unavailable" in captured.err


def test_empty_health_history_is_visible_without_fabricating_domain_rows(owners, monkeypatch, capsys):
    modules, parser = owners
    slots = modules["health_slots"]
    empty = view(slots)
    empty["hours"] = [{"hour": hour["hour"], "state": "missing"} for hour in empty["hours"]]
    empty["observed_hours"] = 0
    monkeypatch.setattr(slots, "inspect", lambda *args, **kwargs: empty)
    args = parser.parse_args(["health", "report", "--store-dir", "explicit-store", "--now", NOW.isoformat()])
    assert args.func(args) == 0
    result = json.loads(capsys.readouterr().out)
    chart = modules["health_chart"].render(result)
    ElementTree.fromstring(chart)
    assert "0/168 observed slots" in chart and "slot missing" in chart
    assert "local listener</text>" not in chart
    assert "observed ok;" not in chart


@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
def test_unresolvable_store_preserves_independent_json_and_external_chart(
    owners, monkeypatch, capsys, error_type,
):
    modules, parser = owners
    store = Path("unresolvable-store")
    output = io.StringIO()

    class Chart:
        def __fspath__(self):
            return "external.svg"

        def resolve(self):
            return Path("external.svg")

        def open(self, mode, encoding):
            assert mode == "x" and encoding == "utf-8"
            return nullcontext(output)

    def resolve(path):
        assert path == store
        raise error_type("private-resolution-detail")

    def inspect(*args, **kwargs):
        raise modules["health_slots"].HealthError("path-unavailable")

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(modules["health_slots"], "inspect", inspect)
    args = parser.parse_args([
        "health", "report", "--store-dir", str(store), "--service", "agent-dispatch",
        "--now", NOW.isoformat(),
    ])
    args.chart = Chart()
    assert args.func(args) == 0
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result["health"] == {"state": "unavailable", "reason": "local-store-unavailable"}
    assert result["services"][0]["state"] == "unsupported"
    assert "private" not in raw
    assert "Local health store unavailable" in output.getvalue()
    ElementTree.fromstring(output.getvalue())


def test_unresolvable_chart_is_a_bounded_output_refusal(owners, monkeypatch, capsys):
    _modules, parser = owners

    def resolve(path):
        raise RuntimeError("private-chart-detail")

    monkeypatch.setattr(Path, "resolve", resolve)
    args = parser.parse_args([
        "health", "report", "--store-dir", "store", "--chart", "external.svg",
        "--now", NOW.isoformat(),
    ])
    assert args.func(args) == 2
    captured = capsys.readouterr()
    assert not captured.out and "private" not in captured.err


def test_closed_hourly_envelope_retains_service_namespace_and_unknown_health(owners):
    modules, _parser = owners
    slots, hourly = modules["health_slots"], modules["health_hourly"]
    record = hourly.validate_record({
        "schema": hourly.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": STAMP // slots.HOUR_MS - 1,
        "revision": 1, "parent": None, "observed_at_ms": STAMP, "observation": None,
        "services": [{
            "instance": "queue", "service": "agent-dispatch",
            "hour": "2026-11-09T11:00:00Z", "state": "unsupported",
        }],
        "coverage": {"observation": "not-requested", "services": "collected"},
    })
    projected = hourly.sample(record, STAMP)
    assert projected["services"] == record["services"]
    assert projected["domains"]["local_listener"]["state"] == "unknown"
    assert projected["domains"]["logical_agents"]["count"] is None
    with pytest.raises(slots.HealthError, match="invalid-fields"):
        slots.validate_record(record)
    policy = {
        "collection": {"bridge_port": None, "services": ["queue=agent-dispatch"]},
    }
    hourly.validate_replacement(record, None, policy)
    with pytest.raises(slots.HealthError, match="service-replacement-refused"):
        hourly.validate_replacement({**record, "services": []}, record, policy)
    protocol = sys.modules["process_telemetry.hourly"]
    source_stamp = record["hour"] * slots.HOUR_MS
    contribution = {
        "schema": protocol.SCHEMA, "version": 1, "service": "agent-dispatch",
        "hour": "2026-11-09T11:00:00Z", "generated_at_ms": STAMP,
        "source": {"first_ms": source_stamp, "last_ms": source_stamp, "continuity": None},
        "coverage": {
            **dict.fromkeys(protocol.COUNTERS, 0), "files": 1, "records": 1, "observations": 1,
            "state": "observed", "reason": "retained-events", "scan_limited": False,
            "expected_events": None, "producer_loss": "unknown",
        },
        "groups": [{
            "metric": "task-lifecycle", "stage": "lifecycle", "unit": "count",
            "aggregation": "count", "scope": "observed-events",
            "observations": 1, "outcomes": {"completed": 1},
        }],
        "health": [], "diagnostics": [],
    }
    accepted = hourly.validate_record({**record, "services": [{
        **record["services"][0], "state": "observed", "contribution": contribution,
    }]})
    for state in ("unavailable", "unsupported", "partial"):
        empty = {
            **contribution, "source": {"first_ms": None, "last_ms": None, "continuity": None},
            "groups": [], "coverage": {
                **contribution["coverage"], **dict.fromkeys(protocol.COUNTERS, 0),
                "state": state, "reason": "capture-unsupported" if state == "unsupported" else "capture-unavailable",
            },
        }
        candidate = hourly.validate_record({
            **accepted, "revision": 2, "parent": 1, "services": [{
                **accepted["services"][0], "state": state, "contribution": empty,
            }],
        })
        assert hourly.has_service_evidence(accepted["services"][0])
        assert not hourly.has_service_evidence(candidate["services"][0])
        with pytest.raises(slots.HealthError, match="service-replacement-refused"):
            hourly.validate_replacement(candidate, accepted, policy)
    chart_view = view(slots)
    chart_view["hours"][-2] = {"hour": record["hour"], "state": "observed", "sample": projected}
    report = modules["health_report"].HourlyReport(NOW).result(0)
    report["health"] = {"state": "available", "view": chart_view}
    chart = modules["health_chart"].render(report)
    ElementTree.fromstring(chart)
    assert "queue/agent-dispatch: unsupported" in chart
    assert all(hour["state"] == "no-observation" for hour in report["hours"])


def test_listener_only_replacement_cannot_change_independent_domain_evidence(owners):
    modules, _parser = owners
    slots, hourly = modules["health_slots"], modules["health_hourly"]
    observation = {
        "schema": slots.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": STAMP // slots.HOUR_MS,
        "revision": 1, "parent": None, "observed_at_ms": STAMP,
        "domains": {"logical_agents": {
            "state": "ok", "evidence": "session-owner", "reason": "none",
            "observed_at_ms": STAMP - 1000, "count": 2,
        }},
    }
    original = hourly.validate_record({
        "schema": hourly.SCHEMA, "version": 1, "publisher": observation["publisher"],
        "continuity": observation["continuity"], "hour": observation["hour"],
        "revision": 1, "parent": None, "observed_at_ms": STAMP,
        "observation": observation, "services": [],
        "coverage": {"observation": "retained", "services": "not-requested"},
    })
    policy = {"collection": {"bridge_port": 1234, "services": []}}
    candidate = copy.deepcopy(original)
    candidate.update(revision=2, parent=1)
    candidate["coverage"]["observation"] = "collected"
    candidate["observation"]["domains"]["local_listener"] = {
        "state": "ok", "evidence": "listener-check", "reason": "none", "observed_at_ms": STAMP,
    }
    hourly.validate_replacement(hourly.validate_record(candidate), original, policy)
    for mutation in ("erased", "refreshed", "defaulted"):
        forged = copy.deepcopy(candidate)
        domains = forged["observation"]["domains"]
        if mutation == "erased":
            del domains["logical_agents"]
        elif mutation == "refreshed":
            domains["logical_agents"]["observed_at_ms"] = STAMP
        else:
            domains["logical_agents"] = {
                "state": "unknown", "evidence": "not-observed", "reason": "not-observed",
                "observed_at_ms": None, "count": None,
            }
        with pytest.raises(slots.HealthError, match="observation-replacement-refused"):
            hourly.validate_replacement(hourly.validate_record(forged), original, policy)


@pytest.mark.parametrize("mutation", ["owner", "hour", "field", "service"])
def test_hourly_envelope_refuses_mismatched_or_unknown_components(owners, mutation):
    modules, _parser = owners
    slots, hourly = modules["health_slots"], modules["health_hourly"]
    observation = {
        "schema": slots.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": STAMP // slots.HOUR_MS,
        "revision": 1, "parent": None, "observed_at_ms": STAMP, "domains": {},
    }
    record = {
        "schema": hourly.SCHEMA, "version": 1, "publisher": "example-worker",
        "continuity": "example-generation", "hour": observation["hour"],
        "revision": 1, "parent": None, "observed_at_ms": STAMP, "observation": observation,
        "services": [], "coverage": {"observation": "collected", "services": "not-requested"},
    }
    if mutation == "owner":
        observation["publisher"] = "another-worker"
    elif mutation == "hour":
        record["hour"] -= 1
    elif mutation == "field":
        record["private-path"] = "not-allowed"
    else:
        record["services"] = [{
            "instance": "queue", "service": "agent-dispatch",
            "hour": "2026-11-09T12:00:00Z", "state": "observed",
        }]
    with pytest.raises(slots.HealthError):
        hourly.validate_record(record)


def test_explicit_collection_orchestration_keeps_hours_and_parent_budget(owners, monkeypatch):
    modules, _parser = owners
    collector, slots = modules["health_collect"], modules["health_slots"]
    monkeypatch.setattr(collector, "time", SimpleNamespace(
        monotonic=lambda: 10.0, time_ns=lambda: STAMP * 1_000_000,
    ))
    monkeypatch.setattr(collector, "admission", lambda store: nullcontext(lambda: None))
    monkeypatch.setattr(slots, "_plain", lambda path, **fields: path)
    policy = {
        "schema": slots.POLICY_SCHEMA, "version": 2, "publisher": "example-worker",
        "continuity": "example-generation",
        "collection": {"bridge_port": None, "services": ["agent-dispatch"]},
    }
    monkeypatch.setattr(slots, "_read", lambda path, validator, **fields: (
        (validator(policy), "policy-revision") if path.name == "policy.json" else (None, None)
    ))
    services = sys.modules[collector.__package__ + ".service_reports"]
    calls = []

    def collect(names, hour, **fields):
        calls.append(fields)
        return [{"instance": "agent-dispatch", "service": "agent-dispatch", "hour": hour, "state": "unsupported"}]

    monkeypatch.setattr(services, "collect", collect)
    historical = collector.collect(Path("synthetic-store"), "2026-11-09T11:00:00Z")
    assert historical["observation"] is None
    assert historical["services"][0]["hour"] == "2026-11-09T11:00:00Z"
    assert historical["services"][0]["state"] == "unsupported"
    assert calls == [{"now_ms": STAMP, "deadline": 54.75}]
    assert historical["coverage"] == {"observation": "not-requested", "services": "collected"}
    current = collector.collect(Path("synthetic-store"))
    assert current["services"] == [] and current["hour"] == STAMP // slots.HOUR_MS
    with pytest.raises(slots.HealthError, match="collection-hour-outside-window"):
        collector.collect(Path("synthetic-store"), "2026-11-09T13:00:00Z")
    with pytest.raises(ValueError, match="canonical UTC hour"):
        collector.collect(Path("synthetic-store"), "")
    tick = 10.0

    @contextmanager
    def admission(store):
        nonlocal tick
        yield lambda: None
        tick = 60.0

    monkeypatch.setattr(collector, "admission", admission)
    monkeypatch.setattr(collector.time, "monotonic", lambda: tick)
    with pytest.raises(slots.HealthError, match="collection-timeout"):
        collector.collect(Path("synthetic-store"), "2026-11-09T11:00:00Z")


@pytest.fixture
def one_shot(owners, monkeypatch):
    modules, parser = owners
    slots, runner, collector = (modules[name] for name in ("health_slots", "health_run", "health_collect"))
    policy = {
        "schema": slots.POLICY_SCHEMA, "version": 2, "publisher": "example-worker",
        "continuity": "example-generation",
        "collection": {"bridge_port": None, "services": ["agent-dispatch"]},
    }
    files = {"policy.json": policy}
    calls = []
    clock = SimpleNamespace(monotonic=lambda: 10.0, time_ns=lambda: STAMP * 1_000_000)
    for module in (slots, runner, collector):
        monkeypatch.setattr(module, "time", clock)

    class Store:
        def __init__(self, name="store"):
            self.name = name

        def __truediv__(self, name):
            return Store(name)

        def __str__(self):
            return self.name

        def stat(self):
            return SimpleNamespace(st_dev=1, st_ino=2)

    def read(path, validator, **fields):
        value = copy.deepcopy(files.get(path.name))
        return (None, None) if value is None else (validator(value), slots._digest(value))

    def write(path, value):
        calls.append(("write", value["hour"]))
        files[path.name] = copy.deepcopy(value)

    def admission(store, **fields):
        calls.append(("admission",))
        return nullcontext(lambda: calls.append(("fence",)))

    monkeypatch.setattr(slots, "_plain", lambda path, **fields: path)
    monkeypatch.setattr(slots, "_read", read)
    monkeypatch.setattr(slots, "_write", write)
    monkeypatch.setattr(slots, "_sync_directory", lambda path: None)
    monkeypatch.setattr(slots, "mutation_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(collector, "admission", admission)
    native = ModuleType("agent_procutil.owned_process")
    native.OwnedCleanupError = type("OwnedCleanupError", (RuntimeError,), {})
    monkeypatch.setitem(sys.modules, native.__name__, native)
    return modules, Store(), files, calls, parser


def test_one_shot_policy_preview_and_approval_are_actor_free(one_shot, monkeypatch):
    modules, store, files, calls, _parser = one_shot
    runner, slots = modules["health_run"], modules["health_slots"]
    plan = runner.run(store)
    assert plan["status"] == "preview" and calls == []
    token = plan["policy_token"]
    monkeypatch.setattr(runner.time, "time_ns", lambda: (STAMP + slots.HOUR_MS) * 1_000_000)
    assert runner.run(store)["policy_token"] == token
    for expected in ("", "0" * 64):
        with pytest.raises(slots.HealthError):
            runner.run(store, apply=True, expected=expected)
    files["policy.json"]["collection"]["services"] = []
    with pytest.raises(slots.HealthError, match="store-policy-changed"):
        runner.run(store, apply=True, expected=token)
    assert calls == []


def test_one_shot_actor_free_apply_uses_real_planner_and_writer_without_candidate_files(one_shot):
    modules, store, files, calls, _parser = one_shot
    runner, slots = modules["health_run"], modules["health_slots"]
    hour = STAMP // slots.HOUR_MS - 1
    applied = runner.run(store, apply=True, expected=runner.run(store)["policy_token"])
    assert applied["status"] == "completed"
    assert applied["targets"][0]["collection"] == "not-requested"
    assert applied["targets"][1]["transaction"] == "recorded"
    assert applied["targets"][1]["applied"] and applied["targets"][1]["changed"]
    assert calls.count(("admission",)) == 1
    assert calls.count(("write", hour)) == 1
    assert set(files) == {"policy.json", f"{hour % slots.SLOTS:03d}.json"}
    assert files[f"{hour % slots.SLOTS:03d}.json"]["observation"] is None


def test_one_shot_actor_free_commit_guard_refuses_policy_drift(one_shot, monkeypatch):
    modules, store, files, _calls, _parser = one_shot
    slots, runner = modules["health_slots"], modules["health_run"]

    @contextmanager
    def drift(*a, **k):
        files["policy.json"]["continuity"] = "changed"
        yield

    monkeypatch.setattr(slots, "mutation_lock", drift)
    result = runner.run(store, apply=True, expected=runner.run(store)["policy_token"])
    assert result["status"] == "failed"
    assert not result["targets"][1]["applied"]
    assert set(files) == {"policy.json"}


def test_one_shot_actor_free_partial_late_write_retains_actual_commit(one_shot, monkeypatch):
    modules, store, files, calls, _parser = one_shot
    slots, runner = modules["health_slots"], modules["health_run"]
    write = slots._write
    tick = 10.0
    monkeypatch.setattr(runner.time, "monotonic", lambda: tick)

    def late(*args):
        nonlocal tick
        write(*args)
        tick = 60.0

    monkeypatch.setattr(slots, "_write", late)
    result = runner.run(store, apply=True, expected=runner.run(store)["policy_token"])
    assert result["status"] == "partial"
    assert result["qualification"] == "collection-timeout"
    assert result["targets"][1]["transaction"] == "recorded"
    assert result["targets"][1]["applied"] and result["targets"][1]["changed"]
    assert len(files) == 2 and len([call for call in calls if call[0] == "write"]) == 1


def test_one_shot_actor_free_interruption_retains_unknown_transaction(one_shot, monkeypatch):
    modules, store, files, _calls, _parser = one_shot
    slots, runner = modules["health_slots"], modules["health_run"]
    write = slots._write

    def interrupted(*args):
        write(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(slots, "_write", interrupted)
    with pytest.raises(KeyboardInterrupt) as caught:
        runner.run(store, apply=True, expected=runner.run(store)["policy_token"])
    result = caught.value.health_run_result
    assert result["qualification"] == "collection-interrupted"
    assert result["targets"][1]["transaction"] == "unknown"
    assert result["targets"][1]["applied"] is result["targets"][1]["changed"] is None
    assert len(files) == 2


def test_one_shot_actor_free_cleanup_preserves_original_exception_and_partial_result(one_shot, monkeypatch):
    modules, store, _files, _calls, _parser = one_shot
    runner, collector = modules["health_run"], modules["health_collect"]
    owner_type = sys.modules["agent_procutil.owned_process"].OwnedCleanupError

    def collect(*args, **kwargs):
        raise owner_type("fixture cleanup")

    monkeypatch.setattr(sys.modules[collector.__package__ + ".service_reports"], "collect", collect)
    with pytest.raises(owner_type) as caught:
        runner.run(store, apply=True, expected=runner.run(store)["policy_token"])
    result = caught.value.health_run_result
    assert result["qualification"] == "collection-cleanup-unconfirmed"
    assert result["targets"][1]["collection"] == "failed"
    assert not result["targets"][1]["applied"]


def test_one_shot_actor_free_final_interruption_retains_committed_facts(one_shot, monkeypatch):
    modules, store, files, _calls, _parser = one_shot
    runner, collector = modules["health_run"], modules["health_collect"]
    preview = runner.policy_preview
    after_admission = False

    @contextmanager
    def admission(*args, **kwargs):
        nonlocal after_admission
        yield lambda: None
        after_admission = True

    def final_check(*args):
        if after_admission:
            raise KeyboardInterrupt
        return preview(*args)

    monkeypatch.setattr(collector, "admission", admission)
    monkeypatch.setattr(runner, "policy_preview", final_check)
    with pytest.raises(KeyboardInterrupt) as caught:
        runner.run(store, apply=True, expected=runner.run(store)["policy_token"])
    result = caught.value.health_run_result
    assert result["qualification"] == "collection-interrupted"
    assert result["targets"][1]["transaction"] == "recorded"
    assert result["targets"][1]["applied"] and result["targets"][1]["changed"]
    assert len(files) == 2
