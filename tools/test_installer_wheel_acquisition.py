"""Controlled TLS-feed checks; no live feed or production installation."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_dependency_policy as policy
import installer_wheel_acquisition as acquisition
from installer_engine_process import _load_resource

CERTIFICATE = Path(__file__).with_name("fixtures") / "wheel-feed-localhost.pem"
IDENTITY = "sha256:" + "a" * 64
POLICY = "sha256:" + "b" * 64
FILENAME = "fixture-1.0-py3-none-any.whl"
DATA = b"selected synthetic archive bytes"
EXPECTED = {
    "name": "fixture", "version": "1.0", "filename": FILENAME,
    "sha256": "sha256:" + hashlib.sha256(DATA).hexdigest(), "requires_python": ">=3.9",
}


@pytest.fixture
def feeds():
    owned = []

    def create(*, certificate=CERTIFICATE):
        state = {"routes": {}, "requests": []}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond(head=False)

            def do_HEAD(self):
                self.respond(head=True)

            def respond(self, *, head):
                state["requests"].append((self.path, dict(self.headers)))
                if state.get("authorize") and not state["authorize"](self.headers):
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                route = state["routes"].get(self.path)
                if callable(route):
                    route(self)
                    return
                status, headers, body = route or (404, {}, b"")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def log_message(self, *_arguments):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        state["origin"] = f"https://127.0.0.1:{server.server_port}"
        owned.append((server, thread))
        return state

    yield create
    for server, thread in reversed(owned):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def listed(*, url=None, **changes):
    return {
        "filename": FILENAME, "url": url or "/files/" + FILENAME,
        "hashes": {"sha256": EXPECTED["sha256"].removeprefix("sha256:")},
        "requires-python": ">=3.9", "upload-time": "2020-01-01T00:00:00Z", "yanked": False,
        **changes,
    }


def project(state, entries=None):
    body = json.dumps({
        "meta": {"api-version": "1.1"}, "name": "fixture",
        "files": entries if entries is not None else [listed()],
    }).encode()
    state["routes"]["/simple/fixture/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, body,
    )
    state["routes"]["/files/" + FILENAME] = (200, {}, DATA)
    return body


def limits(**changes):
    return replace(acquisition.AcquisitionLimits(
        max_packages=3, max_requests=10, max_redirects=5,
        max_candidates_per_project=10, max_index_bytes=4096,
        max_total_metadata_bytes=8192, max_wheel_bytes=4096,
        max_closure_bytes=8192, max_temporary_bytes=1 << 20,
    ), **changes)


def worker_procutil():
    name = "_wheel_acquisition_test_procutil"
    if name not in sys.modules:
        _load_resource(
            name, Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py",
        )
    return sys.modules[name]


def acquire(state, **changes):
    arguments = {
        "feeds": (acquisition.WheelFeed(IDENTITY, state["origin"] + "/simple"),),
        "download_origins": (), "cutoffs": {"fixture": datetime(2025, 1, 1, tzinfo=timezone.utc)},
        "policy_id": POLICY, "limits": limits(), "deadline": time.monotonic() + 10,
        "ssl_context": ssl.create_default_context(cafile=str(CERTIFICATE)), "proxies": {},
        "procutil": worker_procutil(),
    }
    expected = changes.pop("expected", [EXPECTED])
    return acquisition.acquire_wheel_inputs(expected, **(arguments | changes))


def test_real_tls_acquisition_retains_exact_bytes_and_policy_facts(feeds):
    state = feeds()
    metadata = project(state)
    with acquire(state) as result:
        paths = tuple(result.snapshot.paths.values())
        assert result.snapshot.archives[FILENAME].read() == DATA
        result.snapshot.verify()
        evidence = result.evidence()
        assert evidence["policy_id"] == POLICY
        assert evidence["metadata_bytes"] == len(metadata)
        assert evidence["wheel_bytes"] == len(DATA)
        assert evidence["request_count"] == 2
        assert evidence["files"][0]["feed"] == IDENTITY
        assert evidence["files"][0]["sha256"] == EXPECTED["sha256"]
        assert evidence["files"][0]["uploaded"] == "2020-01-01T00:00:00+00:00"
        assert b"127.0.0.1" not in result.evidence_json
    assert all(not path.exists() for path in paths)
    assert [path for path, _ in state["requests"]] == ["/simple/fixture/", "/files/" + FILENAME]


def test_html_metadata_preserves_hash_upload_time_and_python_hint(feeds):
    state = feeds()
    project(state)
    digest = EXPECTED["sha256"].removeprefix("sha256:")
    body = (
        f'<a href="/files/{FILENAME}#sha256={digest}" data-upload-time="2020-01-01T00:00:00Z" '
        f'data-requires-python="&gt;=3.9">{FILENAME}</a>'
    ).encode()
    state["routes"]["/simple/fixture/"] = (200, {"Content-Type": "text/html"}, body)
    with acquire(state) as result:
        assert result.snapshot.archives[FILENAME].read() == DATA


@pytest.mark.parametrize("change,message,error", [
    ({"yanked": True}, "refused", acquisition.AcquisitionUnavailable),
    ({"yanked": "withdrawn by owner"}, "refused", acquisition.AcquisitionUnavailable),
    ({"withdrawn": True}, "refused", acquisition.AcquisitionUnavailable),
    ({"upload-time": "2030-01-01T00:00:00Z"}, "seasoning", acquisition.AcquisitionUnavailable),
    ({"upload-time": None}, "upload time", ValueError),
    ({"upload-time": "2020-01-01"}, "upload time", ValueError),
    ({"hashes": {"sha256": "0" * 64}}, "digest", ValueError),
    ({"requires-python": ">=9"}, "Python hint", ValueError),
])
def test_file_admission_refusals_precede_wheel_download(feeds, change, message, error):
    state = feeds()
    project(state, [listed(**change)])
    with pytest.raises(error, match=message):
        with acquire(state):
            pytest.fail("refused file was acquired")
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("route", ["link", "redirect", "index-redirect"])
def test_untrusted_origin_is_refused_before_endpoint_receives_a_request(feeds, route):
    state, untrusted = feeds(), feeds()
    location = untrusted["origin"] + "/files/" + FILENAME + "?signature=synthetic-private-value"
    project(state)
    if route == "link":
        project(state, [listed(url=location)])
    elif route == "redirect":
        state["routes"]["/files/" + FILENAME] = (
            302, {"Location": location}, b"",
        )
    else:
        state["routes"]["/simple/fixture/"] = (
            302, {"Location": untrusted["origin"] + "/simple/fixture/"}, b"",
        )
    with pytest.raises(ValueError, match="source trust") as error:
        with acquire(state):
            pytest.fail("untrusted endpoint was reached")
    assert untrusted["requests"] == []
    assert untrusted["origin"] in str(error.value)
    assert "synthetic-private-value" not in str(error.value)
    assert FILENAME not in str(error.value)


def test_explicit_download_origin_allows_redirect_without_forwarding_credentials(feeds):
    state, download = feeds(), feeds()
    project(state)
    download["routes"]["/files/" + FILENAME] = (200, {}, DATA)
    state["routes"]["/files/" + FILENAME] = (
        307, {"Location": download["origin"] + "/files/" + FILENAME}, b"",
    )
    fake = "synthetic-test-header"
    feed = acquisition.WheelFeed(IDENTITY, state["origin"] + "/simple", (("Authorization", fake),))
    with acquire(state, feeds=(feed,), download_origins=(download["origin"],)) as result:
        assert result.snapshot.archives[FILENAME].read() == DATA
        assert fake.encode() not in result.evidence_json
    assert all(headers.get("Authorization") == fake for _, headers in state["requests"])
    assert all("Authorization" not in headers for _, headers in download["requests"])


@pytest.mark.parametrize("location", [
    "http://127.0.0.1:9/file.whl", "file:///untrusted.whl",
    "https://user:password@example.invalid/wheel.whl", "https://*.invalid/wheel.whl",
])
def test_unsafe_link_location_is_refused_before_request(feeds, location):
    state = feeds()
    project(state, [listed(url=location)])
    with pytest.raises(ValueError, match="HTTPS location"):
        with acquire(state):
            pytest.fail("unsafe link reached transport")
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("option,value,message", [
    ("max_requests", 1, "request budget"), ("max_index_bytes", 1, "byte budget"),
    ("max_total_metadata_bytes", 1, "byte budget"), ("max_wheel_bytes", len(DATA) - 1, "byte budget"),
    ("max_closure_bytes", len(DATA) - 1, "byte budget"),
    ("max_temporary_bytes", len(DATA) * 2 - 1, "storage"),
])
def test_independent_inclusive_budgets_refuse_and_clean_partial_inputs(
    feeds, tmp_path, monkeypatch, option, value, message,
):
    state = feeds()
    project(state)
    actual = policy._private_retained_root
    roots = []

    def private(path):
        roots.append(path)
        return actual(path)

    monkeypatch.setattr(policy, "_private_retained_root", private)
    with pytest.raises(ValueError, match=message):
        with acquire(state, limits=limits(**{option: value})):
            pytest.fail("over-budget input was admitted")
    assert roots and all(not path.exists() for path in roots)
    assert list(tmp_path.iterdir()) == []


def test_exact_metadata_wheel_and_double_copy_storage_limits_are_inclusive(feeds):
    state = feeds()
    metadata = project(state)
    with acquire(state, limits=limits(
        max_requests=2, max_candidates_per_project=1,
        max_index_bytes=len(metadata), max_total_metadata_bytes=len(metadata),
        max_wheel_bytes=len(DATA), max_closure_bytes=len(DATA),
    )) as result:
        assert result.snapshot.archives[FILENAME].read() == DATA


def test_changed_download_digest_is_not_an_availability_failure(feeds):
    state = feeds()
    project(state)
    state["routes"]["/files/" + FILENAME] = (200, {}, b"changed bytes")
    with pytest.raises(acquisition.WheelError, match="digest") as failure:
        with acquire(state):
            pytest.fail("changed wheel admitted")
    assert not isinstance(failure.value, acquisition.AcquisitionUnavailable)


def test_expired_deadline_does_not_send_a_request(feeds):
    state = feeds()
    project(state)
    with pytest.raises(ValueError, match="deadline"):
        with acquire(state, deadline=time.monotonic() - 1):
            pytest.fail("expired acquisition admitted")
    assert not state["requests"]


def test_invalid_tls_trust_is_a_sanitized_refusal(feeds):
    state = feeds()
    project(state)
    with pytest.raises(ValueError, match="request failed") as failure:
        with acquire(state, ssl_context=ssl.create_default_context()):
            pytest.fail("untrusted TLS certificate admitted")
    assert "127.0.0.1" not in str(failure.value)
    assert not state["requests"]


def test_cancellation_during_consumption_settles_both_owned_input_contexts(feeds):
    state = feeds()
    project(state)
    with pytest.raises(KeyboardInterrupt):
        with acquire(state) as result:
            paths = tuple(result.snapshot.paths.values())
            raise KeyboardInterrupt
    assert all(not path.exists() for path in paths)


def test_storage_peak_counts_worker_control_and_exact_snapshot_copies(feeds, monkeypatch):
    state = feeds()
    content = b"x" * 131_072
    expected = EXPECTED | {"sha256": "sha256:" + hashlib.sha256(content).hexdigest()}
    metadata = project(state, [listed(hashes={"sha256": expected["sha256"][7:]})])
    state["routes"]["/files/" + FILENAME] = (200, {}, content)
    roots = []
    actual = policy._private_retained_root

    def private(path):
        roots.append(path)
        return actual(path)

    monkeypatch.setattr(policy, "_private_retained_root", private)
    budget = 2 * len(content)
    with acquire(state, expected=[expected], limits=limits(
        max_index_bytes=len(metadata), max_total_metadata_bytes=len(metadata),
        max_wheel_bytes=len(content), max_closure_bytes=len(content), max_temporary_bytes=budget,
    )) as result:
        actual_bytes = sum(path.stat().st_size for root in roots for path in root.rglob("*") if path.is_file())
        assert actual_bytes == result.evidence()["temporary_highwater_bytes"] == budget
        assert result.evidence()["control_bytes"] < len(content)
    with pytest.raises(ValueError, match="storage"):
        with acquire(state, expected=[expected], limits=limits(
            max_wheel_bytes=len(content), max_closure_bytes=len(content), max_temporary_bytes=budget - 1,
        )):
            pytest.fail("one byte below the exact storage peak was admitted")
    assert all(not root.exists() for root in roots)


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_trickling_http_cannot_renew_the_whole_deadline(feeds, phase):
    state = feeds()
    project(state)
    done = threading.Event()

    def trickle(handler):
        try:
            if phase == "headers":
                handler.wfile.write(b"HTTP/1.1 200 OK\r\n")
                handler.wfile.flush()
                for _ in range(40):
                    handler.wfile.write(b"X-Slow: value\r\n")
                    handler.wfile.flush()
                    time.sleep(0.2)
            else:
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(DATA)))
                handler.end_headers()
                for value in DATA:
                    handler.wfile.write(bytes([value]))
                    handler.wfile.flush()
                    time.sleep(0.2)
        except OSError:
            pass
        finally:
            done.set()

    state["routes"]["/simple/fixture/" if phase == "headers" else "/files/" + FILENAME] = trickle
    started = time.monotonic()
    with pytest.raises(ValueError, match="deadline|timed out"):
        with acquire(state, deadline=started + 2):
            pytest.fail("trickling response extended the deadline")
    assert time.monotonic() - started < 5
    assert done.wait(2)


def _running(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_blocked_dns_worker_is_reaped_before_return_and_private_cleanup(
    feeds, tmp_path, monkeypatch,
):
    state = feeds()
    project(state)
    marker = tmp_path / "worker-pid"
    sources = acquisition._worker_sources()
    sources["installer_wheel_acquisition.py"] += (
        "\nimport socket\n"
        "def _blocked_dns(*args, **kwargs):\n"
        "    Path(" + repr(str(marker)) + ").write_text(str(os.getpid()))\n"
        "    time.sleep(30)\n"
        "socket.getaddrinfo = _blocked_dns\n"
    ).encode()
    monkeypatch.setattr(acquisition, "_worker_sources", lambda: sources)
    roots = []
    actual = policy._private_retained_root

    def private(path):
        roots.append(path)
        return actual(path)

    monkeypatch.setattr(policy, "_private_retained_root", private)
    started = time.monotonic()
    with pytest.raises(ValueError, match="deadline"):
        with acquire(state, deadline=started + 2):
            pytest.fail("blocked DNS outlived acquisition custody")
    assert marker.is_file()
    assert not _running(int(marker.read_text()))
    assert time.monotonic() - started < 5
    assert state["requests"] == []
    assert roots and all(not root.exists() for root in roots)


@pytest.mark.parametrize("entries,message,error", [
    ([listed(), listed()], "ambiguous", ValueError),
    ([listed(yanked=None)], "malformed", ValueError),
    ([listed(withdrawn="no")], "malformed", ValueError),
    ([], "unavailable", acquisition.AcquisitionUnavailable),
    ([listed(filename="another.whl")], "not listed", acquisition.AcquisitionUnavailable),
])
def test_ambiguous_or_refused_index_data_is_not_substituted(feeds, entries, message, error):
    state = feeds()
    project(state, entries)
    with pytest.raises(error, match=message):
        with acquire(state):
            pytest.fail("bad selected-file metadata was substituted")
    assert len(state["requests"]) == 1


def test_first_index_ownership_does_not_search_a_later_compatible_file(feeds):
    first, second = feeds(), feeds()
    project(first, [listed(filename="fixture-2.0-py3-none-any.whl")])
    project(second)
    selected = (
        acquisition.WheelFeed(IDENTITY, first["origin"] + "/simple"),
        acquisition.WheelFeed("sha256:" + "c" * 64, second["origin"] + "/simple"),
    )
    with pytest.raises(acquisition.AcquisitionUnavailable, match="not listed"):
        with acquire(first, feeds=selected):
            pytest.fail("later index replaced the native owning index")
    assert len(first["requests"]) == 1 and second["requests"] == []


def test_long_selected_wheel_name_keeps_native_extended_path_custody(feeds):
    state = feeds()
    name = "fixture" + "x" * 190
    filename = name + "-1.0-py3-none-any.whl"
    expected = EXPECTED | {"name": name, "filename": filename}
    body = json.dumps({
        "meta": {"api-version": "1.1"}, "name": name,
        "files": [listed(filename=filename, url="/files/" + filename)],
    }).encode()
    state["routes"]["/simple/" + name + "/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, body,
    )
    state["routes"]["/files/" + filename] = (200, {}, DATA)
    with acquire(state, expected=[expected], cutoffs={name: datetime(2025, 1, 1, tzinfo=timezone.utc)}) as result:
        path = result.snapshot.paths[filename]
        if os.name == "nt":
            assert len(str(path)) > 260
        assert result.snapshot.archives[filename].read() == DATA
        result.snapshot.verify()
    assert not path.exists()


@pytest.mark.parametrize("filename", ["NUL.whl", "CON.whl", "../escape.whl"])
def test_invalid_selected_paths_refuse_before_any_request(feeds, filename):
    state = feeds()
    project(state)
    with pytest.raises(ValueError, match="exact file"):
        with acquire(state, expected=[EXPECTED | {"filename": filename}]):
            pytest.fail("invalid selected filename reached acquisition")
    assert state["requests"] == []


@pytest.mark.skipif(os.name != "nt", reason="native Windows windowless-parent observation")
def test_acquisition_console_workers_from_windowless_parent_surface_no_ui(feeds, tmp_path, monkeypatch):
    state = feeds()
    project(state)
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "libs" / "agent-procutil" / "src"))
    observer = _load_resource(
        "_acquisition_window_observer",
        root / "libs" / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py",
    )
    procutil = worker_procutil()
    pythonw = procutil.windowless_python(sys.executable, require_windowless=True)
    script = tmp_path / "windowless-parent.py"
    result_path, workers_path = tmp_path / "result.json", tmp_path / "workers.jsonl"
    script.write_text("""
