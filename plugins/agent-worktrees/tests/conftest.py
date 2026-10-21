"""Shared test fixtures for agent-worktrees tests."""

from __future__ import annotations

import json
import os
import re
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_worktrees import tracking


@pytest.fixture
def payload_subprocess_double(monkeypatch):
    """Classification tests retain their subprocess double; native Job tests opt out."""
    from agent_worktrees import update_acquisition

    monkeypatch.setattr(update_acquisition, "_WINDOWS", False)


@pytest.fixture(scope="session")
def native_bash():
    from native_shell_support import ShellUnavailable, resolve_native_bash

    try:
        return resolve_native_bash()
    except ShellUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def native_powershell():
    from native_shell_support import ShellUnavailable, resolve_native_powershell

    try:
        return resolve_native_powershell()
    except ShellUnavailable as exc:
        pytest.skip(str(exc))


@pytest.fixture(autouse=True)
def _isolate_identity_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_WORKTREES_IDENTITY_KEY", str(tmp_path / "absent-identity.key"))


@pytest.fixture
def keyed_identity_payload(monkeypatch):
    from agent_worktrees import identity_marker

    captured = {}
    key = b"k" * identity_marker.KEY_BYTES
    monkeypatch.setattr(identity_marker, "load_identity_key", lambda: key)

    class Cipher:
        def encrypt(self, nonce, plaintext, associated_data):
            import json

            captured.update(json.loads(plaintext))
            return b"test-ciphertext"

    monkeypatch.setattr(identity_marker, "_aesgcm", lambda resolved: Cipher())
    return captured


def _load_full_command_surface_once() -> None:
    """Restore the pre-lazy-dispatch full eager module surface for tests.

    Tests exercise `agent_worktrees.__main__` module attributes directly
    (`m._some_helper`, `monkeypatch.setattr(m, "_x", ...)`) as white-box unit
    tests of internal implementation, not as simulated CLI invocations. The
    agent-cli-lazy-dispatch effort deferred that surface's construction
    behind `main()`'s own dispatch decision (see `_load_full_command_surface`
    / `_ensure_cluster_loaded` in `agent_worktrees/__main__.py`) so a real
    fast-tracked invocation never pays for it -- but that means it is no
    longer populated merely by `import agent_worktrees.__main__`. Force it
    once, session-wide, so every test keeps seeing the same fully-populated
    module it always has.
    """
    from agent_worktrees import __main__ as m

    m._load_full_command_surface()


_load_full_command_surface_once()


@pytest.fixture(autouse=True)
def _disable_resident_monitor_processes():
    """Unit tests opt in explicitly when resident monitor behavior is under test."""
    import os

    key = "AGENT_WORKTREES_STATUS_MONITOR"
    prior = os.environ.get(key)
    os.environ[key] = "0"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


@pytest.fixture(autouse=True)
def _assume_interactive_terminal(monkeypatch):
    """Default the non-interactive-bare-invocation guard to "interactive".

    Under pytest's default output capture, ``sys.stdin.isatty()`` is False
    (confirmed empirically), which would make every bare-invocation test
    that exercises the launch/Manager seam spuriously hit
    :func:`agent_worktrees.__main__.cmd_noninteractive_bare` instead of the
    behavior it actually means to test. Default the guard closed here, the
    same way this file already isolates the resident-monitor default above;
    a test of the guard itself opts back in explicitly.
    """
    from agent_worktrees import __main__ as m

    monkeypatch.setattr(m, "_is_noninteractive_invocation", lambda: False)


# ---------------------------------------------------------------------------
# Headless subprocess launches (Windows) -- keep the suite from flashing windows.
#
# Many tests shell out to REAL processes (git in the repo/anchor fixtures, bash
# in the installer tests, pwsh/psmux in the launch tests). On Windows, a console
# child spawned by a pytest process that has no console of its own allocates a
# NEW console window -- so a full-suite run flickers dozens of windows. Force
# ``CREATE_NO_WINDOW`` onto every real ``Popen`` for the duration of the session
# so the suite runs headless.
#
# This touches only REAL spawns: tests that mock ``subprocess.run`` / ``Popen``
# replace the attribute and never reach this ``__init__``. A deliberate
# ``CREATE_NEW_CONSOLE`` request (none in-tree today) is left alone -- the two
# flags are mutually exclusive. No-op off Windows.
# ---------------------------------------------------------------------------

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_CONSOLE = 0x00000010


