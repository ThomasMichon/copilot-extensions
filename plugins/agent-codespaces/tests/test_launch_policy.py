"""The host-registered launch policy every CodeSpace worker launch asks first."""

from __future__ import annotations

import argparse
import os
import json
import sys

import pytest

from agent_codespaces import copilot_venue, launch_policy as lp

_REAL_BRIDGE_ENFORCEMENT = lp.bridge_enforcement  # the conftest stubs it per test


def _policy(code: str, *, timeout: float = 20.0) -> None:
    """Register a Python one-liner as the policy (it reads the request on stdin)."""
    lp.register([sys.executable, "-c", code], timeout=timeout)


def test_nothing_registered_allows():
    assert lp.refusal("cs") is None


def test_the_policy_gets_the_request_and_can_allow():
    _policy("import json,sys; r=json.load(sys.stdin); "
            "print(json.dumps({'refuse': None if r == {'schema': 1, 'venue': 'codespace', "
            "'codespace': 'cs'} else 'unexpected request'}))")
    assert lp.refusal("cs") is None


def test_a_refusal_is_its_one_line_reason():
    _policy("print('{\"refuse\": \"the operator  paused\\\\nthe worker\"}')")
    assert lp.refusal("cs") == "the operator paused the worker"


@pytest.mark.parametrize("code,needle", [
    ("import sys; sys.stderr.write('boom'); sys.exit(3)", "exit 3"),
    ("print('not json')", "other than JSON"),
    ("print('{\"allow\": true}')", 'no "refuse" field'),
    ("print('{\"refuse\": \"  \"}')", "without a reason"),
    ("import time; time.sleep(5)", "timed out"),
])
def test_a_policy_that_cannot_answer_fails_closed(code, needle):
    _policy(code, timeout=0.5 if "sleep" in code else 20.0)
    reason = lp.refusal("cs")
    assert reason and needle in reason


_ABS = json.dumps(sys.executable)  # an absolute command, so only the field under test is invalid


@pytest.mark.parametrize("content", ["{", '{"argv": []}', '{"argv": [%s], "timeout": 0}' % _ABS,
                                     '{"argv": [%s], "timeout": 90}' % _ABS, '{"argv": ["x\\u0000y"]}', '{"argv": ["relative"]}'])
def test_an_unreadable_registration_fails_closed(content):
    lp.POLICY_FILE.write_text(content, encoding="utf-8")
    assert "unreadable" in lp.refusal("cs")


def test_an_interrupted_check_kills_the_policy_tree(monkeypatch):
    import io

    killed = []

    class Proc:
        pid, returncode = 4242, None
        stdin, stdout, stderr = io.BytesIO(), io.BufferedReader(io.BytesIO()), io.BufferedReader(io.BytesIO())

        def poll(self):
            raise KeyboardInterrupt

        def wait(self, timeout=None):
            return -9

        def kill(self):
            killed.append("proc")

    class Job:
        def close(self):
            killed.append("job")

    monkeypatch.setattr(lp, "spawn_sync_in_kill_on_close_job", lambda argv, **kw: (Proc(), Job()))
    monkeypatch.setattr(lp.os, "killpg", lambda pid, sig: killed.append("group"), raising=False)
    with pytest.raises(KeyboardInterrupt):
        lp._run_contained(["x"], "{}", 7.0)
    assert "proc" in killed and ("job" in killed or "group" in killed)


def test_a_symlinked_registration_fails_closed_even_when_dangling(tmp_path):
    try:
        lp.POLICY_FILE.symlink_to(tmp_path / "nowhere.json")
    except OSError:
        pytest.skip("symlinks need privileges on this platform")
    assert "symlink" in lp.refusal("cs")


def test_a_spawn_value_error_is_a_refusal_not_a_crash(monkeypatch):
    lp.register([sys.executable])

    def bad(*_a, **_k):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(lp, "_run_contained", bad)
    assert "could not run" in lp.refusal("cs")


