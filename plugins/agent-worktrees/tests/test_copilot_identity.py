"""Tests for Copilot CLI's own identity resolution/enforcement.

Prototype for ThomasMichon/copilot-extensions#3296: Copilot CLI's own
inference identity (``~/.copilot/config.json``) is a separate system from the
``gh`` CLI account already governed by ``repos.py``'s ``account``/
``account_map``. These tests cover the repo-keyed ``copilot_account``
resolution chain and the non-interactive ``ensure_login`` enforcement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import copilot_identity, copilot_identity_cli, output, repos


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the registry and Copilot config read/write under a tmp dir."""
    monkeypatch.setattr(repos.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(copilot_identity.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    return tmp_path


def _write_registry(home: Path, text: str) -> None:
    path = home / ".agent-worktrees" / "repos.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_copilot_config(home: Path, last_login: str | None) -> None:
    path = home / ".copilot" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if last_login is not None:
        data["lastLoggedInUser"] = {"host": "https://github.com", "login": last_login}
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# resolve_copilot_account / copilot_account_for
# ---------------------------------------------------------------------------

def test_resolve_copilot_account_explicit_override(home: Path):
    _write_registry(
        home,
        "repos:\n"
        "  aperture-labs:\n"
        "    class: worktree\n"
        "    copilot_account: ThomasMichon\n",
    )
    entry = repos.find_repo("aperture-labs")
    assert repos.resolve_copilot_account(entry) == "ThomasMichon"
    assert repos.copilot_account_for("aperture-labs") == "ThomasMichon"


def test_copilot_account_for_falls_back_to_machine_default(home: Path, monkeypatch):
    _write_registry(
        home,
        "repos:\n"
        "  odsp-web-harness:\n"
        "    class: worktree\n"
        "    remote: \"https://github.com/gim-home/odsp-web-harness.git\"\n",
    )
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.Config(
            srcroot=str(home), machine="test", platform="windows",
            default_copilot_account="tmichon_microsoft",
        ),
    )
    assert repos.copilot_account_for("odsp-web-harness") == "tmichon_microsoft"


def test_copilot_account_for_none_when_nothing_set(home: Path, monkeypatch):
    _write_registry(
        home,
        "repos:\n"
        "  some-repo:\n"
        "    class: reference\n",
    )
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.Config(srcroot=str(home), machine="test", platform="windows"),
    )
    assert repos.copilot_account_for("some-repo") is None


def test_set_and_unset_copilot_account(home: Path):
    _write_registry(home, "repos:\n  demo:\n    class: worktree\n")
    assert repos.set_copilot_account("demo", "ThomasMichon") is True
    assert repos.copilot_account_for("demo") == "ThomasMichon"
    assert repos.unset_copilot_account("demo") is True
    assert repos.resolve_copilot_account(repos.find_repo("demo")) is None
    # No-op on an unregistered repo.
    assert repos.set_copilot_account("does-not-exist", "X") is False


# ---------------------------------------------------------------------------
# current_login
# ---------------------------------------------------------------------------

def test_current_login_reads_last_logged_in_user(home: Path):
    _write_copilot_config(home, "tmichon_microsoft")
    assert copilot_identity.current_login() == "tmichon_microsoft"


def test_current_login_none_when_missing(home: Path):
    assert copilot_identity.current_login() is None


# ---------------------------------------------------------------------------
# ensure_login
# ---------------------------------------------------------------------------

def test_ensure_login_already_correct_skips_subprocess(home: Path, monkeypatch):
    _write_copilot_config(home, "tmichon_microsoft")

    def _boom(*_a, **_k):
        raise AssertionError("must not shell out when already correct")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _boom)
    result = copilot_identity.ensure_login("tmichon_microsoft")
    assert result.status == "already-correct"
    assert result.ok


def test_ensure_login_no_target_is_a_noop(home: Path):
    result = copilot_identity.ensure_login(None)
    assert result.status == "no-target"
    assert not result.ok


def test_ensure_login_switches_via_gh_token(home: Path, monkeypatch):
    _write_copilot_config(home, "ThomasMichon")
    monkeypatch.setattr(copilot_identity.shutil, "which", lambda _name: "/usr/bin/x")
    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", lambda: 0)

    calls = []

    class _Proc:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:3] == ["gh", "auth", "token"]:
            return _Proc(0, stdout="fake-token-value\n")
        if cmd[:2] == ["copilot", "login"]:
            assert kwargs.get("input") == "fake-token-value"
            return _Proc(0, stdout="Signed in successfully as tmichon_microsoft.\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _run)
    result = copilot_identity.ensure_login("tmichon_microsoft")
    assert result.status == "switched"
    assert result.ok
    assert result.previous == "ThomasMichon"
    assert result.target == "tmichon_microsoft"
    # The token must never be logged/returned anywhere in the result.
    assert "fake-token-value" not in (result.detail or "")


def test_ensure_login_no_cached_token(home: Path, monkeypatch):
    _write_copilot_config(home, "ThomasMichon")
    monkeypatch.setattr(copilot_identity.shutil, "which", lambda _name: "/usr/bin/x")
    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", lambda: 0)

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "not logged in as that user"

    monkeypatch.setattr(copilot_identity.subprocess, "run", lambda *a, **k: _Proc())
    result = copilot_identity.ensure_login("tmichon_microsoft")
    assert result.status == "no-cached-token"
    assert not result.ok


def test_ensure_login_dry_run_never_shells_out(home: Path, monkeypatch):
    _write_copilot_config(home, "ThomasMichon")

    def _boom(*_a, **_k):
        raise AssertionError("dry-run must not shell out")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _boom)
    result = copilot_identity.ensure_login("tmichon_microsoft", dry_run=True)
    assert result.status == "switched"
    assert "dry-run" in result.detail