def _headless_creationflags(flags: int) -> int:
    """OR ``CREATE_NO_WINDOW`` onto ``flags`` unless a new console was requested.

    Pure (no platform check) so it is unit-testable; the caller applies it only
    on Windows. ``CREATE_NO_WINDOW`` and ``CREATE_NEW_CONSOLE`` are mutually
    exclusive, so an explicit new-console request is passed through untouched.
    """
    if flags & _CREATE_NEW_CONSOLE:
        return flags
    return flags | _CREATE_NO_WINDOW


def pytest_configure(config):
    import sys

    if sys.platform != "win32":
        return
    import subprocess

    orig_init = subprocess.Popen.__init__
    if getattr(orig_init, "_aw_headless", False):
        return

    def _headless_init(self, *args, **kwargs):
        kwargs["creationflags"] = _headless_creationflags(
            kwargs.get("creationflags", 0))
        return orig_init(self, *args, **kwargs)

    _headless_init._aw_headless = True
    _headless_init._aw_orig = orig_init
    subprocess.Popen.__init__ = _headless_init


def pytest_unconfigure(config):
    import sys

    if sys.platform != "win32":
        return
    import subprocess

    cur = subprocess.Popen.__init__
    orig = getattr(cur, "_aw_orig", None)
    if orig is not None:
        subprocess.Popen.__init__ = orig

