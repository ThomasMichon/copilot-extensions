"""Machine-only typed renewal across the native child-process boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from agent_worktrees import pr_cli, pr_credential_command
from agent_worktrees.pr_credential_command import command_source
from agent_worktrees.providers.observation_credentials import (
    ObservationCredentialError, ObservationCredentials,
)


def envelope():
    return {
        "schema": "fleet-manager.forge-credential", "version": 1,
        "token": "synthetic-command-secret", "expires_at": int(time.time()) + 30,
        "forge": {
            "provider": "github", "api_base": "https://api.github.com",
            "repository": "owner/repository", "permission": "observe", "authentication": "bearer",
        },
    }


def credentials(source):
    return ObservationCredentials(
        source, provider="github", api_base="https://api.github.com", repository="owner/repository",
    )


def test_explicit_command_renews_with_bounded_captured_streams(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        value = envelope() | {"token": f"synthetic-command-{len(calls)}"}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(pr_credential_command, "run_caller_owned", run)
    source = command_source(json.dumps([sys.executable, "-m", "fixture"]))
    first, second = credentials(source).acquire(), credentials(source).acquire()
    assert first.token != second.token
    assert first.forge == second.forge
    assert "synthetic-command" not in repr(first)
    assert calls == [([sys.executable, "-m", "fixture"], {
        "timeout": 15, "max_stdout_bytes": 65536, "max_stderr_bytes": 8192,
    })] * 2


@pytest.mark.parametrize("serialized", [
    "not-json", "{}", "[]", '["relative-command"]', '[""]', '["x", null]', '["x", "\\u0000"]',
    "[" * 2000, json.dumps([sys.executable, "x" * 16384]),
    json.dumps([sys.executable] * 65),
])
def test_invalid_command_refuses_before_spawn(monkeypatch, serialized):
    monkeypatch.setattr(pr_credential_command, "run_caller_owned",
                        lambda *_args, **_kwargs: pytest.fail("invalid command launched"))
    with pytest.raises(ValueError, match="absolute credential command"):
        command_source(serialized)


@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_implicit_shell_scripts_are_not_credential_executables(tmp_path, suffix):
    script = tmp_path / ("credential" + suffix)
    script.touch()
    with pytest.raises(ValueError, match="absolute credential command"):
        command_source(json.dumps([str(script)]))


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"version": True}, {"version": 2}, {"forge": None},
    {"forge": {}}, {"extra": "unknown"}, {"token": ""},
    {"expires_at": float("inf")}, {"expires_at": True}, {"expires_at": 0},
    {"forge": {"provider": "github"}},
])
def test_invalid_envelope_cannot_become_provider_material(monkeypatch, change):
    monkeypatch.setattr(
        pr_credential_command, "run_caller_owned",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0, json.dumps(envelope() | change), "private-stderr",
        ),
    )
    with pytest.raises(ObservationCredentialError) as failure:
        credentials(command_source(json.dumps([sys.executable]))).acquire()
    assert "synthetic-command" not in str(failure.value) and "private-stderr" not in str(failure.value)


@pytest.mark.parametrize("stdout,returncode", [
    ('{"token":"synthetic-command-secret","token":"other"}', 0),
    ("synthetic-command-secret", 0), ("x" * 65537, 0), (json.dumps(envelope()), 1),
], ids=["duplicate", "non-json", "oversize", "failed-exit"])
def test_command_failure_is_sanitized_and_has_no_fallback(monkeypatch, stdout, returncode):
    monkeypatch.setattr(
        pr_credential_command, "run_caller_owned",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, returncode, stdout, "private-stderr"),
    )
    with pytest.raises(ObservationCredentialError, match="command unavailable") as failure:
        command_source(json.dumps([sys.executable]))()
    assert "synthetic-command" not in str(failure.value)


@pytest.mark.parametrize("phase", ["timeout", "output-limit"])
def test_owned_process_errors_do_not_disclose_captured_secrets(monkeypatch, phase):
    def fail(*args, **kwargs):
        if phase == "timeout":
            raise subprocess.TimeoutExpired("fixture", 15, "synthetic-command-secret", "private-stderr")
        raise subprocess.SubprocessError("synthetic-command-secret")

    monkeypatch.setattr(pr_credential_command, "run_caller_owned", fail)
    with pytest.raises(ObservationCredentialError, match="command unavailable") as failure:
        command_source(json.dumps([sys.executable]))()
    assert "synthetic-command" not in str(failure.value)


def test_conflicting_token_is_rejected_before_configuration(monkeypatch, capsys):
    monkeypatch.setattr(pr_cli.cfg, "load_config", lambda *_: pytest.fail("configuration accessed"))
    assert pr_cli.cmd_pr_watch_dispatch([
        "snapshot", "owner/repository", "7", "--credential-command", json.dumps([sys.executable]),
        "--token", "synthetic-explicit-secret",
    ]) == 2
    captured = capsys.readouterr()
    assert "synthetic-explicit-secret" not in captured.out + captured.err
    assert "cannot be combined" in captured.out + captured.err


def test_unconfirmed_cleanup_retains_the_original_process_custody(monkeypatch):
    failure = OwnedCleanupError(object())

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(pr_credential_command, "run_caller_owned", fail)
    with pytest.raises(OwnedCleanupError) as retained:
        command_source(json.dumps([sys.executable]))()
    assert retained.value is failure


@pytest.mark.parametrize("failure", [None, "disconnect", "scope"])
def test_real_snapshot_child_renews_command_per_native_request(tmp_path, failure):
    helper = tmp_path / "credential.py"
    counter = tmp_path / "acquisitions"
    requested = tmp_path / "requests"
    helper.write_text(
        "import json,pathlib,sys,time\n"
        f"path=pathlib.Path({str(counter)!r})\n"
        "count=int(path.read_text())+1 if path.exists() else 1\n"
        "path.write_text(str(count))\n"
        f"failure={failure!r}\n"
        "if count == 2 and failure == 'disconnect':\n"
        "    print('synthetic-command-private',file=sys.stderr)\n"
        "    sys.exit(1)\n"
        f"value={envelope()!r}\n"
        "value['token']='synthetic-command-'+str(count)\n"
        "value['expires_at']=int(time.time())+30\n"
        "if count == 2 and failure == 'scope':\n"
        "    value['forge']['repository']='other/repository'\n"
        "print(json.dumps(value))\n",
        encoding="utf-8",
    )
    config = tmp_path / "pr-config.json"
    config.write_text(json.dumps({"repos": {"fixture": {"anchor": str(tmp_path), "pr": {
        "provider": "github", "repository": "owner/repository",
    }}}}), encoding="utf-8")
    native = tmp_path / "native.py"
    native.write_text(
        "import json,pathlib,subprocess,sys\n"
        "from agent_worktrees import pr_cli\n"
        "from agent_worktrees.providers import github\n"
        f"counter=pathlib.Path({str(counter)!r})\n"
        f"requested=pathlib.Path({str(requested)!r})\n"
        "def run(argv, *, env):\n"
        "    expected='synthetic-command-'+counter.read_text()\n"
        "    assert env['GH_TOKEN']==env['GITHUB_TOKEN']==expected\n"
        "    assert not any('synthetic-command-' in part for part in argv)\n"
        "    with requested.open('a') as stream: stream.write('request\\n')\n"
        "    url=argv[-1]\n"
        "    if url.endswith('/pulls/7'):\n"
        "        value={'state':'open','head':{'sha':'head'},'title':'Fixture'}\n"
        "    elif url.endswith('/status'):\n"
        "        value={'total_count':0,'statuses':[]}\n"
        "    elif '/check-runs?' in url:\n"
        "        value={'total_count':0,'check_runs':[]}\n"
        "    else: value=[]\n"
        "    return subprocess.CompletedProcess(argv,0,json.dumps(value),'')\n"
        "github.run_cli=run\n"
        "sys.exit(pr_cli.cmd_pr_watch_dispatch(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [sys.executable, str(native), "snapshot", "owner/repository", "7", "--config", str(config),
         "--credential-command", json.dumps([sys.executable, str(helper)]), "--json"],
        timeout=20, max_stdout_bytes=65536, max_stderr_bytes=8192,
    )
    assert "synthetic-command-" not in result.stdout + result.stderr
    if failure:
        assert result.returncode != 0
        assert counter.read_text() == "2" and requested.read_text().splitlines() == ["request"]
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["head_sha"] == "head"
        assert int(counter.read_text()) == len(requested.read_text().splitlines()) == 4
