"""Regression tests for the internal-identifier pre-push guard (#541, #3923).

The guard used to scan the *entire* tracked tree, so a pre-existing identifier
in an untouched file blocked every unrelated push. It now scans only the push
diff (``<base>...HEAD``) by default, while ``--all`` still audits the whole
tree. These tests drive the real script as a subprocess inside a throwaway git
repo so the git-diff scoping is exercised end-to-end.

Run:  python -m pytest tools/test_check_no_internal_identifiers.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-no-internal-identifiers.py"
LEAK = "acme-internal-id"
CI_TOKEN = "widget-facility"
CI_REASON = "replace with generic widget | never name the facility"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo with a simulated ``origin/main`` base carrying a pre-existing
    leak in an untouched file, and a HEAD that changes only a clean file."""
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "tools" / SCRIPT.name)

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "checkout", "-q", "-b", "main")

    # Base commit: a pre-existing leak in an untouched file.
    _write(r, "plugins/old/legacy.txt", f"this file mentions {LEAK} already\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    # Simulate the remote the guard diffs against.
    _git(r, "update-ref", "refs/remotes/origin/main", base_sha)

    # New commit: touch only a clean file.
    _write(r, "plugins/new/clean.txt", "nothing sensitive here\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "clean change")
    return r


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={**_base_env(), "COPILOT_EXTENSIONS_FORBIDDEN_IDS": LEAK},
    )


def _base_env() -> dict[str, str]:
    import os

    # Keep PATH/SYSTEMROOT so git + python resolve on every platform.
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "HOME", "USERPROFILE", "TEMP", "TMP")
    env = {k: v for k, v in os.environ.items() if k in keep}
    # Source 4 (the live agent-worktrees sweep) reads real machine state
    # (repos.yaml, other repos' .identifier-blocklist/ files) -- disabled by
    # default so these subprocess-driven tests stay deterministic and
    # machine-independent regardless of what's actually installed/registered
    # on the box running them. Tests that specifically exercise source 4
    # override this.
    env["COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP"] = "1"
    return env


def test_diff_scope_ignores_pre_existing_leak_in_untouched_file(repo: Path):
    # The bug fix: default (push-diff) scope must NOT flag the pre-existing leak
    # in plugins/old/legacy.txt because this push doesn't touch it.
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_flag_still_audits_whole_tree(repo: Path):
    # --all restores the full-tree sweep and catches the pre-existing leak.
    result = _run(repo, "--all")
    assert result.returncode == 1
    assert LEAK in result.stdout


def test_base_sharing_no_merge_base_falls_back_to_full_tree_scan(repo: Path):
    """A base that RESOLVES but shares no common ancestor with HEAD at all
    (the confirmed fallout of a deliberate `main` history rewrite -- see
    docs/pipelines.md's "If main's history is force-rewritten") used to
    crash this guard outright with an unhandled ``CalledProcessError`` from
    the three-dot diff's "no merge base" failure. It must instead degrade
    to the same full-tree-scan fallback an unresolvable base ref already
    gets -- which, as a side effect, means the pre-existing leak this
    fixture seeds IS now caught (full-tree scope), not a false negative."""
    _git(repo, "checkout", "-q", "--orphan", "rewritten-main")
    _write(repo, "unrelated.txt", "rewritten history\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated root (simulates a rewritten main)")
    rewritten_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", rewritten_sha)
    _git(repo, "checkout", "-q", "main")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert LEAK in result.stdout
    assert "shares no history" in result.stdout + result.stderr


def test_diff_scope_still_catches_introduced_leak(repo: Path):
    # A leak in a file the push actually changes is still caught in diff scope.
    _write(repo, "plugins/new/clean.txt", f"oops {LEAK} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce leak")
    result = _run(repo)
    assert result.returncode == 1
    assert LEAK in result.stdout


