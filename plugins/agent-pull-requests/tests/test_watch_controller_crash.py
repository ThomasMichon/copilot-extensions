"""Owned controller death must recover under a different native guard holder."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import venv
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from test_cell_upgrade import _pending
from test_watch_control import _receiver
from test_watch_driver import _active
from test_watch_invocation import installed as installed
from test_watch_journal import _manifest
from test_watch_runtime import _complete
from zdd import breadcrumb, publication_history, routing
from zdd.process_exit import open_process

from agent_pull_requests import _installation_context as api
from agent_pull_requests.watch_control import WatchControlClient, WatchControlRefused
from agent_pull_requests.watch_journal import WatchJournal, _selection
from agent_pull_requests.watch_recovery import reconcile
from agent_pull_requests.watch_storage import read_subscriptions_state

_CONTROLLER = """
import json, os, sys
from pathlib import Path
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import capture_running
from agent_pull_requests.watch_driver import WatchDriver
from agent_pull_requests.watch_control import WatchControlClient
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_publication import WatchRouting
from agent_pull_requests.watch_runtime import WatchInstance
from agent_pull_requests import _installation_context as api
from agent_pull_requests import watch_journal, watch_publication
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
runtime = capture_running(installation, environment={})
predecessor = WatchInstance.from_evidence(
    json.loads(sys.argv[6]), installation, environment={},
)
options = json.loads(sys.argv[9])
seam = options["seam"]
def crash():
    os._exit(73)
def after(owner, name, predicate):
    original = getattr(owner, name)
    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        if predicate(*args, **kwargs):
            crash()
        return result
    setattr(owner, name, interrupted)
if seam == "passive":
    after(WatchJournal, "bind_successor", lambda *_a, **_k: True)
elif seam == "endpoint":
    after(WatchJournal, "bind_endpoint", lambda self, role, *_a, **_k: role == "successor")
elif seam in {"released", "read-ready"}:
    after(WatchJournal, "advance", lambda self, phase: phase == seam)
elif seam == "marker-cas":
    after(api, "cutover_runtime_slot", lambda **_k: True)
elif seam == "route-published":
    after(WatchRouting, "publish_active_with_previous_guarded", lambda *_a, **_k: True)
elif seam == "uncaptured-route":
    watch_publication.capture_publication = lambda *_a, **_k: crash()
elif seam == "ambiguous-commit":
    after(watch_journal, "_persist", lambda path, document, **_k: document["phase"] == "committed")
elif seam == "committed":
    after(WatchJournal, "commit", lambda *_a, **_k: True)
elif seam == "activated":
    after(WatchControlClient, "control", lambda self, operation, **_k: operation == "activate")
def spawn(port, instance_id):
    print(json.dumps({"port": port, "instanceId": instance_id}), flush=True)
    if sys.stdin.readline().strip() != "spawned":
        raise RuntimeError("fixture child was not acknowledged")
    if seam == "launched":
        crash()
with installation.transaction(timeout=1, environment={}) as guard:
    journal = WatchJournal.begin(
        installation, guard, target=runtime,
        target_manifest=bytes.fromhex(options["manifest"]),
        predecessor=predecessor, environment={},
    )
    old = WatchControlClient(
        endpoint=tuple(json.loads(sys.argv[8])), instance=predecessor,
        installation=lambda: installation,
        transaction=lambda: (journal.transaction_id, journal.authorization), request_timeout=30,
    )
    journal.bind_endpoint("predecessor", old._endpoint, read_health=old.health)
    driver = WatchDriver(
        journal, spawn=spawn, pick_free_port=lambda: int(sys.argv[7]), timeout=120,
    )
    print(json.dumps({"controller": journal._document["controller"]}), flush=True)
    if sys.stdin.readline().strip() != "captured":
        raise RuntimeError("fixture controller was not captured")
    driver.run()
