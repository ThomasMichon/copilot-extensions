"""Finite mux parser controls, without a live user mux."""

import io
import json
import subprocess
from contextlib import contextmanager

import pytest

from agent_remote_driver import mux_creation as mux


def _request():
    return {
        "version": 1, "binary": "psmux.exe", "session": "wt-fixture",
        "argv": ["new-session", "-d", "-s", "wt-fixture", "-c", "a directory",
                 "child", "", 'quote "inside"', "literal\nline", "tail\\"],
    }


@pytest.mark.parametrize("system, operation", [
    ("Windows", "windows-psmux-create-or-join"), ("Linux", "posix-tmux-create-or-join"),
])
def test_contract_query_has_no_native_effects(monkeypatch, capsys, system, operation):
    monkeypatch.setattr(mux.platform, "system", lambda: system)
    monkeypatch.setattr(mux, "run_or_join", lambda *a, **k: pytest.fail("read-only query"))
    assert mux.main(["--describe-contract"]) == 0
    declaration = json.loads(capsys.readouterr().out)
    assert declaration["contract"] == mux.CONTRACT
    assert declaration["operations"] == [operation]


@pytest.mark.parametrize("outcome", ["created", "joined", "refused", "lost"])
@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_cli_delegates_literal_creation_and_preserves_possible_dispatch(
    monkeypatch, capsys, outcome, system,
):
    request = _request()
    if system != "Windows":
        request["binary"] = "/usr/bin/tmux"
    monkeypatch.setattr(mux.sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(mux.platform, "system", lambda: system)

    def run(argv, *, binary, session, prepare):
        assert argv == [request["binary"], *request["argv"]]
        assert binary == request["binary"] and session == request["session"]
        if outcome == "joined":
            return None
        if outcome == "refused":
            raise RuntimeError("private query details")
        prepare()
        if outcome == "lost":
            raise subprocess.TimeoutExpired(["private literal"], 15)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(mux, "run_or_join", run)
    assert mux.main(["--create"]) == (0 if outcome in ("created", "joined") else 3)
    reply = json.loads(capsys.readouterr().out)
    assert reply["status"] == {"refused": "rejected", "lost": "unknown"}.get(outcome, outcome)
    assert set(reply) == {"contract", "version", "status"}


@pytest.mark.parametrize("exists, code", [(True, 0), (False, 0), (False, 17)])
def test_posix_creation_checks_before_preparing_and_preserves_dispatched_failure(monkeypatch, exists, code):
    monkeypatch.setattr(mux.platform, "system", lambda: "Linux")
    calls = []

    def query(binary, session):
        assert binary == "/usr/bin/tmux" and session == "=literal.session"
        assert calls == []
        return exists

    def prepare():
        calls.append("prepare")
        return ["new-session", "literal arguments"]

    monkeypatch.setattr(mux, "has_session", query)
    monkeypatch.setattr(mux, "run", lambda argv: subprocess.CompletedProcess(argv, code))
    command = []
    if exists:
        assert mux.run_or_join(
            command, binary="/usr/bin/tmux", session="literal.session", prepare=prepare,
        ) is None
        assert calls == []
    elif code:
        with pytest.raises(mux.CreationUncertain):
            mux.run_or_join(command, binary="/usr/bin/tmux", session="literal.session", prepare=prepare)
        assert calls == ["prepare"]
    else:
        result = mux.run_or_join(command, binary="/usr/bin/tmux", session="literal.session", prepare=prepare)
        assert result.returncode == 0 and result.args == ["new-session", "literal arguments"]
        assert calls == ["prepare"]


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_admitted_join_does_not_query_or_prepare_another_native_command(monkeypatch, system):
    @contextmanager
    def joined(*args, **kwargs):
        yield False

    def forbidden(*args, **kwargs):
        pytest.fail("A joined gate must not query or prepare another native command")

    monkeypatch.setattr(mux.platform, "system", lambda: system)
    monkeypatch.setattr(mux, "session_gate", joined)
    monkeypatch.setattr(mux, "has_session", forbidden)
    monkeypatch.setattr(mux, "run", forbidden)
    assert mux.run_or_join(
        [], binary="psmux" if system == "Windows" else "tmux", session="wt-fixture", prepare=forbidden,
    ) is None


def test_dispatched_creation_waits_for_partial_rendezvous_without_replay(monkeypatch):
    class Child:
        returncode = None
        waits = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits < 3:
                raise subprocess.TimeoutExpired("synthetic mux", timeout)
            self.returncode = 0

        def kill(self):
            pytest.fail("A publishing server must retain its creation client")

    child = Child()
    starts = []
    releases = []

    def start(argv, **kwargs):
        starts.append(argv)
        return child

    def present(*args, **kwargs):
        if child.waits == 1:
            raise mux.ObservationUnavailable("publishing")
        return True

    monkeypatch.setattr(mux.subprocess, "Popen", start)
    monkeypatch.setattr(mux, "has_session", present)
    argv = ["psmux.exe", "new-session", "-d", "-s", "wt-fixture"]
    result = mux.run_session(argv, "wt-fixture", lambda: releases.append(True))
    assert result.returncode == 0
    assert starts == [argv] and releases == [True] and child.waits == 3


def test_unknown_presence_cannot_admit_creation_after_acquiring_the_gate(monkeypatch):
    import single_instance_lease

    class Lease:
        def __init__(self, *args, **kwargs):
            pass

        def acquire(self):
            pass

        def release(self):
            pass

    def unknown():
        raise mux.ObservationUnavailable("unconfirmed native session")

    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    with pytest.raises(mux.ObservationUnavailable):
        with mux.session_gate("wt-fixture", exists=unknown, enabled=True):
            pytest.fail("Unknown presence admitted another creation")


@pytest.mark.parametrize("invalid", [
    "duplicate", "oversized", "version", "target", "command", "nul", "extra", "type",
])
def test_invalid_requests_never_query_or_create(monkeypatch, capsys, invalid):
    request = _request()
    if invalid == "version":
        request["version"] = True
    elif invalid == "target":
        request["argv"][3] = "other"
    elif invalid == "command":
        request["argv"][0] = "kill-server"
    elif invalid == "nul":
        request["argv"].append("\0")
    elif invalid == "extra":
        request["extra"] = True
    elif invalid == "type":
        request["argv"].append(1)
    raw = json.dumps(request)
    if invalid == "duplicate":
        raw = raw[:-1] + ', "session": "other"}'
    elif invalid == "oversized":
        raw = " " * (1024 * 1024 + 1)
    monkeypatch.setattr(mux.sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(mux.platform, "system", lambda: "Windows")
    monkeypatch.setattr(mux, "run_or_join", lambda *a, **k: pytest.fail("invalid request"))
    assert mux.main(["--create"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


def test_unknown_cli_argument_is_rejected(monkeypatch):
    monkeypatch.setattr(mux, "run_or_join", lambda *a, **k: pytest.fail("unknown argument"))
    with pytest.raises(SystemExit) as error:
        mux.main(["--create", "--typo"])
    assert error.value.code == 2


@pytest.mark.parametrize("outcome", ["created", "failed", "malformed", "timeout"])
def test_private_creation_uses_one_literal_socket_command_and_never_joins(
    tmp_path, monkeypatch, capsys, outcome,
):
    binary = tmp_path / "tmux"
    binary.touch()
    request = {
        "version": 1, "binary": str(binary), "socket": str(tmp_path / "tmux.sock"),
        "session": "wt-fixture", "cwd": str(tmp_path),
        "command": ["child", "", 'quote "inside"', "literal\nline", "tail\\"],
    }
    monkeypatch.setattr(mux.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mux.sys, "stdin", io.StringIO(json.dumps(request)))
    monkeypatch.setattr(mux, "run_or_join", lambda *a, **k: pytest.fail("No default-server join"))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["cwd"] == str(tmp_path) and kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 30
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("private literal must not escape", 30)
        return subprocess.CompletedProcess(
            argv, 17 if outcome == "failed" else 0,
            "private diagnostic" if outcome == "malformed" else "$1:1234|%2\n",
        )

    monkeypatch.setattr(mux.subprocess, "run", run)
    assert mux.private_main() == (0 if outcome == "created" else 3)
    assert calls == [[
        str(binary), "-S", request["socket"], "new-session", "-d", "-s", "wt-fixture",
        "-P", "-F", "#{session_id}:#{session_created}|#{pane_id}", "-c", str(tmp_path),
        *request["command"],
    ]]
    reply = json.loads(capsys.readouterr().out)
    assert reply == {
        "contract": mux.CONTRACT, "version": 1,
        "status": "created" if outcome == "created" else "unknown",
        **({"native": {"incarnation": "$1:1234", "pane": "%2"}} if outcome == "created" else {}),
    }


@pytest.mark.parametrize("invalid", [
    "windows", "duplicate", "oversized", "version", "extra", "command", "nul",
    "session", "cwd", "binary", "socket", "linked-socket",
])
def test_private_refusal_precedes_any_native_effect(tmp_path, monkeypatch, capsys, invalid):
    binary = tmp_path / "tmux"
    binary.touch()
    request = {
        "version": 1, "binary": str(binary), "socket": str(tmp_path / "tmux.sock"),
        "session": "wt-fixture", "cwd": str(tmp_path), "command": ["child"],
    }
    if invalid == "version":
        request["version"] = True
    elif invalid == "extra":
        request["extra"] = True
    elif invalid == "command":
        request["command"] = []
    elif invalid == "nul":
        request["command"] = ["child", "\0"]
    elif invalid == "session":
        request["session"] = "-other"
    elif invalid == "cwd":
        request["cwd"] = "relative"
    elif invalid == "binary":
        request["binary"] = str(tmp_path / "other")
    elif invalid == "socket":
        (tmp_path / "tmux.sock").touch()
    elif invalid == "linked-socket":
        monkeypatch.setattr(type(tmp_path), "is_symlink", lambda path: path.name == "tmux.sock")
    raw = json.dumps(request)
    if invalid == "duplicate":
        raw = raw[:-1] + ', "session": "other"}'
    elif invalid == "oversized":
        raw = " " * (1024 * 1024 + 1)
    monkeypatch.setattr(mux.platform, "system", lambda: "Windows" if invalid == "windows" else "Linux")
    monkeypatch.setattr(mux.sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(mux.subprocess, "run", lambda *a, **k: pytest.fail("Invalid private request"))
    assert mux.private_main() == 3
    assert json.loads(capsys.readouterr().out) == {
        "contract": mux.CONTRACT, "version": 1, "status": "rejected",
    }