import json, pathlib, ssl, sys, time, traceback
spec = json.loads(sys.argv[1])
try:
    sys.path.insert(0, spec["engine"])
    import installer_wheel_acquisition as acquisition
    from installer_engine_process import _load_resource
    procutil = _load_resource("_windowless_acquisition_procutil", pathlib.Path(spec["procutil"]))
    sources = acquisition._worker_sources
    def observed_sources():
        result = sources()
        result["installer_wheel_acquisition.py"] += (
            "\\nwith Path(" + repr(spec["workers"]) + ").open('a') as output:\\n"
            "    output.write(json.dumps({'pid': os.getpid(), 'executable': sys.executable}) + '\\\\n')\\n"
        ).encode()
        return result
    acquisition._worker_sources = observed_sources
    cycles = 0
    for _ in range(2):
        with acquisition.acquire_wheel_inputs(
            spec["expected"], feeds=(acquisition.WheelFeed(spec["identity"], spec["origin"] + "/simple"),),
            download_origins=(), cutoffs={"fixture": acquisition.datetime(2025, 1, 1, tzinfo=acquisition.timezone.utc)},
            policy_id=spec["policy"], limits=acquisition.AcquisitionLimits(**spec["limits"]),
            deadline=time.monotonic() + 20, ssl_context=ssl.create_default_context(cafile=spec["certificate"]),
            proxies={}, procutil=procutil,
        ) as acquired:
            acquired.snapshot.verify()
        cycles += 1
    result = {"cycles": cycles}
