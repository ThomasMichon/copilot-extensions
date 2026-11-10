"""Exercise the actual native fixture command boundary without launching a mux."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from headed_mux_fixture import FixtureMux
import plugin_test_command_safety as command_safety
from plugin_test_command_safety import UnsafeTestCommand, command_problem


def test_every_fixture_operation_names_its_private_mux_namespace_and_exact_target(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kwargs: observed.append((argv, kwargs)) or subprocess.CompletedProcess(argv, 0),
    )
    fixture = FixtureMux("psmux.exe", tmp_path, {
        "PATH": "fixture-path", "TMUX": "productive", "PSMUX_SESSION": "productive",
        "PSMUX_TARGET_SESSION": "productive", "PSMUX_DEFAULT_SESSION": "productive",
    })
    for action, arguments in (
        ("new-session", ("-d", "-s", fixture.target, "--", "pwsh", "-File", "fixture.ps1")),
        ("has-session", ("-t", fixture.target)),
        ("display-message", ("-p", "-t", fixture.target, "#{session_id}")),
        ("capture-pane", ("-p", "-t", fixture.target)),
        ("send-keys", ("-t", fixture.target, "-l", "--", "isolated native input")),
        ("kill-session", ("-t", fixture.target)),
    ):
        fixture.query(action, *arguments)
    assert len(observed) == 6
    for argv, options in observed:
        assert argv[:3] == ["psmux.exe", *fixture.mux_args]
        assert command_problem(argv, sandbox=fixture.sandbox, cwd=tmp_path) is None
        assert fixture.target in argv
        assert options["env"] == {"PATH": "fixture-path"}
        assert options["cwd"] == tmp_path


@pytest.mark.parametrize("action,arguments", [
    ("kill-server", ()), ("server", ()), ("list-sessions", ()),
    ("kill-session", ()), ("kill-session", ("-t", "productive")),
    ("has-session", ("-t", "productive")),
    ("send-keys", ("-t", "productive", "Enter")),
    ("new-session", ("-s", "productive")),
])
def test_unsafe_fixture_commands_refuse_before_any_process(tmp_path, monkeypatch, action, arguments):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must refuse before spawn"))
    fixture = FixtureMux("psmux.exe", tmp_path, {})
    with pytest.raises(ValueError):
        fixture.query(action, *arguments)


def test_fixture_route_override_and_missing_namespace_refuse_before_any_process(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must refuse before spawn"))
    fixture = FixtureMux("psmux.exe", tmp_path, {})
    with pytest.raises(ValueError):
        fixture.query("kill-session", "-t", fixture.target, "-L", "default")
    fixture.mux_args = []
    with pytest.raises(UnsafeTestCommand):
        fixture.query("kill-session", "-t", fixture.target)


def test_raw_child_flags_are_not_mux_route_options(tmp_path, monkeypatch):
    observed = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kwargs: observed.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    fixture = FixtureMux("psmux.exe", tmp_path, {})
    child = [sys.executable, "-I", "-S", "-c", "pass"]
    fixture.query("new-session", "-s", fixture.target, "--", *child)
    assert observed == [[
        "psmux.exe", *fixture.mux_args, "new-session", "-s", fixture.target, "--", *child,
    ]]
    with pytest.raises(ValueError):
        fixture.query("new-session", "-s", fixture.target, "-S", "foreign", "--", *child)


@pytest.mark.parametrize("route", [
    ["-L", "default"], ["-L", "driver-test-" + "a" * 32],
    ["-S", "foreign.sock"],
])
def test_fixture_rejects_noncanonical_mux_route_before_spawn(tmp_path, monkeypatch, route):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must refuse before spawn"))
    fixture = FixtureMux("psmux.exe", tmp_path, {})
    fixture.mux_args = (
        ["-S", str(fixture.sandbox.parent / "foreign.sock")]
        if route[0] == "-S" else route
    )
    with pytest.raises(UnsafeTestCommand):
        fixture.query("has-session", "-t", fixture.target)


def test_posix_socket_scope_distinguishes_owned_relative_and_foreign_absolute_paths(tmp_path, monkeypatch):
    fixture = FixtureMux("tmux", tmp_path, {})
    monkeypatch.setattr(command_safety, "os", SimpleNamespace(
        name="posix", path=os.path, fsdecode=os.fsdecode, environ=os.environ,
    ))
    assert command_problem(
        ["tmux", "-S", "owned.sock", "has-session", "-t", fixture.target],
        sandbox=fixture.sandbox, cwd=tmp_path,
    ) is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must refuse before spawn"))
    fixture.mux_args = ["-S", str(fixture.sandbox.parent / "foreign.sock")]
    with pytest.raises(UnsafeTestCommand):
        fixture.query("has-session", "-t", fixture.target)
