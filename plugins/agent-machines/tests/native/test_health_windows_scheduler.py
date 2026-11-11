"""Explicit native scheduler effects, never an actor-free or default CI packet."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import uuid
import venv
from xml.etree import ElementTree as ET

import pytest

from agent_machines import health_jobs, health_run, health_slots
from agent_procutil import no_window_kwargs, windowless_python
from test_health_observe_consumer import QUALIFYING_BODY, listener, response

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("host-state"),
    pytest.mark.effect("process"),
    pytest.mark.effect("network"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("agent_machines.health_windows_scheduler"),
    pytest.mark.skipif(
        sys.platform != "win32" or os.environ.get("AGENT_MACHINES_NATIVE_SCHEDULER") != "1",
        reason="explicit limited-user Windows scheduler fixture approval required",
    ),
]
REPO = Path(__file__).resolve().parents[4]
PLUGIN = REPO / "plugins" / "agent-machines"
OWNER = Path(__file__).with_name("health_task_owner.ps1")
NS = {"t": health_jobs.NS}


def _ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def _control(operation, name="", definition=None):
    host = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(host), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(OWNER), "-Operation", operation]
    if name:
        command += ["-Name", name]
    if definition is not None:
        command += ["-Definition", str(definition)]
    child = subprocess.run(
        command, cwd=OWNER.parent, capture_output=True, text=True, encoding="utf-8", timeout=20,
        **no_window_kwargs(),
    )
    assert child.returncode == 0, (operation, child.stdout, child.stderr)
    return json.loads(child.stdout)


def _until(predicate, seconds, description):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError(f"Native fixture deadline: {description}")


@contextmanager
def _task(tmp_path, definition):
    name = "ce-health-fixture-" + uuid.uuid4().hex
    uri = ET.Element(f"{{{health_jobs.NS}}}RegistrationInfo")
    ET.SubElement(uri, f"{{{health_jobs.NS}}}Description").text = (
        "urn:copilot-extensions:health-fixture:" + name
    )
    definition.insert(0, uri)
    path = tmp_path / f"{name}.xml"
    path.write_text(ET.tostring(definition, encoding="unicode"), encoding="utf-8")
    attempted = False
    try:
        # Record custody before the registration call, including an unknown reply.
        attempted = True
        registered = _control("register", name, path)
        yield name, registered
    finally:
        if attempted:
            receipt = _control("remove", name)
            (tmp_path / f"{name}-cleanup.json").write_text(json.dumps(receipt), encoding="utf-8")
            assert receipt["removed"] and receipt["instances"] == 0


def _payload(tmp_path, *, delay_seconds=0):
    runtime = tmp_path / "selected-runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / "Scripts/python.exe"
    site = runtime / "Lib/site-packages"
    shutil.copytree(PLUGIN / "src/agent_machines", site / "agent_machines")
    shutil.copytree(REPO / "libs/agent-procutil/src/agent_procutil", site / "agent_procutil")
    (site / "fixture-dependencies.pth").write_text(
        "import sys; sys.path.extend(" + ascii([
            str(Path(p).resolve()) for p in sys.path if p and Path(p).is_dir()
        ]) + ")\n", encoding="ascii",
    )
    (site / "fixture_console_probe.py").write_text(
        "import ctypes, json, os\nfrom ctypes import wintypes\n"
        "kernel = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "kernel.GetConsoleWindow.restype = wintypes.HWND\n"
        "kernel.GetCurrentProcess.restype = wintypes.HANDLE\n"
        "kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4\n"
        "kernel.GetProcessTimes.restype = wintypes.BOOL\n"
        "times = [wintypes.FILETIME() for _ in range(4)]\n"
        "if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(t) for t in times)):\n"
        "    raise ctypes.WinError(ctypes.get_last_error())\n"
        "birth = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime\n"
        f"with open({str(tmp_path / 'console.jsonl')!r}, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'pid': os.getpid(), 'birth': birth, "
        "'console_window': int(kernel.GetConsoleWindow() or 0)}) + '\\n')\n",
        encoding="utf-8",
    )
    (site / "fixture-console-probe.pth").write_text("import fixture_console_probe\n", encoding="utf-8")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "plugin.json").write_text('{"name":"agent-machines"}', encoding="utf-8")
    for relative in (
        "bin/agent-machines.ps1", "scripts/invoke-payload-runtime.ps1",
        "scripts/health-collection-entry.py", "scripts/health-job-entry.py",
    ):
        shutil.copyfile(PLUGIN / relative, payload / relative)
    # Replace only the fixture's installation selectors, not production dispatch.
    (scripts / "installation-context/installation-context.ps1").write_text(
        """[Console]::Out.WriteLine('{"status":"ready","reason":"policy-default-false","""
        """"actualMode":"legacy","desiredMode":"legacy"}')\nexit 0\n""",
        encoding="utf-8-sig",
    )
    home = tmp_path / "home"
    home.mkdir()
    (scripts / "resolve-runtime.ps1").write_text(
        "param([string]$TraceMode)\n"
        "if ($TraceMode -cne 'read-only') { throw 'read-only selection required' }\n"
        "$self = [Diagnostics.Process]::GetCurrentProcess()\n"
        "$identity = @{ pid = $PID; birth = $self.StartTime.ToUniversalTime().ToFileTimeUtc(); "
        "session = $self.SessionId }\n"
        f"$identity | ConvertTo-Json -Compress | Add-Content -LiteralPath {_ps(tmp_path / 'resolver.jsonl')}\n"
        f"Start-Sleep -Seconds {delay_seconds}\n"
        "$env:COPILOT_EXTENSIONS_CONTEXT = $null\n"
        f"$env:USERPROFILE = {_ps(home)}\n$env:HOME = {_ps(home)}\n"
        f"$AgentRtPy = {_ps(python)}\n",
        encoding="utf-8-sig",
    )
    return payload


