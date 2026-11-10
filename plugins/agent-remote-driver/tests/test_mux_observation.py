"""Exercise the native wire boundary against a private synthetic session."""

from contextlib import ExitStack, contextmanager
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from agent_remote_driver import mux_creation, mux_observation as mux
from agent_remote_driver import mux_inventory
from agent_procutil import no_window_kwargs


_PANE = b"P\twt-fixture\t$7:100\t%2\t0\t1\tC:\\fixture with spaces\n"
_CLIENT = b"C\twt-fixture\n"
_IDENTITY = b"I\twt-fixture\t$7:100\t%2\n"

@pytest.fixture(autouse=True)
def private_native_inventory(monkeypatch):
    monkeypatch.setattr(mux_inventory, "observe_registrations", lambda **kw: {})


@contextmanager
def native_endpoint(root, response, *, delay=0, mutate=None, namespace="", session="wt-fixture"):
    responses = response if isinstance(response, tuple) else (response, b"OK\n", b"OK\n")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(.05)
        base = namespace + "__" + session if namespace else session
        (root / (base + ".port")).write_text(str(listener.getsockname()[1]), encoding="ascii")
        (root / (base + ".key")).write_text("synthetic-key", encoding="ascii")
        captured = []
        failures = []
        stop = threading.Event()

        def serve():
            try:
                for index, value in enumerate(responses):
                    while not stop.is_set():
                        try:
                            connection = listener.accept()[0]
                            break
                        except TimeoutError:
                            continue
                    else:
                        return
                    with connection as stream:
                        stream.settimeout(3)
                        reader = stream.makefile("rb")
                        captured.append(reader.readline())
                        captured.append(reader.readline())
                        if delay:
                            time.sleep(delay)
                        try:
                            stream.sendall(value)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        if mutate and index == len(responses) - 1:
                            mutate()
                        reader.close()
            except Exception as exc:
                failures.append(exc)

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            yield captured
        finally:
            stop.set()
            worker.join(timeout=4)
            assert not worker.is_alive()
            assert not failures


def test_fresh_authenticated_presence_and_real_client_count_without_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(mux_creation.subprocess, "run", lambda *a, **k: pytest.fail("passive CLI"))
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    with native_endpoint(
        tmp_path, (b"OK\n" + _PANE, b"OK\n" + _CLIENT * 3, b"OK\n" + _IDENTITY),
    ) as captured:
        observed = mux.observe_psmux("wt-fixture")
    assert observed.presence() and observed.clients == 3
    assert observed.incarnation == "$7:100"
    assert observed.panes == (mux.Pane("%2", False, True, "C:\\fixture with spaces"),)
    scheme = b"AU" + b"TH "
    assert captured[::2] == [scheme + b"synthetic-key\n"] * 3
    assert captured[1].startswith(b"list-panes ") and b" ; " not in captured[1]
    assert captured[3] == b'list-clients -F "C\t#{session_name}"\n'
    assert captured[5] == (
        b'display-message -p "I\t#{session_name}\t#{session_id}:#{session_created}\t#{pane_id}"\n'
    )
    assert (tmp_path / "wt-fixture.key").read_text() == "synthetic-key"


def test_zero_clients_is_live_not_absent(tmp_path):
    with native_endpoint(tmp_path, (b"OK\r\n" + _PANE, b"OK\n", b"OK\n" + _IDENTITY)):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path)
    assert observed.presence() and observed.clients == 0


@pytest.mark.parametrize("namespace", ["", "selected"])
def test_explicit_native_namespace_overrides_ambient_without_changing_it(tmp_path, monkeypatch, namespace):
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "foreign")
    with native_endpoint(
        tmp_path, (b"OK\n" + _PANE, b"OK\n", b"OK\n" + _IDENTITY),
        namespace=namespace,
    ):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path, namespace=namespace)
    assert observed.presence() and observed.incarnation == "$7:100"
    assert os.environ["COPILOT_DRIVER_PSMUX_NAMESPACE"] == "foreign"


def test_current_pane_is_selected_across_two_windows_with_active_panes(tmp_path):
    other_window = _PANE.replace(b"%2", b"%3")
    with native_endpoint(tmp_path, (
        b"OK\n" + _PANE + other_window, b"OK\n" + _CLIENT,
        b"OK\n" + _IDENTITY.replace(b"%2", b"%3"),
    )):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path)
    assert observed.presence()
    assert [p.pane_id for p in observed.panes if p.active] == ["%3"]