raise AssertionError("fixture crash seam was not reached")
"""


def _target(installed):
    installation, _, _, library = installed
    runtime = _complete(installation, version="2.0.0")
    venv.EnvBuilder(with_pip=False).create(runtime.runtime_root)
    python = runtime.runtime_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(  # noqa: S603 -- test-owned completed fixture interpreter
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, check=True, timeout=15, **no_window_kwargs(),
    )
    destination = Path(result.stdout.strip())
    for package in (
        "agent_pull_requests", "zdd", "single_instance_lease", "work_coalescing_singleton",
        "agent_procutil",
    ):
        shutil.copytree(library / package, destination / package,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (destination / "agent_pull_requests" / "__init__.py").write_text(
        f'"""Test-owned completed target."""\n__version__ = "{runtime.runtime_version}"\n',
        encoding="utf-8",
    )
    return installation, runtime, python, destination


def _native_read_evidence(descriptor):
    if os.name != "nt" or descriptor < 0:
        return None
    import ctypes
    import msvcrt
    import struct
    from ctypes import wintypes

    query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    query.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(descriptor)
    evidence = {}
    for name, info_class, size, layout in (
        ("basic", 0, 40, "<qqqqI4x"),
        ("identity", 18, 24, "<Q16s"),
    ):
        buffer = ctypes.create_string_buffer(size)
        if query(handle, info_class, buffer, size):
            evidence[name] = [
                value.hex() if isinstance(value, bytes) else value
                for value in struct.unpack(layout, buffer.raw)
            ]
        else:
            evidence[name] = {"winerror": ctypes.get_last_error()}
    return evidence


def _trace_regular_reads(monkeypatch):
    fail = api._fail
    fstat = os.fstat
    observations = threading.local()

    def trace_stat(descriptor):
        value = fstat(descriptor)
        frame = sys._getframe(1)
        if frame.f_code.co_name == "_read_regular_file":
            name = "final" if "chunks" in frame.f_locals else "opened"
            setattr(observations, name, _native_read_evidence(descriptor))
        return value

    def trace_refusal(message):
        frame = sys._getframe(1)
        if frame.f_code.co_name == "_read_regular_file" and "changed while" in message:
            evidence = {"path": str(frame.f_locals.get("path"))}
            for name in ("opened_stat", "named_stat", "final_stat", "current_stat"):
                value = frame.f_locals.get(name)
                if value is not None:
                    evidence[name] = {
                        field: getattr(value, field, None) for field in (
                            "st_dev", "st_ino", "st_mode", "st_size",
                            "st_mtime_ns", "st_ctime_ns", "st_birthtime_ns",
                        )
                    }
            evidence["native"] = _native_read_evidence(frame.f_locals.get("descriptor", -1))
            evidence["native_opened"] = getattr(observations, "opened", None)
            evidence["native_final"] = getattr(observations, "final", None)
            message += f" Fixture native read evidence: {json.dumps(evidence, sort_keys=True)}"
        fail(message)

    monkeypatch.setattr(api, "_fail", trace_refusal)
    monkeypatch.setattr(os, "fstat", trace_stat)


def test_native_reader_refusal_captures_open_handle_metadata(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    path.write_text('{"fixture":true}', encoding="utf-8")
    before = path.stat()
    _trace_regular_reads(monkeypatch)

    def change_metadata(_chunk):
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))

    with pytest.raises(api.InstallationContextError, match="changed while") as refused:
        api._read_regular_file(
            path, label="Fixture receipt", require_stable_identity=True,
            consume_chunk=change_metadata,
        )
    evidence = json.loads(str(refused.value).split("Fixture native read evidence: ", 1)[1])
    assert evidence["path"] == str(path)
    assert evidence["opened_stat"]["st_ino"] == evidence["final_stat"]["st_ino"]
    assert evidence["opened_stat"]["st_mtime_ns"] != evidence["final_stat"]["st_mtime_ns"]
    if os.name == "nt":
        assert evidence["native_opened"]["identity"] == evidence["native_final"]["identity"]
        assert evidence["native_opened"]["basic"][2] != evidence["native_final"]["basic"][2]
        assert evidence["native"] == evidence["native_final"]
    else:
        assert evidence["native"] is None


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(180)
@pytest.mark.parametrize("consumer", ["raw", "completed-owner"])
def test_completed_receipt_parallel_reads_keep_native_identity(installed, monkeypatch, consumer):
    _trace_regular_reads(monkeypatch)
    installation, runtime, _, _ = installed
    path = installation.receipt
    expected = path.read_bytes()
    until = None

    def read_many():
        count = 0
        while count < 512 or (until is not None and time.monotonic() < until):
            content, _ = api._read_regular_file(
                path, label="Fixture receipt", require_stable_identity=True,
            )
            assert content == expected
            count += 1

    if consumer == "raw":
        with ThreadPoolExecutor(max_workers=8) as readers:
            for future in [readers.submit(read_many) for _ in range(8)]:
                future.result(timeout=120)
        return

    with _receiver(installed, active=True) as receiver:
        client = WatchControlClient(
            endpoint=receiver[2], instance=receiver[1], installation=lambda: installation,
            transaction=lambda: ("", ""), request_timeout=30,
        )
        until = time.monotonic() + 75

        def validate_many():
            count = 0
            while count < 8 or time.monotonic() < until:
                runtime.require_identity(installation, environment={})
                health = client.health()
                assert health["writerHeld"] and health["effectsEnabled"]
                count += 1

        with ThreadPoolExecutor(max_workers=8) as readers:
            futures = [
                readers.submit(read_many if index < 4 else validate_many)
                for index in range(8)
            ]
            for future in futures:
                future.result(timeout=120)
        assert receiver[0].poll() is None


@pytest.mark.timeout(180)
@pytest.mark.parametrize("seam", [
    "passive",
    *(
        pytest.param(seam, marks=pytest.mark.portfolio_tier("T3"))
        for seam in (
            "launched", "endpoint", "released", "read-ready", "marker-cas",
            "uncaptured-route", "route-published", "ambiguous-commit",
            "committed", "activated", "committed-unreachable",
        )
    ),
])
def test_actual_controller_death_recovers_under_new_guard(installed, seam, tmp_path, monkeypatch):
    from test_watch_control import _CHILD
    from test_watch_driver import _port

    _trace_regular_reads(monkeypatch)
    installation, target, python, _ = successor_slot = _target(installed)
    forward = seam in {"committed", "activated", "ambiguous-commit", "committed-unreachable"}
    unavailable = tmp_path / "unavailable"
    saved = _pending(installation.state_root, installed[2])
    with ExitStack() as children:
        with installation.transaction(timeout=1, environment={}), monkeypatch.context() as patch:
            patch.setattr("test_watch_control._CHILD", _CHILD.replace(
                "server = WatchControlServer(owner.compute,",
                f"""