# ---------------------------------------------------------------------------
# Global HOME isolation -- the suite must NEVER touch real machine state.
#
# The agent-worktrees registries resolve from the home directory via two paths:
#   * ``config._home()``  -> ``USERPROFILE`` (Windows) / ``Path.home()``, which
#     backs ``config.install_dir()`` -> ``~/.agent-worktrees/projects.yaml``.
#   * ``repos.py`` resolves ``~/.agent-worktrees/repos.yaml`` via ``Path.home()``
#     directly.
# A test that reaches a real registry *writer* (e.g. ``reconcile_binstubs`` ->
# ``prune_reserved_projects`` -> ``write_projects_registry``) while patching only
# the *reader* would otherwise clobber the developer's real
# ``~/.agent-worktrees/projects.yaml`` -- observed live as a projects.yaml
# reduced to a stray ``realproj`` entry (test-chamber #4349). Redirect HOME to a
# throwaway per-test dir so NO test can reach real state regardless of what it
# patches, and running the suite (even concurrently with an install) can never
# mutate the host. Tests that need finer control still layer their own patches
# (e.g. ``monkeypatch_config``) on top of this safe default.
#
# This ALSO isolates ``copilot_launch_prefs.resolve_launch_pref_flags()`` from
# the real ``~/.copilot/settings.json`` plugin-wide, including in test modules
# that never import ``copilot_launch_prefs`` directly (e.g.
# ``test_profile_assignment.py``'s exact-command assertions). That module's
# ``from pathlib import Path`` is the identical ``pathlib.Path`` class patched
# below -- patching the classmethod once here therefore covers every caller,
# with no second patch of the same global attribute needed (a second autouse
# fixture doing so would instead *conflict* with this one, each overwriting
# the other's fake home for the duration of a test).
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_agent_worktrees_home(tmp_path_factory):
    import os
    import pathlib

    # Use tmp_path_factory (a SEPARATE dir), never the test's own ``tmp_path`` --
    # otherwise this fake home would show up inside a test's tmp_path and break
    # tests that assert on ``tmp_path.iterdir()``.
    fake_home = tmp_path_factory.mktemp("aw-home")
    (fake_home / ".agent-worktrees").mkdir(parents=True, exist_ok=True)

    # Uses os.environ + a manual attribute save/restore (NOT monkeypatch), so
    # this autouse fixture introduces no ``monkeypatch`` dependency that could
    # reorder fixture teardown -- the same discipline as ``_isolate_pivots``
    # below. (Requesting ``monkeypatch`` here pulled its teardown after
    # ``_reset_active_project``'s, which then called a still-patched
    # ``set_active_project`` and errored.)
    saved_agent_home = os.environ.get("AGENT_HOME")
    saved_userprofile = os.environ.get("USERPROFILE")
    saved_home = os.environ.get("HOME")
    saved_path_home = pathlib.Path.__dict__.get("home")

    os.environ["AGENT_HOME"] = str(fake_home)
    os.environ["USERPROFILE"] = str(fake_home)
    os.environ["HOME"] = str(fake_home)
    # ``repos.py`` and several helpers call ``Path.home()`` directly, so the env
    # vars alone are not enough -- patch the resolver on the class too.
    pathlib.Path.home = classmethod(lambda cls: fake_home)
    try:
        yield fake_home
    finally:
        if saved_path_home is not None:
            pathlib.Path.home = saved_path_home
        else:  # pragma: no cover - defensive; home is defined on Path in CPython
            try:
                del pathlib.Path.home
            except AttributeError:
                pass
        for key, val in (
            ("AGENT_HOME", saved_agent_home),
            ("USERPROFILE", saved_userprofile),
            ("HOME", saved_home),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


# ---------------------------------------------------------------------------
# Isolate the in-process active-project / assumed-CWD state between tests.
# These module globals are set by main() during CWD/--project resolution;
# without a reset a test that runs main() (or set_active_project) would leak
# its project into unrelated tests.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_active_project():
    import os

    from agent_worktrees import config as _cfg

    reset_active_project = _cfg.set_active_project
    _saved_handoff = os.environ.get("AGENT_WORKTREES_HANDOFF_TOKEN")
    reset_active_project(None)
    os.environ.pop("AGENT_WORKTREES_HANDOFF_TOKEN", None)
    yield
    reset_active_project(None)
    if _saved_handoff is None:
        os.environ.pop("AGENT_WORKTREES_HANDOFF_TOKEN", None)
    else:
        os.environ["AGENT_WORKTREES_HANDOFF_TOKEN"] = _saved_handoff

# ---------------------------------------------------------------------------
# Path fixtures — redirect config helpers to tmp dirs
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_pivots(tmp_path_factory):
    """Point the picker's pivot-manifest registry at an empty tmp dir so tests
    are hermetic regardless of what a dev machine has deployed under
    ``~/.agent-worktrees/pivots/`` (e.g. the agent-dispatch manifest). Tests
    that exercise discovery override this env var explicitly.

    Also isolates the marketplace plugin-install root that ``ensure_pivots``
    restores from (#2180): without it, the picker's self-heal would scan the
    real ``~/.copilot/installed-plugins`` and copy a contributed manifest back
    into the (otherwise empty) tmp pivots dir, re-introducing the very ambient
    pivot this fixture exists to suppress.

    Uses ``os.environ`` directly (not ``monkeypatch``) so this autouse fixture
    introduces no dependency that could reorder fixture teardown.
    """
    import os

    empty = tmp_path_factory.mktemp("empty-pivots")
    empty_plugins = tmp_path_factory.mktemp("empty-plugins")
    saved = os.environ.get("AGENT_WORKTREES_PIVOTS_DIR")
    saved_plugins = os.environ.get("AGENT_WORKTREES_PLUGINS_DIR")
    os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = str(empty)
    os.environ["AGENT_WORKTREES_PLUGINS_DIR"] = str(empty_plugins)
    yield
    if saved is None:
        os.environ.pop("AGENT_WORKTREES_PIVOTS_DIR", None)
    else:
        os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = saved
    if saved_plugins is None:
        os.environ.pop("AGENT_WORKTREES_PLUGINS_DIR", None)
    else:
        os.environ["AGENT_WORKTREES_PLUGINS_DIR"] = saved_plugins


@pytest.fixture(autouse=True)
def _assume_valid_claimant_worktree(monkeypatch):
    """Default ``pr_cli.require_claimant_worktree``'s underlying CWD->worktree
    resolution to "resolves cleanly" for every test.

    :func:`agent_worktrees.worktree_identity._infer_worktree_id_from_cwd`
    reads the REAL git worktree structure of wherever the test process's CWD
    happens to be (unaffected by ``_isolate_agent_worktrees_home`` above,
    which only isolates registries under HOME) -- so left unmocked, whether
    a ``pr-watch``/``pr-merge`` dispatcher test's claimant check passes would
    depend on the ambient checkout the suite happens to run from, not the
    test's own fixtures. Default it to a fixed, deterministic worktree id; a
    test of the claimant guard itself (or of CWD-identity resolution)
    overrides this explicitly.
    """
    from agent_worktrees import worktree_identity

    monkeypatch.setattr(
        worktree_identity, "_infer_worktree_id_from_cwd",
        lambda config=None: "test-claimant-worktree",
    )


@pytest.fixture
def tmp_tracking_dir(tmp_path: Path) -> Path:
    """Temporary tracking directory for worktree YAMLs."""
    d = tmp_path / "worktrees"
    d.mkdir()
    return d


@pytest.fixture
def tmp_session_state_dir(tmp_path: Path) -> Path:
    """Temporary ~/.copilot/session-state/ equivalent."""
    d = tmp_path / "session-state"
    d.mkdir()
    return d


@pytest.fixture
def monkeypatch_config(monkeypatch, tmp_path: Path, tmp_tracking_dir: Path):
    """Patch config helpers to use tmp dirs."""
    from agent_worktrees import config as _cfg

    _cfg.set_active_project("test-project")
    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tmp_tracking_dir)
    monkeypatch.setattr(
        "agent_worktrees.config.project_dir",
        lambda name=None: tmp_path / f".{name or 'test-project'}",
    )
    monkeypatch.setattr(
        "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees"
    )

    # Fencing verbs (session_register/session_deregister/session_conclude/...)
    # resolve receiving-side project authority via
    # ``execution_spaces.require_project_record_mutation`` ->
    # ``config.load_project_config(record.repo)`` before any mutation. The
    # isolated test HOME has no real registered project, so without this a
    # record naming any repo not explicitly pre-registered would raise
    # ``ExecutionSpaceError`` before reaching the behavior under test. Return
    # a minimal single-repo ``Config`` for whatever project name is actually
    # requested, with a REAL (empty) canonical ``machines.yaml`` at its
    # anchor: ``registry_for_config`` -> ``load_machines_yaml`` then finds the
    # canonical path directly and never falls through to its own
    # ``_overlay_machines_yaml_path`` probe (which calls the bare,
    # un-namespaced ``load_config()`` as a side effect) -- avoiding a spurious
    # extra ``load_config()`` call a caller-side test might assert against.
    # No ``execution_platform`` entries -> the authority check stays a no-op
    # (the same fail-open behavior a real non-multi-platform deployment gets).
    fake_anchor = tmp_path / ".aw-authority-fence-anchor"
    (fake_anchor / ".agent-worktrees").mkdir(parents=True, exist_ok=True)
    (fake_anchor / ".agent-worktrees" / "machines.yaml").write_text(
        "machines: {}\n", encoding="utf-8",
    )

    def _fake_load_project_config(name, **_kwargs):
        return _cfg.Config(
            srcroot=str(tmp_path), machine="test", platform="wsl", repo_name=name,
            repos={name: _cfg.RepoConfig(
                anchor=str(fake_anchor), worktree_root=str(tmp_tracking_dir),
            )},
        )

    monkeypatch.setattr(_cfg, "load_project_config", _fake_load_project_config)
    monkeypatch.setattr(
        "agent_worktrees.config_authority.load_project_authority_config",
        lambda name, **_kwargs: _fake_load_project_config(name),
    )


