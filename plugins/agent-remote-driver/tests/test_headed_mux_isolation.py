"""Exercise the actual native fixture command boundary without launching a mux."""

import subprocess

import pytest

from headed_mux_fixture import FixtureMux


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
        ("kill-session", ("-t", fixture.target)),
    ):
        fixture.query(action, *arguments)
    assert len(observed) == 5
    for argv, options in observed:
        assert argv[:3] == ["psmux.exe", "-L", fixture.namespace]
        assert fixture.target in argv
        assert options["env"] == {"PATH": "fixture-path"}
        assert options["cwd"] == tmp_path


@pytest.mark.parametrize("action,arguments", [
    ("kill-server", ()), ("server", ()), ("list-sessions", ()),
    ("kill-session", ()), ("kill-session", ("-t", "productive")),
    ("has-session", ("-t", "productive")),
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
    fixture.namespace = ""
    with pytest.raises(ValueError):
        fixture.query("kill-session", "-t", fixture.target)