def _definition(payload, store, sid, *, enabled, calendar=True):
    prepared = health_jobs.prepare(
        platform="win32", payload=str(payload), store=str(store),
        policy_token=health_run.policy_preview(store)[2], user_id=sid,
        windows_root=os.environ["SystemRoot"],
        gui_python=windowless_python(
            payload.parent / "selected-runtime/Scripts/python.exe", require_windowless=True,
        ),
        runtime_site=str(payload.parent / "selected-runtime/Lib/site-packages"),
    )
    root = ET.fromstring(prepared["artifacts"]["agent-machines-health.xml"])
    now = datetime.now(timezone.utc)
    trigger = root.find("t:Triggers/t:TimeTrigger", NS)
    trigger.find("t:StartBoundary", NS).text = (
        now + timedelta(seconds=12)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Native Windows repetition has a one-minute minimum; this is test cadence.
    trigger.find("t:Repetition/t:Interval", NS).text = "PT1M"
    end = ET.Element(f"{{{health_jobs.NS}}}EndBoundary")
    end.text = (
        now + timedelta(minutes=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    trigger.insert(list(trigger).index(trigger.find("t:Enabled", NS)), end)
    settings = root.find("t:Settings", NS)
    ET.SubElement(settings, f"{{{health_jobs.NS}}}DeleteExpiredTaskAfter").text = "PT1M"
    for node in (trigger.find("t:Enabled", NS), settings.find("t:Enabled", NS)):
        node.text = "true" if enabled else "false"
    if not calendar:
        trigger.find("t:Enabled", NS).text = "false"
    return root


def _assert_gui_entry(executable):
    with Path(executable).open("rb") as stream:
        dos = stream.read(64)
        assert dos[:2] == b"MZ"
        offset = struct.unpack_from("<I", dos, 60)[0]
        assert 0 < offset < 1024 * 1024
        stream.seek(offset)
        header = stream.read(94)
    assert header[:4] == b"PE\0\0"
    assert struct.unpack_from("<H", header, 24)[0] in (0x10B, 0x20B)
    assert struct.unpack_from("<H", header, 24 + 68)[0] == 2


def _exercise_cycles(tmp_path, observation, *, observed_session=None):
    identity = _control("identity")
    assert identity["interactive"]
    if observed_session is not None and identity["session"] != observed_session:
        from sdk_desktop_observer import ObservationBlocked

        raise ObservationBlocked("optional desktop/controller session mismatch")
    payload = _payload(tmp_path)
    if observed_session is not None:
        observation.api.prepare_task_observation(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    unrelated = tmp_path / "unrelated-data"
    unrelated.write_bytes(b"fixture sentinel")
    with listener(response(QUALIFYING_BODY), count=2) as (port, requests):
        (store / "policy.json").write_text(json.dumps({
            "schema": health_slots.POLICY_SCHEMA, "version": 2,
            "publisher": "example-worker", "continuity": "example-generation",
            "collection": {"bridge_port": port, "services": []},
        }), encoding="utf-8")
        (store / "collection.lock").write_bytes(b"\0")
        policy_bytes = (store / "policy.json").read_bytes()
        definition = _definition(payload, store, identity["sid"], enabled=False)
        # Read back inert registration before enabling a distinct finite fixture.
        with _task(tmp_path, definition) as (_name, registered):
            assert registered["configured"] and not registered["enabled"]
            assert registered["instances"] == []
            readback = ET.fromstring(registered["xml"])
            assert readback.find("t:Principals/t:Principal/t:LogonType", NS).text == "InteractiveToken"
            level = readback.find("t:Principals/t:Principal/t:RunLevel", NS)
            assert level is None or level.text == "LeastPrivilege"
            assert readback.find("t:Settings/t:ExecutionTimeLimit", NS).text == "PT1M"
            _assert_gui_entry(readback.find("t:Actions/t:Exec/t:Command", NS).text)
        definition = _definition(payload, store, identity["sid"], enabled=True)
        samples, hours = [], {}
        with observation as observer:
            with _task(tmp_path, definition) as (name, registered):
                assert registered["configured"] and registered["enabled"]
                for count in (1, 2):
                    _until(lambda: len(requests) >= count, 90 if count == 1 else 75,
                           f"calendar cycle {count}")
                    state = _until(
                        lambda: (row if (row := _control("read", name))["state"] == 3
                                 and row["last_result"] == 0 else None),
                        20, f"cycle {count} completed",
                    )
                    samples.append(state)
                    records = [p for p in store.glob("*.json") if p.name != "policy.json"]
                    decoded = [json.loads(p.read_text(encoding="utf-8")) for p in records]
                    record = max(decoded, key=lambda row: row["observed_at_ms"])
                    assert record["observation"]["domains"]["local_listener"]["state"] == "ok"
                    hours[record["hour"]] = hours.get(record["hour"], 0) + 1
                    assert {row["hour"]: row["revision"] for row in decoded} == hours
                    assert len(requests) == count
                    identities = [
                        json.loads(line) for line in (tmp_path / "resolver.jsonl").read_text(
                            encoding="utf-8-sig",
                        ).splitlines()
                    ]
                    assert len(identities) == count
                    assert all(row["session"] > 0 for row in identities)
                    if observed_session is not None and any(
                        row["session"] != observed_session for row in identities
                    ):
                        from sdk_desktop_observer import ObservationBlocked

                        raise ObservationBlocked("optional desktop/task session mismatch")
                    consoles = [
                        json.loads(line)
                        for line in (tmp_path / "console.jsonl").read_text(encoding="utf-8").splitlines()
                    ]
                    assert len(consoles) == 2 * count
                    assert all(row["console_window"] == 0 for row in consoles)
                    assert all(row["birth"] > 0 for row in consoles)
                assert samples[0]["last_run"] != samples[1]["last_run"]
                assert _control("read", name)["instances"] == []
        if observer is not None:
            packet = observer.packet()
            (tmp_path / "desktop-observation.json").write_text(json.dumps(packet), encoding="utf-8")
    assert (store / "policy.json").read_bytes() == policy_bytes
    assert (store / "collection.lock").read_bytes() == b"\0"
    assert unrelated.read_bytes() == b"fixture sentinel"
    assert not list(store.glob("*.tmp.*"))
    return observer.packet() if observer is not None else None


@pytest.mark.timeout(240)
def test_scheduler_functional_two_real_calendar_cycles_and_exact_cleanup(tmp_path):
    _exercise_cycles(tmp_path, nullcontext())


def _store(tmp_path, port):
    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 2,
        "publisher": "example-worker", "continuity": "example-generation",
        "collection": {"bridge_port": port, "services": []},
    }), encoding="utf-8")
    (store / "collection.lock").write_bytes(b"\0")
    return store


def _resolver_identity(tmp_path):
    try:
        lines = (tmp_path / "resolver.jsonl").read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return None
    return json.loads(lines[0]) if lines else None


@pytest.mark.timeout(90)
def test_native_ignore_new_reuses_one_instance_and_one_collection(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "libs/agent-procutil/tests"))
    from sdk_native_win32 import NativeProcessAPI

    api = NativeProcessAPI()
    payload = _payload(tmp_path, delay_seconds=8)
    identity = _control("identity")
    assert identity["interactive"]
    with listener(response(QUALIFYING_BODY), count=2) as (port, requests):
        store = _store(tmp_path, port)
        definition = _definition(payload, store, identity["sid"], enabled=True, calendar=False)
        pin = None
        try:
            with _task(tmp_path, definition) as (name, _registered):
                first = _control("run", name)
                launched = _until(lambda: _resolver_identity(tmp_path), 15, "resolver entry")
                pin = api.pin(launched["pid"], launched["birth"], owned=True)
                assert not pin.gone()
                assert len(first["instances"]) == 1
                again = _control("run", name)
                assert again["instances"] == first["instances"]
                complete = _until(
                    lambda: (row if (row := _control("read", name))["state"] == 3 else None),
                    30, "single overlapped action completion",
                )
                assert complete["last_result"] == 0
                _until(pin.gone, 5, "original resolver process settlement")
                assert len(requests) == 1
                assert len((tmp_path / "resolver.jsonl").read_text(encoding="utf-8-sig").splitlines()) == 1
                record = next(p for p in store.glob("*.json") if p.name != "policy.json")
                assert json.loads(record.read_text(encoding="utf-8"))["revision"] == 1
        finally:
            if pin is not None:
                try:
                    if not pin.gone():
                        pin.terminate_owned()
                        _until(pin.gone, 5, "exact fixture process cleanup")
                finally:
                    pin.close()