def test_ensure_login_dry_run_bypasses_other_sessions_gate(home: Path, monkeypatch):
    """dry-run must not even query for other running sessions -- it never
    shells out, and the gate itself is irrelevant to a pure preview."""
    _write_copilot_config(home, "ThomasMichon")

    def _boom():
        raise AssertionError("dry-run must not check for other sessions")

    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", _boom)
    result = copilot_identity.ensure_login("tmichon_microsoft", dry_run=True)
    assert result.status == "switched"


# ---------------------------------------------------------------------------
# The other-running-sessions safety gate
# ---------------------------------------------------------------------------

def test_ensure_login_refuses_switch_when_other_sessions_running(home: Path, monkeypatch):
    """Switching the shared ~/.copilot/config.json identity while another
    Copilot CLI process is running risks splicing that session's billing
    across accounts, invalidating its prompt cache, and auth errors -- this
    must refuse rather than silently switching underneath it."""
    _write_copilot_config(home, "ThomasMichon")
    monkeypatch.setattr(copilot_identity.shutil, "which", lambda _name: "/usr/bin/x")
    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", lambda: 3)

    def _boom(*_a, **_k):
        raise AssertionError("must not shell out when the safety gate refuses")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _boom)
    result = copilot_identity.ensure_login("tmichon_microsoft")
    assert result.status == "other-sessions-active"
    assert not result.ok
    assert "3 other" in result.detail


def test_ensure_login_force_overrides_other_sessions_gate(home: Path, monkeypatch):
    _write_copilot_config(home, "ThomasMichon")
    monkeypatch.setattr(copilot_identity.shutil, "which", lambda _name: "/usr/bin/x")
    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", lambda: 3)

    class _Proc:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def _run(cmd, **kwargs):
        if cmd[:3] == ["gh", "auth", "token"]:
            return _Proc(0, stdout="fake-token-value\n")
        if cmd[:2] == ["copilot", "login"]:
            return _Proc(0, stdout="Signed in successfully.\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _run)
    result = copilot_identity.ensure_login("tmichon_microsoft", force=True)
    assert result.status == "switched"
    assert result.ok


def test_ensure_login_no_gate_when_already_correct(home: Path, monkeypatch):
    """The gate only matters when an actual switch would happen -- it must
    not even be consulted on the already-correct fast path."""
    _write_copilot_config(home, "tmichon_microsoft")

    def _boom():
        raise AssertionError("must not check for other sessions when already correct")

    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", _boom)
    result = copilot_identity.ensure_login("tmichon_microsoft")
    assert result.status == "already-correct"


# ---------------------------------------------------------------------------
# copilot_identity_switch_enabled -- loader mapping (default / global /
# machine-local precedence), exercised through the real cfg.load_config()
# YAML parsing rather than a hand-built Config, per review feedback that the
# monkeypatched-Config tests below never cover the loader site itself.
# ---------------------------------------------------------------------------

def _write_machine_config(path: Path, extra_line: str = "") -> None:
    path.write_text(
        "repo_name: ext\n"
        "srcroot: /tmp/src\n"
        "machine: anomalous-potato\n"
        "platform: wsl\n"
        f"{extra_line}"
        "repos:\n"
        "  ext:\n"
        "    anchor: /tmp/src/ext\n"
        "    worktree_root: /tmp/src/.worktrees/ext\n"
        "    default_branch: main\n"
        "    remote: origin\n",
        encoding="utf-8",
    )


def test_loader_copilot_identity_switch_defaults_false(home: Path):
    cfgfile = home / "config.yaml"
    _write_machine_config(cfgfile)
    conf = cfg.load_config(cfgfile)
    assert conf.copilot_identity_switch_enabled is False


def test_loader_copilot_identity_switch_true_from_global_tier(home: Path):
    global_dir = home / ".agent-worktrees"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(
        "copilot_identity_switch_enabled: true\n", encoding="utf-8"
    )
    cfgfile = home / "machine" / "config.yaml"
    cfgfile.parent.mkdir(parents=True, exist_ok=True)
    _write_machine_config(cfgfile)
    conf = cfg.load_config(cfgfile)
    assert conf.copilot_identity_switch_enabled is True


def test_loader_copilot_identity_switch_machine_overrides_global(home: Path):
    global_dir = home / ".agent-worktrees"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(
        "copilot_identity_switch_enabled: true\n", encoding="utf-8"
    )
    cfgfile = home / "machine" / "config.yaml"
    cfgfile.parent.mkdir(parents=True, exist_ok=True)
    _write_machine_config(cfgfile, "copilot_identity_switch_enabled: false\n")
    conf = cfg.load_config(cfgfile)
    assert conf.copilot_identity_switch_enabled is False


# ---------------------------------------------------------------------------
# switch_enabled()/intended_account() must still resolve when invoked as the
# real 'copilot-identity' no-project command -- front_door_cli classifies it
# in _NO_PROJECT_COMMANDS, so config.load_config() (no explicit path) raises
# via project_name(); without a no-project fallback the switch could never
# be turned on through the real CLI (only ever through a test's monkeypatched
# Config). Simulate that exact condition: no _ACTIVE_PROJECT set.
# ---------------------------------------------------------------------------

def test_switch_enabled_resolves_without_an_active_project(home: Path, monkeypatch):
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "")
    global_dir = home / ".agent-worktrees"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(
        "copilot_identity_switch_enabled: true\n", encoding="utf-8"
    )
    assert copilot_identity.switch_enabled() is True