@pytest.fixture
def sample_record(tmp_tracking_dir: Path) -> tracking.WorktreeRecord:
    """A pre-built WorktreeRecord saved to the tracking dir."""
    rec = tracking.WorktreeRecord(
        worktree_id="test-wt-001",
        branch="worktree/test-wt-001",
        worktree_path="/tmp/test-worktree",
        repo="test-repo",
        machine="test-machine",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
    )
    tracking.save_record(rec, tmp_tracking_dir / f"{rec.worktree_id}.yaml")
    return rec


def make_session_dir(
    session_state_dir: Path,
    session_id: str,
    cwd: str,
    *,
    summary: str = "",
    updated_at: str = "2026-06-01T10:00:00.000Z",
    events_lines: list[str] | None = None,
    lock_pid: int | None = None,
    has_events_file: bool = True,
    context_pct: int | None = None,
    substatus: dict | None = None,
) -> Path:
    """Create a mock session directory with workspace.yaml and optional files."""
    sdir = session_state_dir / session_id
    sdir.mkdir(parents=True, exist_ok=True)

    ws_content = textwrap.dedent(f"""\
        id: {session_id}
        cwd: {cwd}
        git_root: {cwd}
        branch: main
        name: Test Session
        summary: {summary or 'Test Session'}
        created_at: {updated_at}
        updated_at: {updated_at}
    """)
    (sdir / "workspace.yaml").write_text(ws_content)

    if has_events_file:
        lines = events_lines or []
        (sdir / "events.jsonl").write_text("\n".join(lines) + "\n" if lines else "")

    if lock_pid is not None:
        (sdir / f"inuse.{lock_pid}.lock").write_text("")

    if context_pct is not None:
        import json
        (sdir / "context.json").write_text(json.dumps({
            "sessionId": session_id,
            "utilizationPct": context_pct,
            "updatedAt": updated_at,
        }))

    if substatus is not None:
        import json
        (sdir / "substatus.json").write_text(json.dumps(substatus))

    return sdir


