"""Tests for the cross_repo_guard preToolUse hook decision logic (E1c, #878)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

# The guard ships as a standalone script under scripts/ (deployed to
# ~/.agent-worktrees/bin/), not as a package module -- load it by path.
_GUARD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cross_repo_guard.py"
_spec = importlib.util.spec_from_file_location("cross_repo_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.fixture
def guarded(tmp_path: Path) -> list[dict]:
    """One agent-guarded related repo with a real local checkout path."""
    repo = tmp_path / "SPO.Core"
    repo.mkdir()
    return [{"name": "SPO.Core", "delegate": "agent-bridge",
             "path": str(repo), "locus": {"machines": ["dev6"]}}]


def _write(tool, path, cwd):
    return {"toolName": tool, "cwd": str(cwd), "toolArgs": {"path": str(path)}}


def _shell(cmd, cwd):
    return {"toolName": "bash", "cwd": str(cwd), "toolArgs": {"command": cmd}}


# --- write-tool blocking ------------------------------------------------------

def test_write_into_guarded_repo_denies(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "src" / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d["permissionDecision"] == "deny"
    assert "SPO.Core" in d["permissionDecisionReason"]
    assert "agent-bridge" in d["permissionDecisionReason"]


def test_write_outside_guarded_repo_allows(tmp_path, guarded):
    p = _write("create", tmp_path / "elsewhere" / "y.ts", tmp_path)
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded) is None


def test_read_tool_into_guarded_allows(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "src" / "x.ts"
    p = {"toolName": "view", "cwd": str(tmp_path), "toolArgs": {"path": str(target)}}
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded) is None


def test_relative_write_path_resolves_against_cwd(tmp_path, guarded):
    # cwd INSIDE the guarded repo + a relative path -> resolves into it -> deny.
    cwd = Path(guarded[0]["path"]) / "src"
    p = {"toolName": "edit", "cwd": str(cwd), "toolArgs": {"path": "x.ts"}}
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


# --- shell blocking -----------------------------------------------------------

def test_shell_write_into_guarded_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    p = _shell(f'Set-Content "{gp}\\notes.md" "hi"', tmp_path)
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_into_guarded_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    p = _shell(f'git -C "{gp}" commit -m x', tmp_path)
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_read_into_guarded_allows(tmp_path, guarded):
    gp = guarded[0]["path"]
    p = _shell(f'cat "{gp}\\README.md"', tmp_path)
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded) is None


# -- false-positive regressions (dotfiles#1144), mirrored from anchor guard ----

def test_shell_readonly_git_with_fd_redirect_and_path_in_var_allows(
    tmp_path, guarded
):
    gp = guarded[0]["path"]
    cmd = f'$a="{gp}"; cd $a; git fetch origin --quiet 2>&1 | Out-Null; git log'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_guarded_path_in_quoted_body_allows(tmp_path, guarded):
    gp = guarded[0]["path"]
    body = f'mentions `{gp}` and `Set-Content` and `git commit` as prose'
    cmd = f'gh issue create --repo o/r --body "{body}"'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_fd_dup_redirect_is_not_a_write(tmp_path, guarded):
    gp = guarded[0]["path"]
    assert guard.decide(_shell(f'cat "{gp}\\x" 2>&1', tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_git_read_dashC_guarded_allows(tmp_path, guarded):
    gp = guarded[0]["path"]
    assert guard.decide(_shell(f'git -C "{gp}" log --oneline', tmp_path),
                        env={}, home=tmp_path, guarded_roots=guarded) is None


def test_shell_git_commit_from_guarded_cwd_denies(tmp_path, guarded):
    # A repo-scoped git write with cwd inside the guarded repo (no path named).
    gp = guarded[0]["path"]
    d = guard.decide(_shell("git commit -m x", gp), env={},
                     home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_into_guarded_then_git_commit_denies(tmp_path, guarded):
    # `cd <guarded>; git commit` from an unrelated tool cwd must be caught.
    gp = guarded[0]["path"]
    d = guard.decide(_shell(f'cd "{gp}"; git commit -m x', tmp_path),
                     env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_variable_target_does_not_move_cwd(tmp_path, guarded):
    gp = guarded[0]["path"]
    cmd = f'$a="{gp}"; cd $a; git commit -m x'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_redirect_into_guarded_still_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    d = guard.decide(_shell(f'echo hi > "{gp}\\note.txt"', tmp_path),
                     env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_sudo_prefixed_write_into_guarded_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    d = guard.decide(_shell(f'sudo rm -rf "{gp}/src"', tmp_path),
                     env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


# --- modes + kill switches ----------------------------------------------------

def test_mode_off_env_allows(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={"CROSS_REPO_GUARD": "off"}, home=tmp_path,
                        guarded_roots=guarded) is None
    assert guard.decide(p, env={"CROSS_REPO_GUARD_MODE": "off"}, home=tmp_path,
                        guarded_roots=guarded) is None


def test_mode_warn_returns_additional_context(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={"CROSS_REPO_GUARD_MODE": "warn"}, home=tmp_path,
                     guarded_roots=guarded)
    assert d and "additionalContext" in d and "permissionDecision" not in d


def test_mode_ask_returns_ask(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={"CROSS_REPO_GUARD_MODE": "ask"}, home=tmp_path,
                     guarded_roots=guarded)
    assert d and d["permissionDecision"] == "ask"


# --- break-glass --------------------------------------------------------------

def test_active_break_glass_allows(tmp_path, guarded):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"SPO.Core": {"expires_at_ms": (time.time() + 600) * 1000}}
    }), encoding="utf-8")
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={}, home=home, guarded_roots=guarded) is None


def test_expired_break_glass_still_denies(tmp_path, guarded):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"SPO.Core": {"expires_at_ms": (time.time() - 60) * 1000}}
    }), encoding="utf-8")
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={}, home=home, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


# --- runtime resolution (#1089: no dependence on the fragile PATH binstub) -----

def test_strip_nt_prefix():
    assert guard._strip_nt_prefix("\\??\\C:\\slot") == "C:\\slot"
    assert guard._strip_nt_prefix("\\\\?\\C:\\slot") == "C:\\slot"
    assert guard._strip_nt_prefix("C:\\slot") == "C:\\slot"


def _make_slot(root: Path, ver: str) -> Path:
    """Create versions/<ver> with a runtime python and return that python path."""
    slot = root / "versions" / ver
    py = guard._slot_python(slot)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text(
        json.dumps({
            "version": ver,
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    return py


def test_runtime_argv_prefers_current_version(tmp_path):
    root = tmp_path / ".agent-worktrees"
    _make_slot(root, "1.5.3-dev100")  # older slot present too
    py = _make_slot(root, "1.5.3-dev200")
    (root / "current-version").write_text("1.5.3-dev200", encoding="utf-8")
    assert guard._runtime_argv(root) == [str(py), "-m", "agent_worktrees"]


def test_runtime_argv_falls_back_to_newest_slot_without_marker(tmp_path):
    root = tmp_path / ".agent-worktrees"
    _make_slot(root, "1.5.3-dev9")
    py = _make_slot(root, "1.5.3-dev10")
    # No current-version marker -> newest slot wins.
    assert guard._runtime_argv(root) == [str(py), "-m", "agent_worktrees"]


def test_runtime_argv_ignores_stale_marker_pointing_at_missing_slot(tmp_path):
    root = tmp_path / ".agent-worktrees"
    py = _make_slot(root, "1.5.3-dev100")
    (root / "current-version").write_text("1.5.3-dev999", encoding="utf-8")  # gone
    # Marker slot has no python -> fall through to the newest present slot.
    assert guard._runtime_argv(root) == [str(py), "-m", "agent_worktrees"]


def test_runtime_argv_none_when_no_runtime(tmp_path, monkeypatch):
    root = tmp_path / ".agent-worktrees"
    root.mkdir()
    monkeypatch.setattr(guard.shutil, "which", lambda _n: None)
    assert guard._runtime_argv(root) is None


def test_run_related_warns_once_when_runtime_unresolved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "_runtime_argv", lambda: None)
    guard._RUNTIME_UNRESOLVED_WARNED = False
    assert guard._run_related(["list", "--json"], str(tmp_path)) is None
    first = capsys.readouterr().err
    assert "INACTIVE" in first and "cross-repo-guard" in first
    # A second call does not re-warn (one-time).
    assert guard._run_related(["list", "--json"], str(tmp_path)) is None
    assert capsys.readouterr().err == ""


def test_run_related_suppresses_console_when_resolver_falls_back_to_cmd(
    tmp_path, monkeypatch
):
    # _runtime_argv's own docstring documents a rare last-resort fallback to
    # the PATH .cmd binstub, which Windows must interpret through a fresh
    # cmd.exe -- CREATE_NO_WINDOW must be set so that console never flashes.
    monkeypatch.setattr(
        guard, "_runtime_argv", lambda: [r"C:\bin\agent-worktrees.CMD"]
    )
    captured = {}

    def fake_run(argv, *, cwd, capture_output, text, timeout, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)

        class _Result:
            returncode = 0
            stdout = "{}"

        return _Result()

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    monkeypatch.setattr(guard, "_IS_WIN", True)
    assert guard._run_related(["list", "--json"], str(tmp_path)) == "{}"
    assert captured["kwargs"].get("creationflags") == 0x08000000


# --- empty guarded set / fail-open --------------------------------------------

def test_no_guarded_repos_allows(tmp_path):
    target = tmp_path / "anything" / "x.ts"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=[]) is None


# --- guarded-root discovery (injected runner) ---------------------------------

def test_discover_filters_non_delegated_and_pathless(monkeypatch, tmp_path):
    real = tmp_path / "GuardedRepo"
    real.mkdir()

    def fake_related(args, cwd):
        if args[0] == "list":
            return json.dumps({"related": [
                {"name": "GuardedRepo", "delegate": "agent-bridge"},
                {"name": "PlainRepo", "delegate": "none"},
                {"name": "RemoteOnly", "delegate": "agent-codespaces"},
            ]})
        if args[0] == "show":
            name = args[1]
            if name == "GuardedRepo":
                return json.dumps({"registry": {"path": str(real)},
                                   "locus": {"machines": ["dev6"]}})
            return json.dumps({"registry": None})  # no local checkout
        return None

    monkeypatch.setattr(guard, "_run_related", fake_related)
    roots, complete = guard._discover_guarded_roots(str(tmp_path))
    assert complete
    names = {g["name"] for g in roots}
    assert names == {"GuardedRepo"}  # plain (none) + pathless (RemoteOnly) dropped
    assert roots[0]["path"] == str(real)


def test_load_guarded_roots_cache_roundtrip(monkeypatch, tmp_path):
    home = tmp_path / "home"
    calls = {"n": 0}

    def fake_discover(root, deadline=None):
        calls["n"] += 1
        return guard._DiscoveryResult(
            [{"name": "X", "delegate": "agent-bridge", "path": str(tmp_path)}],
            True,
        )

    monkeypatch.setattr(guard, "_discover_guarded_roots", fake_discover)
    r1 = guard.load_guarded_roots(str(tmp_path), home)
    r2 = guard.load_guarded_roots(str(tmp_path), home)
    assert r1 == r2 and calls["n"] == 1  # second call served from cache


def test_deadline_discovery_does_not_publish_partial_cache(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(
        guard,
        "_discover_guarded_roots",
        lambda root, deadline: guard._DiscoveryResult([], False),
    )
    assert guard.load_guarded_roots(
        str(tmp_path), home, deadline=guard.time.monotonic() + 1
    ) == []
    assert not guard._cache_path(str(tmp_path), home).exists()


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_complete_discovery_caches_cold_and_warm_decisions(
    monkeypatch, tmp_path, bounded, empty
):
    root = tmp_path / "consumer"
    root.mkdir()
    (root / ".git").mkdir()
    target = tmp_path / "delegated"
    calls = []

    def related(args, cwd, deadline=None):
        calls.append(args[0])
        assert cwd == str(root)
        if args[0] == "list":
            return json.dumps({"related": [] if empty else [
                {"name": "delegated", "delegate": "agent-bridge"}
            ]})
        return json.dumps({"registry": {"path": str(target)}})

    monkeypatch.setattr(guard, "_run_related", related)
    deadline = time.monotonic() + 23 if bounded else None
    for _ in range(2):
        decision = guard.decide(
            _write("create", target / "example.txt", root),
            env={}, home=tmp_path, deadline=deadline,
        )
        if empty:
            assert decision is None
        else:
            assert decision["permissionDecision"] == "deny"
    assert calls == (["list"] if empty else ["list", "show"])
    assert guard._cache_path(str(root), tmp_path).exists()


@pytest.mark.parametrize("deadline", [None, float("inf")])
@pytest.mark.parametrize("bad_response", [
    None, "", "{", "null", "[]", "{}", '{"related": null}',
    '{"related": {}}', '{"related": [null]}',
    '{"related": [{}]}', '{"related": [{"name": "missing-delegate"}]}',
    '{"related": [{"name": "bad", "delegate": []}]}',
    '{"related": [{"delegate": "agent-bridge"}]}',
])
def test_failed_or_malformed_listing_is_not_cached(
    monkeypatch, tmp_path, deadline, bad_response
):
    monkeypatch.setattr(guard, "_run_related", lambda *args: bad_response)
    assert guard.load_guarded_roots(str(tmp_path), tmp_path, deadline) == []
    assert not guard._cache_path(str(tmp_path), tmp_path).exists()


@pytest.mark.parametrize("bounded", [False, True])
@pytest.mark.parametrize("bad_response", [
    None, "", "{", "null", "[]", "{}",
    '{"registry": []}', '{"registry": {}}', '{"registry": {"path": 123}}',
    '{"registry": {"path": "/delegated"}, "locus": []}',
])
def test_partial_discovery_keeps_known_roots_but_is_not_cached(
    monkeypatch, tmp_path, bounded, bad_response
):
    def related(args, cwd, deadline=None):
        if args[0] == "list":
            return json.dumps({"related": [
                {"name": "known", "delegate": "agent-bridge"},
                {"name": "failed", "delegate": "agent-bridge"},
            ]})
        if args[1] == "known":
            return json.dumps({"registry": {"path": str(tmp_path / "known")}})
        return bad_response

    monkeypatch.setattr(guard, "_run_related", related)
    deadline = time.monotonic() + 23 if bounded else None
    roots = guard.load_guarded_roots(str(tmp_path), tmp_path, deadline)
    assert [r["name"] for r in roots] == ["known"]
    assert not guard._cache_path(str(tmp_path), tmp_path).exists()
    decision = guard.decide(
        _write("edit", tmp_path / "known" / "x", tmp_path),
        env={}, home=tmp_path, guarded_roots=roots,
    )
    assert decision["permissionDecision"] == "deny"


@pytest.mark.parametrize("expire_at", ["before-list", "after-first", "after-last"])
def test_deadline_expiry_never_publishes_snapshot(monkeypatch, tmp_path, expire_at):
    clock = [2.0 if expire_at == "before-list" else 0.0]
    calls = []

    def related(args, cwd, deadline=None):
        calls.append(args[0])
        if args[0] == "list":
            return json.dumps({"related": [
                {"name": name, "delegate": "agent-bridge"}
                for name in ("first", "last")
            ]})
        if (args[1] == "first" and expire_at == "after-first") or args[1] == "last":
            clock[0] = 2.0
        return json.dumps({"registry": {"path": str(tmp_path / args[1])}})

    monkeypatch.setattr(guard.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(guard, "_run_related", related)
    roots = guard.load_guarded_roots(str(tmp_path), tmp_path, deadline=1.0)
    assert len(roots) == {"before-list": 0, "after-first": 1, "after-last": 2}[expire_at]
    assert not guard._cache_path(str(tmp_path), tmp_path).exists()
    if expire_at == "before-list":
        assert calls == []


@pytest.mark.parametrize("age", [301, -10, float("nan"), "invalid", None])
def test_invalid_or_expired_cache_is_not_reused(monkeypatch, tmp_path, age):
    cache = guard._cache_path(str(tmp_path), tmp_path)
    cache.parent.mkdir(parents=True)
    computed_at = time.time() - age if isinstance(age, (int, float)) else age
    cache.write_text(json.dumps({
        "version": guard._CACHE_VERSION, "computed_at": computed_at, "roots": [],
    }), encoding="utf-8")
    monkeypatch.setattr(
        guard, "_discover_guarded_roots",
        lambda *args: guard._DiscoveryResult([{"name": "fresh"}], True),
    )
    assert guard.load_guarded_roots(str(tmp_path), tmp_path) == [{"name": "fresh"}]


def test_cache_scope_and_legacy_snapshot(monkeypatch, tmp_path):
    root = str(tmp_path / "one")
    home = tmp_path / "home"
    cache = guard._cache_path(root, home)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({
        "computed_at": time.time(), "roots": [],
    }), encoding="utf-8")
    calls = []

    def discover(root, deadline=None):
        calls.append(root)
        return guard._DiscoveryResult([{"name": root}], True)

    monkeypatch.setattr(guard, "_discover_guarded_roots", discover)
    for scoped_root, scoped_home in [
        (root, home), (root, home),
        (str(tmp_path / "two"), home), (root, tmp_path / "other-home"),
    ]:
        assert guard.load_guarded_roots(scoped_root, scoped_home) == [{"name": scoped_root}]
    assert calls == [root, str(tmp_path / "two"), root]


@pytest.mark.parametrize("command", [
    "Get-Item README.md", "Get-Date", "git log --oneline", "",
])
def test_readonly_shell_skips_topology(monkeypatch, tmp_path, command):
    def unexpected(*args):
        pytest.fail("read-only shell must not discover topology")

    monkeypatch.setattr(guard, "load_guarded_roots", unexpected)
    assert guard.decide(
        _shell(command, tmp_path), env={}, home=tmp_path,
        deadline=time.monotonic() + 23,
    ) is None


def test_fallback_pre_reuses_cache_across_real_topology_subprocesses(
    monkeypatch, tmp_path
):
    spec = importlib.util.spec_from_file_location(
        "cache_test_hook_client", _GUARD_PATH.with_name("hook_client.py")
    )
    client = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, client)
    spec.loader.exec_module(client)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("CROSS_REPO_GUARD", raising=False)
    monkeypatch.delenv("CROSS_REPO_GUARD_MODE", raising=False)
    monkeypatch.setattr(
        client, "_load_sibling",
        lambda name: guard if name == "cross_repo_guard.py" else None,
    )
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    (consumer / ".git").mkdir()
    target = tmp_path / "delegated"
    counter = tmp_path / "calls.jsonl"
    cli = tmp_path / "topology.py"
    cli.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"with Path({str(counter)!r}).open('a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[2] == 'list':\n"
        "    print(json.dumps({'related': ["
        "{'name': 'delegated', 'delegate': 'agent-bridge'}]}))\n"
        "else:\n"
        f"    print(json.dumps({{'registry': {{'path': {str(target)!r}}}}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard, "_runtime_argv", lambda: [sys.executable, str(cli)])
    for _ in range(2):
        decision = client._fallback_pre(
            _write("create", target / "never-written.txt", consumer), tmp_path
        )
        assert decision["permissionDecision"] == "deny"
    assert len(counter.read_text().splitlines()) == 2
    assert not (target / "never-written.txt").exists()


@pytest.mark.parametrize("complete", [False, True])
def test_selected_runtime_never_uses_ambient_binstub(monkeypatch, tmp_path, complete):
    root = tmp_path / "selected"
    monkeypatch.setattr(
        guard.shutil, "which",
        lambda _name: pytest.fail("selected installation must not consult PATH"),
    )
    if complete:
        python = _make_slot(root, "1.0.0")
        expected = [str(python), "-m", "agent_worktrees"]
    else:
        expected = None
    assert guard._runtime_argv(root, allow_legacy_fallback=False) == expected