@pytest.mark.timeout(115)
@pytest.mark.parametrize("outer_ceiling", [False, True])
def test_native_deadline_settles_the_exact_console_descendant(tmp_path, monkeypatch, outer_ceiling):
    monkeypatch.syspath_prepend(str(REPO / "libs/agent-procutil/tests"))
    from sdk_native_win32 import NativeProcessAPI

    api = NativeProcessAPI()
    # Delay only the copied selector, below the unchanged production launch chain.
    payload = _payload(tmp_path, delay_seconds=90)
    if outer_ceiling:
        entry = payload / "scripts/health-job-entry.py"
        entry.write_text(
            entry.read_text(encoding="utf-8").replace("ACTION_SECONDS = 55", "ACTION_SECONDS = 90"),
            encoding="utf-8",
        )
    identity = _control("identity")
    assert identity["interactive"]
    store = _store(tmp_path, 1)
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    definition = _definition(payload, store, identity["sid"], enabled=True, calendar=False)
    pin = None
    try:
        with _task(tmp_path, definition) as (name, _registered):
            started = time.monotonic()
            running = _control("run", name)
            assert len(running["instances"]) == 1
            launched = _until(lambda: _resolver_identity(tmp_path), 15, "deadline resolver entry")
            pin = api.pin(launched["pid"], launched["birth"], owned=True)
            stopped = _until(
                lambda: (row if (row := _control("read", name))["state"] == 3 else None),
                75, "native sixty-second ceiling",
            )
            elapsed = time.monotonic() - started
            assert (55 <= elapsed < 75) if outer_ceiling else (45 <= elapsed < 60)
            assert stopped["last_result"] != 0 if outer_ceiling else stopped["last_result"] == 124
            _until(pin.gone, 5, "native timeout must retire the original console descendant")
            assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    finally:
        if pin is not None:
            try:
                if not pin.gone():
                    pin.terminate_owned()
                    _until(pin.gone, 5, "exact deadline fixture cleanup")
            finally:
                pin.close()