def test_intended_account_resolves_without_an_active_project(home: Path, monkeypatch):
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "")
    global_dir = home / ".agent-worktrees"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(
        "default_copilot_account: tmichon_microsoft\n", encoding="utf-8"
    )
    assert copilot_identity.intended_account(None) == "tmichon_microsoft"


def test_switch_enabled_machine_local_resolves_with_explicit_repo(home: Path, monkeypatch):
    """The no-project fallback must still consult the *machine-local* tier
    (not only global) when the caller passes its known --repo -- e.g.
    launch-session.ps1 always does -- overriding the global tier."""
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "")
    global_dir = home / ".agent-worktrees"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(
        "copilot_identity_switch_enabled: false\n", encoding="utf-8"
    )
    machine_path = cfg.project_dir("myproj") / "config.yaml"
    machine_path.parent.mkdir(parents=True, exist_ok=True)
    machine_path.write_text("copilot_identity_switch_enabled: true\n", encoding="utf-8")
    assert copilot_identity.switch_enabled("myproj") is True


# ---------------------------------------------------------------------------
# The config-file switch_enabled() gate (replaces an env-var opt-out)
# ---------------------------------------------------------------------------

def test_switch_enabled_defaults_false(home: Path, monkeypatch):
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.Config(srcroot=str(home), machine="test", platform="windows"),
    )
    assert copilot_identity.switch_enabled() is False


def test_switch_enabled_true_when_configured(home: Path, monkeypatch):
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.Config(
            srcroot=str(home), machine="test", platform="windows",
            copilot_identity_switch_enabled=True,
        ),
    )
    assert copilot_identity.switch_enabled() is True


def test_switch_enabled_fails_closed_on_config_error(home: Path, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(cfg, "load_config", _boom)
    assert copilot_identity.switch_enabled() is False


def test_cli_ensure_is_noop_when_disabled(home: Path, monkeypatch):
    """The CLI dispatch -- the single path both launch-session.ps1 and a
    manual invocation go through -- must short-circuit before ever minting a
    token, shelling out, or reading ~/.copilot/config.json, when the machine
    has not opted in. No `_write_copilot_config` here on purpose: reading
    ``current_login()`` on this path would need it, and must not happen."""
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.Config(
            srcroot=str(home), machine="test", platform="windows",
            default_copilot_account="tmichon_microsoft",
        ),
    )

    def _boom(*_a, **_k):
        raise AssertionError("must not shell out while switching is disabled")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _boom)
    with output.capture_json_output() as buf:
        rc = copilot_identity_cli.cmd_copilot_identity_dispatch(["ensure", "--json"])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["status"] == "disabled"
    assert out["target"] == "tmichon_microsoft"
    assert out["previous"] is None


def test_cli_ensure_switches_when_enabled(home: Path, monkeypatch):
    _write_copilot_config(home, "ThomasMichon")
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.Config(
            srcroot=str(home), machine="test", platform="windows",
            default_copilot_account="tmichon_microsoft",
            copilot_identity_switch_enabled=True,
        ),
    )
    monkeypatch.setattr(copilot_identity.shutil, "which", lambda _name: "/usr/bin/x")
    monkeypatch.setattr(copilot_identity, "other_copilot_sessions_running", lambda: 0)

    class _Proc:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def _run(cmd, **kwargs):
        if cmd[:3] == ["gh", "auth", "token"]:
            return _Proc(0, stdout="fake-token-value\n")
        if cmd[:2] == ["copilot", "login"]:
            return _Proc(0, stdout="Signed in successfully.\n")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(copilot_identity.subprocess, "run", _run)
    with output.capture_json_output() as buf:
        rc = copilot_identity_cli.cmd_copilot_identity_dispatch(["ensure", "--json"])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["status"] == "switched"
    assert out["target"] == "tmichon_microsoft"