def fixture_compute(kind, payload):
    if kind == "health" and Path({str(unavailable)!r}).exists():
        return {{"error": "fixture-owned predecessor health unavailable"}}
    return owner.compute(kind, payload)
server = WatchControlServer(fixture_compute,""",
            ))
            predecessor = _active(installed, children)
        prior = _selection(installation)
        controller = subprocess.Popen(  # noqa: S603 -- owned physical completed-slot controller
            [str(python), "-I", "-c", _CONTROLLER, *(
                str(getattr(installation, key)) for key in (
                    "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
                )
            ), json.dumps(predecessor.instance.evidence()), str(_port()),
             json.dumps(predecessor._endpoint),
             json.dumps({
                 "seam": "committed" if seam == "committed-unreachable" else seam,
                 "manifest": _manifest(target).hex(),
             })],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            **no_window_kwargs(),
        )
        try:
            line = controller.stdout.readline()
            assert line, controller.communicate(timeout=10)
            identity = json.loads(line)["controller"]
            assert identity["pid"] != predecessor.instance.pid
            with open_process(identity["pid"], identity["startTime"]) as original:
                controller.stdin.write("captured\n")
                controller.stdin.flush()
                line = controller.stdout.readline()
                assert line, controller.communicate(timeout=10)
                launch = json.loads(line)
                child, instance, endpoint = children.enter_context(_receiver(
                    successor_slot, instance_id=launch["instanceId"],
                    bind_port=launch["port"], publish_launch=True,
                ))
                with (
                    open_process(instance.pid, instance.start_time) as successor_exit,
                    open_process(
                        predecessor.instance.pid, predecessor.instance.start_time,
                    ) as old_exit,
                ):
                    controller.stdin.write("spawned\n")
                    controller.stdin.flush()
                    assert controller.wait(timeout=120) == 73, controller.communicate(timeout=5)
                    assert original.wait(0)
                    pending = json.loads((
                        installation.run_root / "watch-owner-transaction.json"
                    ).read_text(encoding="utf-8"))
                    phases = {
                        "launched": "prepared", "endpoint": "passive", "marker-cas": "publishing",
                        "route-published": "publishing", "activated": "committed",
                        "uncaptured-route": "publishing", "ambiguous-commit": "committed",
                        "committed-unreachable": "committed",
                    }
                    assert pending["phase"] == phases.get(seam, seam)
                    assert pending["successor"] == (
                        None if seam == "launched" else instance.evidence()
                    )
                    if seam in {"launched", "passive"}:
                        assert pending["endpoints"]["successor"] is None
                    if seam == "marker-cas":
                        selected = _selection(installation)
                        assert bytes.fromhex(selected["current"]).decode().strip() == "2.0.0"
                        assert bytes.fromhex(selected["lastKnownGood"]).decode().strip() == "2.0.0"
                        assert selected["manifest"] == prior["manifest"]
                        assert selected["route"] == prior["route"]
                    before = routing.read_table(installation.run_root, strict=True)
                    if seam == "committed-unreachable":
                        unavailable.touch()
                        with installation.transaction(timeout=1, environment={}) as guard:
                            blocked = WatchJournal.recover(installation, guard, environment={})
                            with pytest.raises(WatchControlRefused, match="rejected"):
                                reconcile(blocked, timeout=120)
                            assert blocked._document["phase"] == "committed"
                            assert blocked._document["exited"] == []
                            assert not old_exit.wait(0) and not successor_exit.wait(0)
                            assert routing.read_table(installation.run_root, strict=True) == before
                        unavailable.unlink()
                    with installation.transaction(timeout=1, environment={}) as guard:
                        recovered = WatchJournal.recover(installation, guard, environment={})
                        assert recovered._document["controller"] != identity
                        assert recovered.authorization != pending["authorization"]
                        reconcile(recovered, timeout=120)
                        assert recovered._document["phase"] == (
                            "complete" if forward else "rolled-back"
                        )
                        assert recovered._document["exited"] == (
                            ["predecessor"] if forward else ["successor"]
                        )
                        assert recovered._document["endpoints"]["successor"] is not None
                        selected = _selection(installation)
                        if forward:
                            assert old_exit.wait(0)
                            assert not successor_exit.wait(0)
                            assert child.poll() is None
                            for key in ("current", "lastKnownGood"):
                                assert bytes.fromhex(selected[key]).decode().strip() == "2.0.0"
                            assert bytes.fromhex(selected["manifest"]) == _manifest(target)
                            serving, serving_endpoint = instance, endpoint
                        else:
                            assert successor_exit.wait(0)
                            assert child.wait(timeout=10) == 0
                            assert not old_exit.wait(0)
                            assert all(selected[key] == prior[key] for key in (
                                "current", "lastKnownGood", "manifest",
                            ))
                            serving, serving_endpoint = predecessor.instance, predecessor._endpoint
                        after = routing.read_table(installation.run_root, strict=True)
                        assert all(entry in after[publication_history.KEY]["entries"]
                                   for entry in before[publication_history.KEY]["entries"])
                        if seam not in {"route-published", "uncaptured-route"} or forward:
                            assert after == before
                        else:
                            assert after["active"]["generation"] > before["active"]["generation"]
                        owner = WatchControlClient(
                            endpoint=serving_endpoint, instance=serving,
                            installation=lambda: installation,
                            transaction=lambda: (
                                recovered.transaction_id, recovered.authorization,
                            ),
                            request_timeout=30,
                        )
                        status = owner.health()
                        assert all(status[key] is True for key in (
                            "writerHeld", "readReady", "effectsEnabled",
                        ))
                        assert routing.read_table(installation.run_root)["active"]["pid"] == (
                            serving.pid
                        )
                        archive = installation.run_root / "watch-owner-history" / (
                            recovered.transaction_id + ".json"
                        )
                        assert json.loads(archive.read_text()) == recovered._document
                        assert breadcrumb.read_breadcrumb(installation.run_root) is None
                        assert read_subscriptions_state(installation.state_root) == [saved]
        finally:
            if controller.poll() is None:
                controller.stdin.close()
                controller.wait(timeout=130)
            for stream in (controller.stdin, controller.stdout, controller.stderr):
                if stream is not None:
                    stream.close()