except BaseException:
    result = {"error": traceback.format_exc()}
pathlib.Path(spec["result"]).write_text(json.dumps(result))
""", encoding="utf-8")
    specification = {
        "engine": str(root / "libs" / "installer-engine"),
        "procutil": str(Path(procutil.__file__)), "workers": str(workers_path),
        "expected": [EXPECTED], "identity": IDENTITY, "origin": state["origin"],
        "policy": POLICY, "limits": vars(limits()), "certificate": str(CERTIFICATE),
        "result": str(result_path),
    }
    with observer._WindowObserver(*observer._configure_win32()) as windows:
        parent, job = procutil.spawn_sync_in_kill_on_close_job(
            [pythonw, "-I", "-S", "-B", str(script), json.dumps(specification)],
            env=os.environ.copy(), require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            parent.communicate(timeout=50)
            assert parent.returncode == 0
        finally:
            job.terminate_and_wait(10)
            parent.wait(timeout=10)
    assert json.loads(result_path.read_text()) == {"cycles": 2}
    workers = [json.loads(line) for line in workers_path.read_text().splitlines()]
    assert len(workers) == 2 and all(Path(item["executable"]).name.lower() == "python.exe" for item in workers)
    assert all(not _running(item["pid"]) for item in workers)
    assert len(state["requests"]) == 4
    assert windows.surfaced == {} and windows.focus_changes == {}