# ---------------------------------------------------------------------------
# PR-mode repo fixture (shared by test_pr_ops + test_providers)
# ---------------------------------------------------------------------------

from agent_worktrees import config as cfg  # noqa: E402
from agent_worktrees import git_ops  # noqa: E402


def _pr_git(*args: str, cwd) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


@contextmanager
def _seed_directory(tmp_path_factory, key):
    """Publish one immutable seed within the runner-owned plugin sandbox."""
    if not re.fullmatch(r"[a-z0-9-]+", key):
        raise ValueError(f"invalid fixture seed key: {key!r}")
    sandbox = os.environ.get("COPILOT_EXTENSIONS_TEST_SANDBOX")
    if os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1" or not sandbox:
        yield tmp_path_factory.mktemp(key), True
        return
    parent = Path(sandbox)
    if not parent.is_absolute() or not parent.is_dir() or parent.is_symlink():
        raise ValueError("fixture seeds require an existing real absolute test sandbox")
    root = parent / "s"
    if root.is_symlink():
        raise ValueError("fixture seed cache cannot be a symlink")
    root.mkdir(exist_ok=True)
    seed = root / key
    ready = root / f"{key}.ready"
    if ready.is_symlink() or ready.with_suffix(".lock").is_symlink():
        raise ValueError("fixture seed control files cannot be symlinks")
    expected = {"version": 1, "key": key, "path": str(seed)}
    with tracking._RecordLock(ready, timeout=30, require_sidecar=True):
        if ready.exists():
            readiness = json.loads(ready.read_text("utf-8"))
            if (
                not seed.is_dir()
                or seed.is_symlink()
                or readiness != expected
                or type(readiness["version"]) is not int
            ):
                raise ValueError(f"invalid fixture seed readiness: {key}")
            yield seed, False
            return
        if seed.exists() or seed.is_symlink():
            raise RuntimeError(f"incomplete fixture seed: {key}")
        seed.mkdir()
        yield seed, True
        tracking._atomic_write(ready, json.dumps(expected))


@pytest.fixture
def record_repo(tmp_path: Path, monkeypatch):
    """Private persisted-record inputs without native Git setup."""
    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()
    worktree_id = "test-wt-20260618-aaaa"
    wt_root = tmp_path / "worktrees"
    wt_path = wt_root / worktree_id
    wt_path.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(tmp_path / "anchor"), worktree_root=str(wt_root),
            default_branch="master", remote="origin",
            pr=cfg.PRConfig(
                enabled=True, provider="gitea", branch_prefix="feature",
                head_scheme="snapshot", auto_open=False,
            ),
        )},
    )
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: tracking_d)
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(wt_path),
        "ext", "test", "linux", tracking_d,
    )
    return config, worktree_id, wt_path, tmp_path / "remote.git"