def test_a_registration_that_is_not_utf8_fails_closed():
    lp.POLICY_FILE.write_bytes(b'{"argv": ["\xff\xfe"]}')
    assert "unreadable" in lp.refusal("cs")
    assert lp.refused_exit_code("cs") == lp.LAUNCH_REFUSED_EXIT


def test_a_registration_that_is_not_a_regular_file_fails_closed():
    lp.POLICY_FILE.mkdir()
    assert "not a regular file" in lp.refusal("cs")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
def test_a_fifo_registration_is_refused_without_blocking():
    os.mkfifo(lp.POLICY_FILE)
    assert "not a regular file" in lp.refusal("cs")


def test_a_registration_probe_error_fails_closed(monkeypatch):
    real_lstat = os.lstat

    def lstat(path, *a, **k):
        if str(path) == str(lp.POLICY_FILE):
            raise PermissionError("no traverse permission")
        return real_lstat(path, *a, **k)

    monkeypatch.setattr(lp.os, "lstat", lstat)
    assert "unreadable" in lp.refusal("cs")


def test_a_timeout_kills_the_whole_policy_tree():
    """A policy whose descendant keeps the pipes open must not keep the launch
    waiting for EOF after the timeout."""
    import time

    grandchild = "import time; time.sleep(30)"
    _policy(f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); "
            "time.sleep(30)", timeout=1.0)
    started = time.monotonic()
    reason = lp.refusal("cs")
    assert "timed out" in reason and time.monotonic() - started < 15


def test_copilot_launch_is_refused_before_any_claim(monkeypatch, capsys):
    claimed = []
    monkeypatch.setattr("agent_codespaces.lease.claim_for_connect", lambda *a, **k: claimed.append(a))
    _policy("print('{\"refuse\": \"paused\"}')")
    rc = copilot_venue.claim_or_exit_code(argparse.Namespace(name="cs", force_claim=False, effort=None))
    assert rc == lp.LAUNCH_REFUSED_EXIT == 79 and claimed == []
    assert "paused" in capsys.readouterr().err


def test_copilot_launch_proceeds_to_the_claim_when_allowed(monkeypatch):
    claimed = []
    monkeypatch.setattr("agent_codespaces.lease.claim_for_connect", lambda *a, **k: claimed.append(a))
    _policy("print('{\"refuse\": null}')")
    assert copilot_venue.claim_or_exit_code(argparse.Namespace(name="cs", force_claim=False, effort=None)) is None
    assert claimed == [("cs",)]


def _cli(argv):
    parser = argparse.ArgumentParser()
    lp.add_launch_policy_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(argv)
    return args.func(args)