@pytest.mark.timeout(60)
def test_native_failed_slot_preserves_policy_existing_data_and_reports_failure(tmp_path):
    payload = _payload(tmp_path)
    identity = _control("identity")
    assert identity["interactive"]
    with listener(response(QUALIFYING_BODY), count=2) as (port, requests):
        store = _store(tmp_path, port)
        hour = time.time_ns() // 1_000_000 // health_slots.HOUR_MS
        blocked = [store / f"{(hour + offset) % health_slots.SLOTS:03d}.json" for offset in (0, 1)]
        for slot in blocked:
            slot.mkdir()
            (slot / "sentinel").write_bytes(b"not replaceable")
        before = (store / "policy.json").read_bytes()
        definition = _definition(payload, store, identity["sid"], enabled=True, calendar=False)
        with _task(tmp_path, definition) as (name, _registered):
            _control("run", name)
            completed = _until(
                lambda: (row if (row := _control("read", name))["state"] == 3
                         and row["last_result"] not in (267009, 267011) else None),
                30, "failed storage action result",
            )
            assert completed["last_result"] == 2
        assert len(requests) == 0
    assert (store / "policy.json").read_bytes() == before
    assert all((slot / "sentinel").read_bytes() == b"not replaceable" for slot in blocked)
    assert not list(store.glob("*.tmp.*"))


@pytest.mark.timeout(60)
def test_native_unicode_payload_and_store_paths_survive_powershell_decoding(tmp_path):
    root = tmp_path / "unicode-\u00e9"
    root.mkdir()
    payload = _payload(root)
    identity = _control("identity")
    assert identity["interactive"]
    with listener(response(QUALIFYING_BODY), count=2) as (port, requests):
        store = _store(root, port)
        definition = _definition(payload, store, identity["sid"], enabled=True, calendar=False)
        with _task(root, definition) as (name, registered):
            xml = ET.fromstring(registered["xml"])
            assert xml.find("t:Actions/t:Exec/t:WorkingDirectory", NS).text == str(payload)
            _control("run", name)
            completed = _until(
                lambda: (row if (row := _control("read", name))["state"] == 3
                         and row["last_result"] not in (267009, 267011) else None),
                30, "Unicode-path action completion",
            )
            assert completed["last_result"] == 0
        assert len(requests) == 1
    assert (root / "resolver.jsonl").is_file()
    consoles = [
        json.loads(line) for line in (root / "console.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(consoles) == 2 and all(row["console_window"] == 0 for row in consoles)