@pytest.fixture
def pr_repo(tmp_path: Path, monkeypatch):
    """A bare 'remote' + anchor + a worktree branch with two commits.

    Returns (config, worktree_id, worktree_path, remote_dir).  Patches
    tracking_dir so records land in a tmp directory.  PR mode is enabled with
    ``auto_open=False`` so create_pr exercises only the git side unless a test
    opts in.
    """
    remote_dir = tmp_path / "remote.git"
    anchor = tmp_path / "anchor"
    wt_root = tmp_path / "worktrees"
    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()

    git_ops.git("init", "--bare", "-b", "master", str(remote_dir))

    git_ops.git("init", "-b", "master", str(anchor))
    _pr_git("config", "user.email", "t@example.com", cwd=anchor)
    _pr_git("config", "user.name", "Test", cwd=anchor)
    (anchor / "README.md").write_text("base\n")
    _pr_git("add", "-A", cwd=anchor)
    _pr_git("commit", "-m", "initial", cwd=anchor)
    _pr_git("remote", "add", "origin", str(remote_dir), cwd=anchor)
    _pr_git("push", "-u", "origin", "master", cwd=anchor)

    worktree_id = "test-wt-20260618-aaaa"
    wt_path = wt_root / worktree_id
    wt_root.mkdir(parents=True, exist_ok=True)
    git_ops.git(
        "worktree", "add", str(wt_path), "-b", f"worktree/{worktree_id}",
        "origin/master", cwd=str(anchor),
    )
    _pr_git("config", "user.email", "t@example.com", cwd=wt_path)
    _pr_git("config", "user.name", "Test", cwd=wt_path)
    (wt_path / "a.txt").write_text("one\n")
    _pr_git("add", "-A", cwd=wt_path)
    _pr_git("commit", "-m", "work 1", cwd=wt_path)
    (wt_path / "b.txt").write_text("two\n")
    _pr_git("add", "-A", cwd=wt_path)
    _pr_git("commit", "-m", "work 2", cwd=wt_path)

    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(wt_root),
            default_branch="master", remote="origin",
            # Pin the legacy ``snapshot`` scheme here so the base fixture keeps
            # exercising it explicitly. The default is now ``refspec`` (#1815);
            # refspec is covered by the ``_refspec_config`` tests + the config
            # default tests, which override/assert the scheme directly.
            pr=cfg.PRConfig(enabled=True, provider="gitea", branch_prefix="feature",
                            head_scheme="snapshot", auto_open=False),
        )},
    )

    monkeypatch.setattr(
        "agent_worktrees.config.tracking_dir", lambda name=None: tracking_d
    )
    # pr_ops helpers that are called without an explicit ``config`` fall back to
    # ``cfg.load_config()``, which resolves the on-disk config for the active
    # project. In tests there is no active project, so pin load_config to this
    # fixture's config -- otherwise the call raises (no project) or, worse,
    # silently reads a real ~/.<project>/config.yaml when the launching shell
    # leaked WORKTREE_PROJECT, making these tests order/environment dependent.
    monkeypatch.setattr("agent_worktrees.config.load_config", lambda *a, **k: config)

    tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(wt_path),
        "ext", "test", "linux", tracking_d,
    )

    return config, worktree_id, wt_path, remote_dir


class _SubprocessRunInstaller:
    """``installer_run`` process stand-in that delegates to ``subprocess.run``.

    Many update tests fake an installer by patching ``subprocess.run`` (its
    exit code, a ``TimeoutExpired``, the argv/env/cwd it receives). This keeps
    those fakes meaningful now that installers run through
    :func:`agent_worktrees.installer_run.run_installer`; tests of the real
    process-tree behavior opt out with ``@pytest.mark.real_installer_processes``.
    """

    def __init__(self, argv, cwd, env, capture, output_fd=None):
        self._call = (list(argv), cwd, env, capture, output_fd)
        self.returncode = None
        self.pid = 0

    def communicate(self, timeout=None):
        import subprocess

        argv, cwd, env, capture, output_fd = self._call
        stdio = (
            {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True}
            if capture else {}
        )
        if output_fd is not None:
            stdio = {"stdout": output_fd, "stderr": output_fd}
        result = subprocess.run(argv, cwd=cwd, timeout=timeout, env=env, **stdio)
        self.returncode = result.returncode
        return (getattr(result, "stdout", None) if capture else None), None


@pytest.fixture(autouse=True)
def _installer_processes_through_subprocess_run(request, monkeypatch):
    if request.node.get_closest_marker("real_installer_processes"):
        return
    from agent_worktrees import installer_run

    monkeypatch.setattr(
        installer_run, "_spawn",
        lambda argv, cwd, env, capture, output_fd=None: (
            _SubprocessRunInstaller(argv, cwd, env, capture, output_fd), None,
        ),
    )
    monkeypatch.setattr(installer_run, "_kill_tree", lambda proc, job: "")
    if request.node.get_closest_marker("installer_receipts"):
        return
    # These fakes model an installer by its exit code alone: one that exits 0
    # is taken to have published its target receipt. Receipt judgment itself is
    # covered by tests marked ``installer_receipts`` / ``real_installer_processes``.
    judge = installer_run._judge
    monkeypatch.setattr(
        installer_run, "_judge",
        lambda rc, expired, target, deployed: judge(
            rc, expired, target, target if rc == 0 and not expired else deployed),
    )