def test_cli_set_show_check_clear(capsys):
    assert _cli(["launch-policy", "set", "--timeout", "5", "--", sys.executable, "-c",
                 "print('{\"refuse\": \"held\"}')"]) == 0
    capsys.readouterr()
    assert _cli(["launch-policy", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["policy"]["timeout"] == 5.0
    assert _cli(["launch-check", "cs", "--json"]) == 79
    assert json.loads(capsys.readouterr().out) == {"codespace": "cs", "refuse": "held"}
    assert _cli(["launch-policy", "clear"]) == 0
    assert _cli(["launch-check", "cs"]) == 0


def test_cli_set_needs_a_command(capsys):
    assert _cli(["launch-policy", "set"]) == 2


def test_an_empty_argument_after_the_command_is_valid():
    """`set -- policy ""` must not persist a registration every check then
    treats as unreadable."""
    _policy("import sys; print('{\"refuse\": null}' if sys.argv[1:] == [''] else '{\"refuse\": \"bad\"}')")
    lp.register([*lp.registered()["argv"], ""])
    assert lp.refusal("cs") is None


def test_an_empty_command_is_rejected_by_set(capsys):
    assert _cli(["launch-policy", "set", "--", ""]) == 2
    assert not lp.POLICY_FILE.exists()


def test_a_runaway_policy_output_is_refused_not_buffered():
    import time

    _policy("import sys\nwhile True: sys.stdout.write('x' * 65536)", timeout=30.0)
    started = time.monotonic()
    reason = lp.refusal("cs")
    assert "KiB" in reason and time.monotonic() - started < 15


def test_set_and_show_never_echo_the_arguments(capsys):
    assert _cli(["launch-policy", "set", "--", sys.executable, "--token=s3cret"]) == 0
    assert _cli(["launch-policy", "show"]) == 0
    assert _cli(["launch-policy", "show", "--json"]) == 0
    out = capsys.readouterr().out
    assert "s3cret" not in out and "+1 arguments" in out


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_a_registration_in_a_directory_others_can_write_is_refused():
    import os

    lp.register([sys.executable])
    os.chmod(lp.POLICY_FILE.parent, 0o777)
    try:
        assert "only this user controls" in lp.refusal("cs")
        lp.register([sys.executable])  # a write tightens it again
        assert os.stat(lp.POLICY_FILE.parent).st_mode & 0o077 == 0
    finally:
        os.chmod(lp.POLICY_FILE.parent, 0o700)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PATHEXT shim resolution")
def test_a_bare_command_resolves_to_its_windows_shim(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "my-policy.cmd").write_text('@echo {"refuse": "held by shim"}\r\n', encoding="ascii")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    assert _cli(["launch-policy", "set", "--", "my-policy"]) == 0  # set pins the shim's absolute path
    assert lp.refusal("cs") == "held by shim"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_registration_is_owner_only():
    import os
    import stat

    old = os.umask(0o022)
    try:
        lp.register([sys.executable, "--token", "secret"])
    finally:
        os.umask(old)
    assert stat.S_IMODE(lp.POLICY_FILE.stat().st_mode) == 0o600


def test_cli_set_caps_the_timeout_below_the_bridge_budget(capsys):
    assert _cli(["launch-policy", "set", "--timeout", "90", "--", sys.executable]) == 2
    assert lp.registered() is None
    with pytest.raises(ValueError):
        lp.register([sys.executable], timeout=lp.MAX_TIMEOUT + 1)


def test_cli_reports_a_resident_bridge_that_would_skip_the_policy(monkeypatch, capsys):
    monkeypatch.setattr(lp, "bridge_enforcement", lambda: False)
    assert _cli(["launch-policy", "set", "--", sys.executable]) == 0
    assert "predates launch-policy enforcement" in capsys.readouterr().err
    assert _cli(["launch-policy", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["bridge_enforces"] is False


@pytest.mark.parametrize("health,expected", [
    ({"protocol_version": 25}, True), ({"protocol_version": 24}, False), ({}, False)])
def test_bridge_enforcement_reads_the_daemon_protocol(monkeypatch, health, expected):
    import venue_copilot

    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda *a, **k: 4242)
    monkeypatch.setattr(venue_copilot, "_daemon_health", lambda port: health)
    assert _REAL_BRIDGE_ENFORCEMENT() is expected


def test_bridge_enforcement_is_unknown_without_a_daemon(monkeypatch):
    import venue_copilot

    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda *a, **k: None)
    assert _REAL_BRIDGE_ENFORCEMENT() is None


def test_a_relative_command_is_never_registered_or_run():
    with pytest.raises(ValueError):
        lp.register(["./policy"])
    lp.POLICY_FILE.write_text('{"argv": ["policy"]}', encoding="utf-8")  # a hand-edited registration
    assert "unreadable" in lp.refusal("cs")


def test_the_policy_runs_from_the_registration_directory():
    """The launcher and the resident bridge run in different directories: both
    run the policy from one place, so a relative argument means the same file."""
    _policy("import os, json; print(json.dumps({'refuse': os.getcwd()}))")
    assert os.path.normcase(lp.refusal("cs")) == os.path.normcase(str(lp.POLICY_FILE.parent))


def test_cli_set_pins_a_bare_command_to_its_absolute_path(capsys):
    name = os.path.basename(sys.executable)
    assert _cli(["launch-policy", "set", "--", os.path.splitext(name)[0], "-c", "pass"]) == 0
    assert os.path.isabs(lp.registered()["argv"][0])