def test_explicit_namespace_scopes_listing_and_keeps_logical_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "private-fixture")
    (tmp_path / "other__wt-fixture.pid").write_text("123", encoding="ascii")
    (tmp_path / "unrelated.pid").write_text("456", encoding="ascii")
    with native_endpoint(tmp_path, b"OK\n" + _PANE + _IDENTITY, namespace="private-fixture"):
        observed = mux.list_psmux()
    assert set(observed) == {"wt-fixture"}
    assert observed["wt-fixture"].presence()
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", "../invalid")
    assert mux.list_psmux() is None
    assert mux.observe_psmux("wt-fixture").status == "unknown"


@pytest.mark.parametrize("namespace,session", [
    ("", "wt-fixture"), ("private-fixture", "wt-fixture"),
    ("", "wt-fixture__one"), ("private-fixture", "wt-fixture__one"),
])
def test_catalog_includes_live_inventory_published_after_the_directory_scan(
    tmp_path, monkeypatch, namespace, session,
):
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COPILOT_DRIVER_PSMUX_NAMESPACE", namespace)
    base = namespace + "__" + session if namespace else session
    wire_name = session.encode("utf-8")
    with ExitStack() as endpoints:
        def publish(**kwargs):
            assert not (tmp_path / (base + ".port")).exists()
            endpoints.enter_context(native_endpoint(
                tmp_path, tuple(
                    b"OK\n" + row.replace(b"wt-fixture", wire_name)
                    for row in (_PANE, _CLIENT, _IDENTITY)
                ), namespace=namespace, session=session,
            ))
            return {base: (session, 1), "unrelated__wt-other": ("wt-other", 2)}

        monkeypatch.setattr(mux_inventory, "observe_registrations", publish)
        observed = mux.list_psmux()
    assert set(observed) == {session}
    assert observed[session].presence() and observed[session].clients == 1


@pytest.mark.parametrize("session", ["wt-d\u00e8sktop-win", "wt-de\u0300sktop-win"])
def test_unicode_worktree_identity_reaches_the_finite_wire_consumer(
    tmp_path, monkeypatch, capsys, session,
):
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"version": 1, "session": session})))
    with native_endpoint(tmp_path, tuple(
        b"OK\n" + row.replace(b"wt-fixture", session.encode("utf-8"))
        for row in (_PANE, _CLIENT, _IDENTITY)
    ), session=session):
        assert mux.main(["--observe"]) == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["status"] == "present" and observed["clients"] == 1


@pytest.mark.parametrize("session", ["wt-a/b", "wt-a\\b", "wt-a\nb", "wt-a\0b"])
def test_native_identity_still_refuses_path_separators_and_controls(tmp_path, session):
    assert mux.observe_psmux(session, data_dir=tmp_path).status == "unsupported"


@pytest.mark.parametrize("response", [
    b"ERROR: Invalid session key\n",
    b"OK\n",
    b"OK\nERROR: unknown command\n",
    b"OK\n" + _PANE.replace(b"wt-fixture", b"wt-other") + _IDENTITY,
    b"OK\n" + _PANE + _IDENTITY.replace(b"$7:100", b"$8:101"),
    b"OK\n" + _PANE + _PANE + _IDENTITY,
    b"OK\n" + _PANE.replace(b"\t0\t1\t", b"\tbad\t1\t") + _IDENTITY,
    b"OK\n" + _PANE + _CLIENT,
    b"OK\n" + _PANE + _IDENTITY + _CLIENT,
    b"OK\n" + _PANE + _CLIENT.replace(b"wt-fixture", b"wt-other") + _IDENTITY,
    b"OK\n" + _PANE + _IDENTITY * 2,
    b"OK\n" + _PANE + _IDENTITY.replace(b"%2", b"%99"),
    b"OK\n" + b"x" * (mux._LIMIT + 1),
], ids=[
    "auth", "empty", "error", "name", "incarnation", "duplicate-pane", "malformed",
    "missing-end", "trailing-client", "foreign-client", "duplicate-end", "missing-current-pane",
    "oversized",
])
def test_invalid_or_unconfirmed_wire_never_means_absence(tmp_path, response):
    with native_endpoint(tmp_path, response):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path)
    assert observed.status == "unknown"
    with pytest.raises(mux.ObservationUnavailable):
        observed.presence()


def test_missing_and_partial_rendezvous_need_complete_coverage(tmp_path, monkeypatch):
    assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "absent"
    (tmp_path / "wt-fixture.pid").write_text("123", encoding="ascii")
    assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "absent"
    monkeypatch.setattr(mux_inventory, "observe_registrations", lambda **kw: {"wt-fixture": ("wt-fixture", 1)})
    assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "unknown"
    assert (tmp_path / "wt-fixture.pid").read_text() == "123"