def test_load_identifier_data_merges_plain_and_ci_sources_without_duplicates(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _load_module(repo)
    monkeypatch.setenv(module.LIVE_SWEEP_DISABLE_ENV, "1")
    home_dir = repo / "home"
    home_dir.mkdir()
    monkeypatch.setattr(module, "HOME_LIST", home_dir / ".agent-codespaces" / "forbidden-identifiers.txt")
    monkeypatch.setenv(
        "COPILOT_EXTENSIONS_FORBIDDEN_IDS",
        f"{LEAK},{CI_TOKEN},CaseOnly,",
    )
    monkeypatch.setenv(
        module.CI_LIST_ENV,
        f"{CI_TOKEN}|{CI_REASON};second-token|why this matters\ncaseonly|reason that loses\n",
    )
    (home_dir / ".agent-codespaces").mkdir()
    (home_dir / ".agent-codespaces" / "forbidden-identifiers.txt").write_text(
        "# comment\nthird-token\nSECOND-token\n",
        encoding="utf-8",
    )

    identifiers, reasons, live_sweep_error = module._load_identifier_data()
    assert live_sweep_error is None

    assert identifiers == [
        LEAK,
        CI_TOKEN,
        "caseonly",
        "third-token",
        "second-token",
    ]
    assert reasons == {
        CI_TOKEN: CI_REASON,
        "second-token": "why this matters",
        "caseonly": "reason that loses",
    }


def test_no_live_sweep_keeps_every_configured_input(repo: Path, monkeypatch):
    module = _load_module(repo)
    home_list = repo / "configured-identifiers.txt"
    home_list.write_text("local-token\n", encoding="utf-8")
    monkeypatch.setattr(module, "HOME_LIST", home_list)
    monkeypatch.setenv("COPILOT_EXTENSIONS_FORBIDDEN_IDS", "environment-token")
    monkeypatch.setenv(module.CI_LIST_ENV, "ci-token|configured CI input")
    monkeypatch.setattr(
        module, "_load_live_sweep_identifiers",
        lambda: pytest.fail("configured-only mode started discovery"),
    )
    identifiers, reasons, error = module._load_identifier_data(include_live_sweep=False)
    assert identifiers == ["environment-token", "local-token", "ci-token"]
    assert reasons["ci-token"] == "configured CI input"
    assert error is None
    assert module.main(["--no-live-sweep"]) == 0


# ---------------------------------------------------------------------------
# Source 4: the live agent-worktrees cross-repo sweep
# ---------------------------------------------------------------------------

def test_live_sweep_disabled_by_default_env_var(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.setenv(module.LIVE_SWEEP_DISABLE_ENV, "1")
    # Even a "found" binary must not be invoked once disabled.
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")
    called = []
    monkeypatch.setattr(
        module, "_run_live_sweep",
        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError("should not run")),
    )
    assert module._load_live_sweep_identifiers() == []
    assert called == []


def test_live_sweep_absent_when_binary_not_found(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    assert module._load_live_sweep_identifiers() == []


def test_live_sweep_contributes_identifiers(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = json.dumps({
            "error": None,
            "entries": [
                {"token": "swept-token", "reason": "swept reason"},
                {"token": r"regex:\bSWEPT\b", "reason": None},
            ],
        })
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    result = module._load_live_sweep_identifiers()
    assert result == [
        ("swept-token", "swept reason"),
        (r"regex:\bSWEPT\b", None),
    ]


def test_live_sweep_treats_unknown_subcommand_as_benign_absence(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    """An installed agent-worktrees old enough to predate the `identifiers`
    command entirely must be treated as absent, not a configuration
    failure -- it rejects with the dispatcher's generic "Unknown
    subcommand" error, same as any other unrecognized verb."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 1
        stdout = "✗ Unknown subcommand: identifiers\n✗ Run 'agent-worktrees --help' for available commands.\n"
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    assert module._load_live_sweep_identifiers() == []


def test_live_sweep_treats_unresolved_project_as_benign_absence(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    """An installed agent-worktrees whose cwd isn't a registered project on
    this machine must also be treated as absent, not a configuration
    failure -- it rejects before subcommand dispatch with the generic
    "Could not resolve a project" response."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 1
        stdout = "Could not resolve a project for 'identifiers'. Context is discovered from the cwd.\n"
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    assert module._load_live_sweep_identifiers() == []


def test_live_sweep_merges_into_load_identifier_data(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = json.dumps({
            "error": None,
            "entries": [{"token": "swept-token", "reason": "swept reason"}],
        })
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    identifiers, reasons, live_sweep_error = module._load_identifier_data()
    assert live_sweep_error is None
    assert "swept-token" in identifiers
    assert reasons["swept-token"] == "swept reason"


def test_live_sweep_rejects_non_list_entries(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = json.dumps({"error": None, "entries": "not-a-list"})
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_live_sweep_rejects_non_mapping_entry(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = json.dumps({"error": None, "entries": ["not-a-mapping"]})
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_live_sweep_rejects_entry_with_missing_or_non_string_token(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A successful subprocess response of `{"entries": [{}]}` must fail
    closed rather than silently producing an empty token that
    `_load_identifier_data()` would just drop -- an empty/non-string token
    means the live-sweep protocol itself is malformed, which the guard must
    never silently accept."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = json.dumps({"error": None, "entries": [{}]})
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_live_sweep_raises_on_nonzero_exit(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 1
        stdout = json.dumps({"error": "boom", "entries": []})
        stderr = "boom"

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_live_sweep_failure_preserves_partial_entries(repo: Path, monkeypatch: pytest.MonkeyPatch):
    """A broken peer repo's blocklist must not discard the entries a sweep
    DID manage to parse before failing."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 1
        stdout = json.dumps({
            "error": "one peer repo's blocklist failed to parse",
            "entries": [{"token": "still-valid", "reason": "a reason"}],
        })
        stderr = "one peer repo's blocklist failed to parse"

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure) as excinfo:
        module._load_live_sweep_identifiers()
    assert excinfo.value.pairs == [("still-valid", "a reason")]


def test_load_identifier_data_fails_loud_on_live_sweep_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 1
        stdout = json.dumps({
            "error": "boom",
            "entries": [{"token": "still-valid", "reason": "a reason"}],
        })
        stderr = "boom"

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    identifiers, reasons, live_sweep_error = module._load_identifier_data()
    assert live_sweep_error is not None
    # Partial entries are still included, even though the overall result
    # must still be treated as a failure by the caller.
    assert "still-valid" in identifiers


def test_main_fails_the_push_on_live_sweep_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """`main()` must fail the push outright on a live-sweep failure, even
    with zero real forbidden-identifier matches in the diff -- a broken
    local blocklist configuration is never silently downgraded to a pass."""
    module = _load_module(repo)
    monkeypatch.setattr(
        module, "_load_identifier_data",
        lambda: ([], {}, "agent-worktrees identifiers sweep failed: boom"),
    )
    rc = module.main([])
    assert rc == 1
    assert "boom" in capsys.readouterr().out


def test_live_sweep_silently_ignores_timeout(repo: Path, monkeypatch: pytest.MonkeyPatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    def _raise(*a, **k):
        raise module.subprocess.TimeoutExpired(cmd="agent-worktrees", timeout=10)

    monkeypatch.setattr(module, "_run_live_sweep", _raise)
    assert module._load_live_sweep_identifiers() == []


def test_live_sweep_unsafe_observation_is_not_benign_absence(repo, monkeypatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    def fail(_command):
        raise RuntimeError("owned scope could not settle")

    monkeypatch.setattr(module, "_run_live_sweep", fail)
    with pytest.raises(module.LiveSweepFailure, match="observed safely"):
        module._load_live_sweep_identifiers()


@pytest.mark.parametrize("failure", ["output", "cleanup"])
def test_live_sweep_concrete_owned_failures_block_publication(repo, monkeypatch, failure):
    monkeypatch.syspath_prepend(str(SCRIPT.parent.parent / "libs" / "agent-procutil" / "src"))
    from agent_procutil.owned_process import OutputLimitExceeded, OwnedCleanupError

    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")
    error = OutputLimitExceeded("stdout", 32 * 1024 * 1024) if failure == "output" else OwnedCleanupError(object())

    def fail(_command):
        raise error

    monkeypatch.setattr(module, "_run_live_sweep", fail)
    with pytest.raises(type(error)) as caught:
        module._load_live_sweep_identifiers()
    assert caught.value is error


@pytest.mark.skipif(os.name != "nt", reason="native Windows pipe-inheriting descendant boundary")
@pytest.mark.parametrize("root_exits", [False, True])
def test_native_live_sweep_deadline_retires_pipe_holding_descendant(tmp_path, root_exits):
    import ctypes
    from ctypes import wintypes

    module = _load_module(SCRIPT.parent.parent)
    pid_file = tmp_path / "owned-child.pid"
    script = (
        "import os,pathlib,subprocess,sys,time;"
        "assert os.environ['AGENT_WORKTREES_NO_SELFPROVISION']=='1';"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(p.pid));"
        + ("sys.exit(0)" if root_exits else "time.sleep(60)")
    )
    started = time.monotonic()
    try:
        module._run_live_sweep([sys.executable, "-c", script, str(pid_file)])
    except subprocess.TimeoutExpired:
        pass
    assert time.monotonic() - started < 15
    pid = int(pid_file.read_text())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if handle:
        try:
            code = wintypes.DWORD()
            assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
            assert code.value != 259
        finally:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="native Windows command-binstub boundary")
def test_native_live_sweep_quotes_command_binstub_without_provisioning(tmp_path):
    module = _load_module(SCRIPT.parent.parent)
    command = tmp_path / "owner's & catalog.cmd"
    command.write_text(
        '@echo off\nif not "%AGENT_WORKTREES_NO_SELFPROVISION%"=="1" exit /b 9\n'
        'echo {"error":null,"entries":[]}\nexit /b 0\n',
    )
    result = module._run_live_sweep([str(command), "identifiers", "sweep", "--format", "json"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"error": None, "entries": []}


@pytest.mark.skipif(os.name == "nt", reason="native POSIX command and finite-reader boundary")
def test_native_posix_live_sweep_preserves_command_and_deadline(tmp_path):
    module = _load_module(SCRIPT.parent.parent)
    command = tmp_path / "owner's & catalog"
    command.write_text(
        "#!/bin/sh\n"
        'test "$AGENT_WORKTREES_NO_SELFPROVISION" = 1 || exit 9\n'
        'test "$1" = "literal argument" || exit 8\n'
        "printf '%s\\n' '{\"error\":null,\"entries\":[]}'\n",
    )
    command.chmod(0o700)
    result = module._run_live_sweep([str(command), "literal argument"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"error": None, "entries": []}
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        module._run_live_sweep([sys.executable, "-c", "import time; time.sleep(60)"])
    assert time.monotonic() - started < 15
def test_live_sweep_missing_capture_helper_fails_closed(repo: Path, monkeypatch):
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: sys.executable)
    with pytest.raises(module.LiveSweepFailure, match="requires repository agent-procutil"):
        module._load_live_sweep_identifiers()


def _sweep_ownership(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parents[1] / "libs/agent-procutil/src"))
    from agent_procutil import owned_process

    return owned_process


def test_live_sweep_uses_fixed_owned_capture_contract(monkeypatch):
    module = _load_module(SCRIPT.parent.parent)
    ownership = _sweep_ownership(monkeypatch)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "selected-sweep")
    monkeypatch.setenv("LIVE_SWEEP_CONTEXT_FIXTURE", "preserved")
    calls = []

    def run(argv, **kwargs):
        environment = kwargs.pop("env")
        calls.append((
            argv, kwargs, environment.get("AGENT_WORKTREES_NO_SELFPROVISION"),
            environment.get("LIVE_SWEEP_CONTEXT_FIXTURE"),
        ))
        return subprocess.CompletedProcess(argv, 0, '{"error":null,"entries":[]}', "")

    monkeypatch.setattr(ownership, "run_caller_owned", run)
    assert module._load_live_sweep_identifiers() == []
    assert calls == [(
        ["selected-sweep", "identifiers", "sweep", "--format", "json"],
        {"cwd": module.REPO, "timeout": 10, "max_stdout_bytes": 32 * 1024 * 1024,
         "max_stderr_bytes": 1024 * 1024},
        "1", "preserved",
    )]


def test_live_sweep_retains_unconfirmed_cleanup_custody(monkeypatch):
    module = _load_module(SCRIPT.parent.parent)
    ownership = _sweep_ownership(monkeypatch)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "selected-sweep")
    process, job = object(), object()
    failure = ownership.OwnedCleanupError(process, job)

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(ownership, "run_caller_owned", run)
    with pytest.raises(ownership.OwnedCleanupError) as caught:
        module._load_identifier_data()
    assert caught.value is failure
    assert caught.value.process is process and caught.value.job is job


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_live_sweep_output_limit_is_not_absence(monkeypatch, stream):
    module = _load_module(SCRIPT.parent.parent)
    ownership = _sweep_ownership(monkeypatch)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "selected-sweep")

    def run(*args, **kwargs):
        raise ownership.OutputLimitExceeded(stream, 16)

    monkeypatch.setattr(ownership, "run_caller_owned", run)
    with pytest.raises(module.LiveSweepFailure, match=f"{stream} exceeded"):
        module._load_live_sweep_identifiers()


@pytest.mark.skipif(
    os.name != "nt" and not sys.platform.startswith("linux"),
    reason="native Windows process handle or Linux pidfd observer required",
)
def test_live_sweep_timeout_settles_exited_parent_and_inherited_pipes(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack
    import ctypes
    import signal
    import time

    module = _load_module(SCRIPT.parent.parent)
    _sweep_ownership(monkeypatch)
    monkeypatch.syspath_prepend(str(SCRIPT.parents[1] / "libs/agent-procutil/tests"))
    import caller_owned_fixture as fixture

    ready, gate, exited = (tmp_path / name for name in ("ready", "gate", "parent-exiting"))
    child = (
        "import os,sys,time; from pathlib import Path; "
        "ready=Path(sys.argv[1]); pending=ready.with_suffix('.pending'); "
        "pending.write_text(str(os.getpid())); pending.replace(ready); time.sleep(60)"
    )
    parent = (
        "import os,subprocess,sys,time; from pathlib import Path; "
        "from agent_procutil import no_window_kwargs; "
        f"subprocess.Popen([sys.executable,'-I','-c',{child!r},{str(ready)!r}],"
        "stdin=subprocess.DEVNULL,**no_window_kwargs()); "
        f"gate=Path({str(gate)!r}); "
        "\nwhile not gate.exists(): time.sleep(.01)\n"
        f"Path({str(exited)!r}).write_text('exiting'); os._exit(0)"
    )
    # The parent needs only the same canonical helper for its hidden leaf launch.
    bootstrap = (
        f"import sys; sys.path.insert(0,{str(SCRIPT.parents[1] / 'libs/agent-procutil/src')!r}); "
        + parent
    )
    with ExitStack() as cleanup, ThreadPoolExecutor(max_workers=1) as pool:
        started = time.monotonic()
        future = pool.submit(module._run_live_sweep, [sys.executable, "-I", "-c", bootstrap])
        try:
            deadline = started + 6
            while not ready.exists():
                if future.done():
                    future.result()
                assert time.monotonic() < deadline, "sweep leaf did not become ready"
                time.sleep(.01)
            identity = fixture.identity(int(ready.read_text()))
            if os.name == "nt":
                kernel = fixture.windows_kernel()
                handle = kernel.OpenProcess(0x1000 | 0x100000 | 0x0001, False, identity["pid"])
                if not handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                cleanup.callback(kernel.CloseHandle, handle)
                assert fixture.handle_birth(kernel, handle) == identity["birth"]

                def gone():
                    status = kernel.WaitForSingleObject(handle, 0)
                    assert status in (0, 0x102)
                    return status == 0

                def kill():
                    if not gone() and not kernel.TerminateProcess(handle, 137):
                        raise ctypes.WinError(ctypes.get_last_error())
            else:
                fd = fixture.open_pidfd(identity["pid"])
                cleanup.callback(os.close, fd)
                assert fixture.identity(identity["pid"]) == identity

                def gone():
                    return fixture.pidfd_exited(fd)

                def kill():
                    if not gone():
                        fixture.signal_pidfd(fd, signal.SIGKILL)
            cleanup.callback(kill)
            gate.touch()
            with pytest.raises(subprocess.TimeoutExpired):
                future.result(timeout=15)
            assert exited.read_text() == "exiting"
            deadline = time.monotonic() + 2
            while not gone() and time.monotonic() < deadline:
                time.sleep(.01)
            assert time.monotonic() - started < 15
            assert gone(), "owned sweep leaf survived the capture timeout"
        finally:
            gate.touch()


def test_live_sweep_fails_closed_on_empty_output_with_exit_zero(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Once a discovered sweep command has actually run (not one of the
    explicitly recognized benign-absence responses), empty stdout on a
    successful exit is itself a protocol failure -- a well-behaved sweep
    always emits a full JSON payload, even for a genuinely empty result.
    Silently treating this as "nothing to report" could disable the live
    denylist with no visible signal."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = "   \n"
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_live_sweep_fails_closed_on_malformed_json_with_exit_zero(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Non-JSON stdout on a successful exit must also fail closed, not be
    silently treated as an empty sweep."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 0
        stdout = "not actually json"
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_live_sweep_benign_absence_uses_exact_line_not_loose_substring(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A real malformed-blocklist error that happens to mention a repo path
    or embed YAML parser text containing one of the benign-absence phrases
    (e.g. a peer repo literally named "could-not-resolve-a-project") must
    still fail the push -- the detection matches an exact dispatcher error
    line, not a loose substring anywhere in the whole output."""
    module = _load_module(repo)
    monkeypatch.delenv(module.LIVE_SWEEP_DISABLE_ENV, raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/usr/bin/agent-worktrees")

    class _FakeResult:
        returncode = 1
        stdout = json.dumps({
            "error": (
                "block-for-public.yaml entry #2: Could not resolve a project "
                "reference in token pattern -- invalid regex"
            ),
            "entries": [],
        })
        stderr = ""

    monkeypatch.setattr(module, "_run_live_sweep", lambda *a, **k: _FakeResult())
    with pytest.raises(module.LiveSweepFailure):
        module._load_live_sweep_identifiers()


def test_ci_loader_splits_first_pipe_only(repo: Path):
    module = _load_module(repo)
    assert module._load_ci_identifiers(
        f"{CI_TOKEN}|{CI_REASON};other-token|simple reason\nbare-token"
    ) == [
        (CI_TOKEN, CI_REASON),
        ("other-token", "simple reason"),
        ("bare-token", None),
    ]


def test_regex_ci_token_matches_whole_word_without_embedded_words(repo: Path):
    module = _load_module(repo)
    token = r"regex:\bexample\b"
    assert module._load_ci_identifiers(token + "|Use a placeholder") == [
        (token, "Use a placeholder")
    ]
    matches = module._scan_text(
        "plugins/new/clean.txt",
        "spexample example-ish EXAMPLE example_spoon teaspoon\n",
        [token, "missing-literal"],
        {token: "Use a placeholder"},
    )
    assert [(match.line, match.col, match.identifier, match.reason) for match in matches] == [
        (1, 11, "example", "Use a placeholder")
    ]


def test_regex_ci_loader_preserves_alternation_and_pipe_in_reason(repo: Path):
    module = _load_module(repo)
    assert module._load_ci_identifiers(
        r"regex:\b(foo||bar)\b|Use generic | not internal;plain|plain reason"
    ) == [
        (r"regex:\b(foo|bar)\b", "Use generic | not internal"),
        ("plain", "plain reason"),
    ]
    matches = module._scan_text(
        "plugins/new/clean.txt",
        "bar",
        [r"regex:\b(foo|bar)\b"],
        {},
    )
    assert [(match.col, match.identifier) for match in matches] == [(1, "bar")]


def test_regex_ci_mode_redacts_pattern_but_trusted_details_report_match(
    repo: Path, tmp_path: Path
):
    _write(repo, "plugins/new/clean.txt", "fool FOO teaspoon\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce bounded identifier")
    details_out = tmp_path / "details.json"
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / SCRIPT.name),
            "--ci",
            "--trusted-details-json-out",
            str(details_out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": r"regex:\bfoo\b|Use a generic placeholder",
        },
    )
    assert result.returncode == 1
    assert "FOO" not in result.stdout
    assert "regex:" not in result.stdout
    assert json.loads(details_out.read_text(encoding="utf-8")) == [
        {
            "file": "plugins/new/clean.txt",
            "line": 1,
            "col": 6,
            "identifier": "FOO",
            "reason": "Use a generic placeholder",
        }
    ]


def test_invalid_regex_fails_explicitly_without_printing_token(repo: Path):
    module = _load_module(repo)
    with pytest.raises(ValueError, match="invalid regular expression in forbidden identifier list"):
        module._scan_text("example.txt", "anything", ["regex:(private-marker"], {})
    with pytest.raises(ValueError, match="empty regular expression match"):
        module._scan_text("example.txt", "anything", ["regex:(?=anything)"], {})
    with pytest.raises(ValueError, match="invalid regular expression"):
        module._scan([], ["regex:(private-marker"], {})
    with pytest.raises(ValueError, match="empty regular expression match"):
        module._scan([], ["regex:.*"], {})


def test_load_paths_file_preserves_filename_whitespace(repo: Path):
    module = _load_module(repo)
    paths_file = repo / "paths.txt"
    paths_file.write_text(" leading space.txt \nplain.txt\n", encoding="utf-8")
    assert module._load_paths_file(paths_file) == [" leading space.txt ", "plain.txt"]


def test_jsonl_paths_preserve_all_git_filename_characters(repo: Path):
    module = _load_module(repo)
    paths = ["line\nbreak.txt", "line\rbreak.txt", "line\u2028break.txt", "slash\\name", " space "]
    path = repo / "paths.jsonl"
    path.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in paths), encoding="utf-8")
    assert module._load_paths_jsonl(path) == paths


@pytest.mark.parametrize("record", ['123', '""', '"nul\\u0000name"', 'not-json'])
def test_jsonl_paths_reject_invalid_records(repo: Path, record: str):
    module = _load_module(repo)
    path = repo / "paths.jsonl"
    path.write_text(record + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        module._load_paths_jsonl(path)


def test_jsonl_path_interface_is_exclusive_and_empty_files_are_valid(repo: Path):
    module = _load_module(repo)
    path = repo / "paths.jsonl"
    path.write_text("", encoding="utf-8")
    assert module._load_paths_jsonl(path) == []
    result = _run(repo, "--paths-file", "old.txt", "--paths-jsonl", str(path))
    assert result.returncode == 2 and "mutually exclusive" in result.stderr


def test_json_out_writes_hashed_findings_without_raw_token_or_reason(repo: Path, tmp_path: Path):
    _write(repo, "plugins/new/clean.txt", f"oops {CI_TOKEN} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce ci leak")
    json_out = tmp_path / "findings.json"
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / SCRIPT.name),
            "--json-out",
            str(json_out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS": LEAK,
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": f"{CI_TOKEN}|{CI_REASON}",
        },
    )

    assert result.returncode == 1
    payload_text = json_out.read_text(encoding="utf-8")
    assert CI_TOKEN not in payload_text
    assert "generic widget" not in payload_text
    payload = json.loads(payload_text)
    assert payload == [
        {
            "file": "plugins/new/clean.txt",
            "line": 1,
            "col": 6,
            "identifier_hash": hashlib.sha256(CI_TOKEN.encode("utf-8")).hexdigest(),
            "has_reason": True,
        }
    ]


def test_git_ref_scan_reads_passive_pr_head_data_and_trusted_details(repo: Path, tmp_path: Path):
    _write(repo, "plugins/new/clean.txt", f"oops {CI_TOKEN} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce ci leak")
    _write(repo, "plugins/new/clean.txt", "working tree cleaned after commit\n")
    paths_file = tmp_path / "paths.txt"
    json_out = tmp_path / "findings.json"
    details_out = tmp_path / "trusted-details.json"
    paths_file.write_text("plugins/new/clean.txt\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "tools" / SCRIPT.name),
            "--ci",
            "--paths-file",
            str(paths_file),
            "--git-ref",
            "HEAD",
            "--json-out",
            str(json_out),
            "--trusted-details-json-out",
            str(details_out),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": f"{CI_TOKEN}|{CI_REASON}",
        },
    )

    assert result.returncode == 1
    assert result.stdout.strip() == (
        "1 forbidden identifier(s) found -- see the 'identifier leak guard' Check Run output for details."
    )
    assert CI_TOKEN not in json_out.read_text(encoding="utf-8")
    assert CI_REASON not in json_out.read_text(encoding="utf-8")
    assert json.loads(details_out.read_text(encoding="utf-8")) == [
        {
            "file": "plugins/new/clean.txt",
            "line": 1,
            "col": 6,
            "identifier": CI_TOKEN,
            "reason": CI_REASON,
        }
    ]


def test_git_ref_loader_preserves_literal_paths_binary_deletions_and_snapshot(repo: Path):
    module = _load_module(repo)
    paths = [" line\nbreak\tname.txt ", ":(glob)*", "binary.txt", "deleted.txt"]
    for name, body in zip(paths, [f"{LEAK}\r\nsecond\n".encode(), b"", b"\xff", b"removed"]):
        result = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=body,
            capture_output=True, check=True,
        )
        blob = result.stdout.decode("ascii").strip()
        subprocess.run(
            ["git", "-c", "core.protectNTFS=false", "update-index", "-z", "--index-info"],
            cwd=repo, input=f"100644 {blob}\t{name}\0".encode(),
            capture_output=True, check=True,
        )
    _git(repo, "commit", "-qm", "literal names")
    loader = module._make_git_ref_loader("HEAD", [*paths, "missing.txt"])
    _git(repo, "update-index", "--force-remove", "--", "deleted.txt")
    _git(repo, "commit", "-qm", "delete selected path")
    assert loader(paths[0]) == f"{LEAK}\r\nsecond\n"
    assert loader(paths[1]) == ""
    assert loader(paths[2]) is None
    assert loader(paths[3]) == "removed"
    assert loader("missing.txt") is None
    assert module._make_git_ref_loader("HEAD", paths)("deleted.txt") is None


def test_git_ref_loader_batches_and_deduplicates_blobs(monkeypatch: pytest.MonkeyPatch):
    module = _load_module(SCRIPT.parent.parent)
    blobs = {f"{i:040x}": f"body {i}\n".encode() for i in range(65)}
    names = {f"file-{i}": blob for i, blob in enumerate(blobs)}
    names["duplicate"] = names["file-0"]
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "ls-tree":
            output = b"".join(
                f"100644 blob {blob} {len(blobs[blob])}\t{name}\0".encode()
                for name, blob in names.items()
            )
        else:
            requested = kwargs["input"].decode().splitlines()
            assert len(requested) <= 32
            output = b"".join(
                f"{blob} blob {len(blobs[blob])}\n".encode() + blobs[blob] + b"\n"
                for blob in requested
            )
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    loader = module._make_git_ref_loader("HEAD", list(names))
    assert all(loader(name) == blobs[blob].decode() for name, blob in names.items())
    assert [args[1] for args in calls] == ["ls-tree", "cat-file", "cat-file", "cat-file"]


@pytest.mark.parametrize("sizes,counts", [
    ([4 * 1024 * 1024, 4 * 1024 * 1024, 1], [2, 1]),
    ([8 * 1024 * 1024 + 1, 1], [1, 1]),
])
def test_git_ref_loader_bounds_batch_bytes(monkeypatch: pytest.MonkeyPatch, sizes, counts):
    module = _load_module(SCRIPT.parent.parent)
    blobs = {f"{i:040x}": b"x" * size for i, size in enumerate(sizes)}
    observed = []

    def run(argv, **kwargs):
        if argv[1] == "ls-tree":
            output = b"".join(
                f"100644 blob {blob} {len(body)}\tfile-{i}\0".encode()
                for i, (blob, body) in enumerate(blobs.items())
            )
        else:
            requested = kwargs["input"].decode().splitlines()
            observed.append(len(requested))
            output = b"".join(
                f"{blob} blob {len(blobs[blob])}\n".encode() + blobs[blob] + b"\n"
                for blob in requested
            )
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    loader = module._make_git_ref_loader("HEAD", [f"file-{i}" for i in range(len(sizes))])
    assert [len(loader(f"file-{i}")) for i in range(len(sizes))] == sizes
    assert observed == counts


@pytest.mark.parametrize("response", [
    b"", b"a" * 40 + b" missing\n",
    b"a" * 40 + b" tree 3\nabc\n",
    b"b" * 40 + b" blob 3\nabc\n",
    b"a" * 40 + b" blob 4\nabc\n",
    b"a" * 40 + b" blob 3\nab",
    b"a" * 40 + b" blob 3\nabc!",
    b"a" * 40 + b" blob 3\nabc\nextra",
])
def test_git_ref_loader_refuses_incomplete_or_malformed_batches(
    monkeypatch: pytest.MonkeyPatch, response: bytes,
):
    module = _load_module(SCRIPT.parent.parent)

    def run(argv, **kwargs):
        output = b"100644 blob " + b"a" * 40 + b" 3\tfile.txt\0" if argv[1] == "ls-tree" else response
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(ValueError, match="identifier scan blob response"):
        module._make_git_ref_loader("HEAD", ["file.txt"])


def test_git_ref_loader_propagates_git_failure(monkeypatch: pytest.MonkeyPatch):
    module = _load_module(SCRIPT.parent.parent)

    def fail(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        module._make_git_ref_loader("HEAD", ["file.txt"])


def test_ci_mode_stdout_is_count_only(repo: Path):
    _write(repo, "plugins/new/clean.txt", f"oops {CI_TOKEN} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce ci leak")
    result = subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), "--ci"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **_base_env(),
            "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI": f"{CI_TOKEN}|{CI_REASON}",
        },
    )

    assert result.returncode == 1
    assert CI_TOKEN not in result.stdout
    assert CI_REASON not in result.stdout
    assert result.stdout.strip() == (
        "1 forbidden identifier(s) found -- see the 'identifier leak guard' Check Run output for details."
    )


def test_scan_tracks_first_column_for_multi_occurrence_line(repo: Path):
    module = _load_module(repo)
    _write(repo, "plugins/new/clean.txt", f"prefix {LEAK} middle {LEAK} suffix\n")
    violations = module._scan(
        ["plugins/new/clean.txt"],
        [LEAK],
        {},
    )

    assert violations == [
        module.Violation(
            path="plugins/new/clean.txt",
            line=1,
            col=8,
            identifier=LEAK,
            reason=None,
        )
    ]


def test_all_and_paths_file_are_mutually_exclusive(repo: Path):
    result = _run(repo, "--all", "--paths-file", "ignored.txt")
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr


def _load_module(repo: Path):
    import importlib.util
    import sys as _sys

    spec = importlib.util.spec_from_file_location("check_no_internal_identifiers", repo / "tools" / SCRIPT.name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