def test_missing_registration_does_not_hide_a_surviving_server(tmp_path, monkeypatch):
    monkeypatch.setattr(mux_inventory, "observe_registrations", lambda **kw: {"wt-fixture": ("wt-fixture", 1)})
    assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "unknown"

    def incomplete(**kwargs):
        raise mux.ObservationUnavailable("A live mux server is missing its registration")

    monkeypatch.setattr(mux_inventory, "observe_registrations", incomplete)
    assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "unknown"


def test_missing_target_waits_for_another_servers_partial_publication_within_budget(tmp_path, monkeypatch):
    calls = []

    def publishing(**kwargs):
        calls.append(kwargs["timeout"])
        if len(calls) == 1:
            raise mux.ObservationUnavailable("A warm server is publishing")
        return {}

    monkeypatch.setattr(mux_inventory, "observe_registrations", publishing)
    monkeypatch.setattr(mux.time, "sleep", lambda delay: None)
    assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "absent"
    assert len(calls) == 2 and 0 < calls[1] <= calls[0] <= 1


@pytest.mark.skipif(os.name != "nt", reason="Native Windows process identity")
def test_native_coverage_identity_reads_its_own_process_without_a_subprocess(monkeypatch):
    from agent_remote_driver import mux_process_identity

    monkeypatch.setattr(mux_creation.subprocess, "run", lambda *a, **k: pytest.fail("Native identity child"))
    before = mux_process_identity.process_start_time(os.getpid())
    assert before and before.isdecimal()
    assert mux_process_identity.command_line(os.getpid())
    assert mux_process_identity.process_start_time(os.getpid()) == before

def test_timeout_and_changed_rendezvous_preserve_unknown(tmp_path):
    started = time.monotonic()
    with native_endpoint(tmp_path, b"OK\n" + _PANE + _IDENTITY, delay=0.15):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path, timeout=0.05)
    assert observed.status == "unknown" and time.monotonic() - started < 2
    with native_endpoint(
        tmp_path, b"OK\n" + _PANE + _IDENTITY,
        mutate=lambda: (tmp_path / "wt-fixture.key").write_text("replacement", encoding="ascii"),
    ):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path)
    assert observed.status == "unknown"


def test_separate_native_queries_share_one_deadline_and_authenticate_every_reply(tmp_path):
    replies = (b"OK\n" + _PANE, b"OK\n" + _CLIENT, b"OK\n" + _IDENTITY)
    started = time.monotonic()
    with native_endpoint(tmp_path, replies, delay=.04):
        observed = mux.observe_psmux("wt-fixture", data_dir=tmp_path, timeout=.06)
    assert observed.status == "unknown" and time.monotonic() - started < 1
    with native_endpoint(tmp_path, (replies[0], b"ERROR: refused\n", replies[2])):
        assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "unknown"


def test_separate_native_replies_share_one_response_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(mux, "_LIMIT", 128)
    replies = (b"OK\n" + _PANE, b"OK\n" + _CLIENT * 6, b"OK\n" + _IDENTITY)
    with native_endpoint(tmp_path, replies):
        assert mux.observe_psmux("wt-fixture", data_dir=tmp_path).status == "unknown"


def test_creation_check_joins_from_wire_without_native_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(mux_creation.subprocess, "run", lambda *a, **k: pytest.fail("passive CLI"))
    with native_endpoint(tmp_path, b"OK\n" + _PANE + _IDENTITY):
        assert mux_creation.has_session("psmux.exe", "wt-fixture")
    (tmp_path / "wt-fixture.port").write_text("invalid", encoding="ascii")
    with pytest.raises(mux.ObservationUnavailable):
        mux_creation.has_session("psmux.exe", "wt-fixture")


def test_finite_cli_keeps_unknown_and_invalid_requests_explicit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PSMUX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"version":1,"session":"wt-fixture"}'))
    assert mux.main(["--observe"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "absent"
    monkeypatch.setattr("sys.stdin", io.StringIO('{"version":1,"session":"../other"}'))
    assert mux.main(["--observe"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "unsupported"
    with pytest.raises(SystemExit) as exc:
        mux.main(["--observe", "--typo"])
    assert exc.value.code == 2


def test_finite_file_entrypoint_retains_inventory_error_identity(tmp_path):
    bootstrap = """
import runpy, sys
from agent_remote_driver import mux_inventory
from agent_remote_driver.mux_observation_types import ObservationUnavailable
def unavailable(**kwargs):
    raise ObservationUnavailable("synthetic incomplete native coverage")
mux_inventory.observe_registrations = unavailable
sys.argv = [sys.argv[1], "--observe"]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap, mux.__file__],
        input='{"version":1,"session":"wt-fixture"}', capture_output=True, text=True,
        env=dict(os.environ, PSMUX_DATA_DIR=str(tmp_path)), timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["status"] == "unknown"
