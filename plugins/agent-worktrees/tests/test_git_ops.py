"""Tests for agent_worktrees.git_ops — git wrappers and classification."""

from __future__ import annotations

import types
import typing
from pathlib import Path

import pytest

from agent_worktrees.git_ops import (
    GitError,
    WorktreeState,
    WorktreeStateInfo,
    git,
    is_cwd_inside,
    merge_squash,
    refine_state_with_session,
    resolve_to_anchor,
    worktree_suffix,
)

# ---------------------------------------------------------------------------
# git() wrapper
# ---------------------------------------------------------------------------

class TestGitWrapper:
    def test_successful_command(self, tmp_path: Path):
        """git() should capture stdout from a successful command."""
        # Use a real git command that works without a repo
        result = git("--version")
        assert result.returncode == 0
        assert "git version" in result.stdout

    def test_raises_git_error_on_failure(self, tmp_path: Path):
        """git() should raise GitError when check=True and command fails."""
        with pytest.raises(GitError) as exc_info:
            git("log", cwd=str(tmp_path))  # valid dir, not a git repo
        assert exc_info.value.returncode != 0

    def test_no_raise_when_check_false(self, tmp_path: Path):
        """git() with check=False should return result even on failure."""
        result = git("log", cwd=str(tmp_path), check=False)
        assert result.returncode != 0

    def test_git_error_attributes(self, tmp_path: Path):
        try:
            git("log", cwd=str(tmp_path))
        except GitError as e:
            assert e.returncode != 0
            assert isinstance(e.cmd, list)
            assert isinstance(e.stderr, str)


class TestPythonRuntimeEnvScrub:
    """#4552: a leaked PYTHONHOME/PYTHONPATH/PYTHONEXECUTABLE/VIRTUAL_ENV/
    UV_INTERNAL__PYTHONHOME/__PYVENV_LAUNCHER__ from this plugin's own
    runtime must never reach a spawned ``git`` child (or the
    ``repository_identity_env()`` probes), since that redirects any other
    interpreter the child in turn execs (e.g. a repo's pre-push hook) onto
    this plugin's version-mismatched stdlib/venv."""

    _LEAKED: typing.ClassVar[dict[str, str]] = {
        "PYTHONHOME": r"C:\fake\stale\home",
        "PYTHONPATH": r"C:\fake\stale\path",
        "PYTHONEXECUTABLE": r"C:\fake\stale\python.exe",
        "VIRTUAL_ENV": r"C:\fake\stale\venv",
        "UV_INTERNAL__PYTHONHOME": r"C:\fake\uv\internal",
        "__PYVENV_LAUNCHER__": r"C:\fake\stale\launcher.exe",
    }

    def test_git_scrubs_python_runtime_vars(self, monkeypatch):
        import agent_worktrees.git_ops as go
        for name, value in self._LEAKED.items():
            monkeypatch.setenv(name, value)
        captured = {}

        def fake_run(cmd, **kw):
            captured["env"] = kw.get("env")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        go.git("--version")
        for name in self._LEAKED:
            assert name not in captured["env"]

    def test_repository_identity_env_scrubs_python_runtime_vars(self, monkeypatch):
        import agent_worktrees.git_ops as go
        for name, value in self._LEAKED.items():
            monkeypatch.setenv(name, value)
        env = go.repository_identity_env()
        for name in self._LEAKED:
            assert name not in env


class TestNoHooks:
    """#3707: the plugin's mechanical git ops (squash re-commit / rebase)
    disable a repo's client-side guard hooks via ``-c core.hooksPath=`` so a
    branch-protection pre-commit/pre-rebase can't block or corrupt the flow.
    Server-side protection is unaffected (not a client hook). ``push()`` is
    NOT one of these ops (#3561): it must let a repo's real pre-push release
    guard run, so it never disables hooks -- see the ``TestPush`` class
    below."""

    def _capture(self, monkeypatch):
        import subprocess as _sp
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _sp.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        return seen

    def test_no_hooks_prepends_config(self, monkeypatch):
        seen = self._capture(monkeypatch)
        go.git("commit", "-m", "x", no_hooks=True)
        assert seen["cmd"][:3] == ["git", "-c", f"core.hooksPath={go._NO_HOOKS_PATH}"]
        assert seen["cmd"][3:] == ["commit", "-m", "x"]

    def test_default_keeps_hooks_enabled(self, monkeypatch):
        seen = self._capture(monkeypatch)
        go.git("commit", "-m", "x")
        assert seen["cmd"] == ["git", "commit", "-m", "x"]
        assert "core.hooksPath" not in " ".join(seen["cmd"])

    def test_squash_recommit_bypasses_hooks(self, monkeypatch):
        # The squash re-commit must run with hooks disabled (the #3707 root
        # cause: a branch-guard pre-commit blocking the soft-reset re-commit).
        calls = []

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False):
            calls.append((args, no_hooks))
            # merge-base / rev-list --count 2 so we reach the commit path.
            if args[:1] == ("rev-list",):
                out = "2"
            elif args[:1] == ("merge-base",):
                out = "deadbeef"
            elif args[:1] == ("rev-parse",):
                out = "cafef00d"
            else:
                out = ""
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        ok, reason = go.squash_branch("origin/master", "squashed", cwd=".")
        assert ok and reason is None
        commit_calls = [c for c in calls if c[0][:1] == ("commit",)]
        assert commit_calls and all(nh for _, nh in commit_calls)

    def test_rebase_bypasses_hooks(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout=None, no_hooks=False: (
            seen.update(args=a, no_hooks=no_hooks),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        assert go.rebase("origin/master", cwd=".") is True
        assert seen["args"][:1] == ("rebase",)
        assert seen["no_hooks"] is True


class TestPush:
    """#3561: unlike ``TestNoHooks``'s squash/rebase plumbing, ``push()`` is
    the terminal action that actually publishes to remote -- a real pre-push
    release guard (e.g. ``check-changefile-presence.py``) must be allowed to
    run and block a non-compliant push."""

    def test_push_does_not_bypass_hooks(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        seen = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout=None, no_hooks=False,
                            kill_tree=False: (
            seen.update(args=a, no_hooks=no_hooks),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        assert bool(go.push("origin", "main", cwd=".")) is True
        assert seen["args"][:1] == ("push",)
        assert seen["no_hooks"] is False

    def test_push_retry_also_does_not_bypass_hooks(self, monkeypatch):
        """The auth-fallback retry push (#900) must not bypass hooks either --
        it's still the same terminal publish action, just retried without the
        injected cross-account token."""
        monkeypatch.setattr(
            go, "_auth_config_args",
            lambda remote, *, cwd: ["-c", "http.extraheader=AUTHORIZATION: basic x"],
        )
        no_hooks_seen: list[bool] = []

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            no_hooks_seen.append(no_hooks)
            injected = "http.extraheader=AUTHORIZATION: basic x" in args
            rc = 1 if injected else 0
            return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "main", cwd=".")) is True
        assert no_hooks_seen == [False, False]  # neither the injected nor the fallback call


class TestPushTimeout:
    """A repo's pre-push hook re-invokes the full agent-worktrees binstub,
    which resolves its own runtime slot on every call and can stall for the
    same reasons a direct CLI invocation can (ThomasMichon/copilot-extensions
    #4547): a self-update racing the runtime-slot swap, or the mutex-gated
    self-provisioning path's own ~30-120s venv build. Left unbounded, that
    stall propagated into an indefinite ``push()`` hang with no diagnostic --
    exactly the failure mode that drove agents to bypass ``create-pr`` for a
    raw ``git push`` + ``gh pr create``, silently losing PR attribution."""

    def test_push_passes_default_timeout(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            captured["timeout"] = timeout
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "main", cwd=".")) is True
        assert captured["timeout"] == go.push_timeout.DEFAULT_PUSH_TIMEOUT

    def test_push_timeout_override(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            captured["timeout"] = timeout
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "main", cwd=".", timeout=5)) is True
        assert captured["timeout"] == 5

    def test_push_none_timeout_preserves_unbounded(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            captured["timeout"] = timeout
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "main", cwd=".", timeout=None)) is True
        assert captured["timeout"] is None

    def test_push_kills_tree_on_stall(self, monkeypatch):
        """push() must ask git() to kill the WHOLE process tree on a stall
        (Copilot review finding on PR #4600), not just rely on the default
        (tree-preserving) behavior every other git() caller gets."""
        captured = {}

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            captured["kill_tree"] = kill_tree
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "main", cwd=".")) is True
        assert captured["kill_tree"] is True

    def test_push_stall_becomes_result_not_hang(self, monkeypatch):
        """A stall past the bound surfaces as a failed (falsy) PushResult the
        existing create-pr/push-changes error-reporting path already knows
        how to render -- never an unbounded hang."""
        import subprocess
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            raise subprocess.TimeoutExpired(
                cmd=["git", *args], timeout=timeout,
                output="", stderr="[agent-worktrees] runtime not provisioned...",
            )

        monkeypatch.setattr(go, "git", fake_git)
        result = go.push("origin", "main", cwd=".")
        assert bool(result) is False
        assert "timed out after 180s" in result.stderr
        assert "#4547" in result.stderr
        # Partial hook output captured before the timeout is forwarded, not
        # discarded -- the whole point of surfacing "what it was doing".
        assert "runtime not provisioned" in result.stderr
        assert "silently skip PR attribution" in result.stderr

    def test_push_stall_forwards_stdout_too(self, monkeypatch):
        """Copilot review finding on PR #4600: a hook's STDOUT (not just
        stderr) must also be forwarded -- git() captures both streams, so
        discarding stdout would silently drop diagnostic content some hook
        invocations write there instead of stderr."""
        import subprocess
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            raise subprocess.TimeoutExpired(
                cmd=["git", *args], timeout=timeout,
                output="stdout: provisioning uv venv...",
                stderr="stderr: runtime not provisioned",
            )

        monkeypatch.setattr(go, "git", fake_git)
        result = go.push("origin", "main", cwd=".")
        assert bool(result) is False
        assert "stdout: provisioning uv venv..." in result.stderr
        assert "stderr: runtime not provisioned" in result.stderr

    def test_push_retry_stall_also_becomes_result_not_hang(self, monkeypatch):
        """The auth-fallback retry (#900) must be bounded too -- it is still
        the same terminal publish action, just retried without the injected
        cross-account token."""
        import subprocess
        monkeypatch.setattr(
            go, "_auth_config_args",
            lambda remote, *, cwd: ["-c", "http.extraheader=AUTHORIZATION: basic x"],
        )

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False, kill_tree=False):
            injected = "http.extraheader=AUTHORIZATION: basic x" in args
            if injected:
                return types.SimpleNamespace(returncode=1, stdout="", stderr="403")
            raise subprocess.TimeoutExpired(cmd=["git", *args], timeout=timeout)

        monkeypatch.setattr(go, "git", fake_git)
        result = go.push("origin", "main", cwd=".")
        assert bool(result) is False
        assert "timed out after 180s" in result.stderr


class TestPushTimeoutTreeKill:
    """Real-subprocess regression test (Copilot review finding on PR #4600):
    prove push_timeout.run_bounded() kills a stalled command's ENTIRE process
    tree, not just its direct child -- a monkeypatched ``git`` cannot exercise
    this since the whole point is real OS-level process/descendant lifetime.
    """

    def test_run_bounded_kills_grandchild_on_timeout(self, tmp_path, monkeypatch):
        import os
        import platform
        import subprocess
        import sys
        import time

        from agent_worktrees import push_timeout
        from agent_worktrees.locks import pid_alive, process_start_time

        # This test's whole point is the REAL, non-contained pgid-based
        # descendant sweep in push_timeout._kill_tree (see its own
        # docstring): that sweep is deliberately skipped whenever
        # contained_test_mode() is true, so that a test harness invoking a
        # stalled command doesn't killpg its own worker. The repository's
        # own full-suite test runner (tools/run-plugin-tests.py, via
        # tools/plugin_test_containment.py) sets
        # COPILOT_EXTENSIONS_TEST_CONTAINED=1 ambiently for the ENTIRE
        # test process to protect itself from exactly that -- which leaks
        # into this test's own call to run_bounded() and silently disables
        # the very sweep being asserted (confirmed live: the fast/PR-time
        # lane runs a bare test invocation with no such wrapper, so this
        # passed there, while every full-suite run -- including every real
        # validate-and-promote promotion attempt -- inherits the
        # containment flag and fails this assertion deterministically).
        #
        # Clearing it here is required to exercise the real sweep, but it
        # also moves the spawned tree outside plugin_test_containment.py's
        # own protection: if push_timeout._kill_tree ever regresses --
        # exactly the failure this test exists to catch -- the escaped
        # grandchild would otherwise survive its full 60s sleep unreaped
        # and excluded from the suite's own resource accounting. The
        # `finally` block below is an independent safety net -- it force-
        # kills the recorded grandchild PID directly regardless of whether
        # the assertion above it passed or failed, so this test can never
        # itself leak a live process even on its own negative path.
        monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)

        ready = tmp_path / "ready"
        pidfile = tmp_path / "grandchild.pid"
        # A real script file (not an inline -c string) avoids Windows
        # quoting/escaping fragility. Outer process spawns a grandchild that
        # outlives it, then hangs -- simulating a pre-push hook (outer)
        # whose own provisioning descendant (grandchild) must not survive
        # the tree-kill either.
        grandchild_script = tmp_path / "grandchild.py"
        grandchild_script.write_text(
            f"import time\n"
            f"open({str(pidfile)!r}, 'w').write('x')\n"
            f"time.sleep(60)\n"
        )
        outer_script = tmp_path / "outer.py"
        outer_script.write_text(
            f"import subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, {str(grandchild_script)!r}])\n"
            f"open({str(ready)!r}, 'w').write(str(p.pid))\n"
            f"time.sleep(60)\n"
        )
        cmd = [sys.executable, str(outer_script)]

        grandchild_pid: int | None = None
        grandchild_start_time: str | None = None
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                push_timeout.run_bounded(
                    cmd, cwd=str(tmp_path), env=dict(os.environ), timeout=1.0,
                )

            deadline = time.monotonic() + 10
            while not (ready.exists() and pidfile.exists()) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert ready.exists() and pidfile.exists(), (
                "grandchild never started -- test setup issue, not a real assertion"
            )
            grandchild_pid = int(ready.read_text().strip())
            # Captured immediately after reading the PID, before any risk
            # of it exiting and being reused -- the finally block below
            # revalidates against this token before ever signaling the PID.
            grandchild_start_time = process_start_time(grandchild_pid)

            deadline = time.monotonic() + 10
            alive = pid_alive(grandchild_pid)
            while alive and time.monotonic() < deadline:
                time.sleep(0.2)
                alive = pid_alive(grandchild_pid)
            assert not alive, "grandchild process survived run_bounded's tree-kill"
        finally:
            # Independent safety net (see comment above): force-kill the
            # grandchild directly by PID if it's still alive, regardless of
            # the outcome above -- this test must never itself leak a live
            # process back to the (now-uncontained) suite. Revalidate the
            # start-time identity token first (agent_worktrees.locks'
            # PID-reuse-proof mechanism, already used elsewhere in this
            # module for exactly this class of race): a PID can be reaped
            # and reused by an unrelated process during the poll window
            # above, and signaling a bare PID without this check could kill
            # that unrelated process instead of our own grandchild.
            if (
                grandchild_pid is not None
                and grandchild_start_time is not None
                and pid_alive(grandchild_pid)
                and process_start_time(grandchild_pid) == grandchild_start_time
            ):
                if platform.system() == "Windows":
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(grandchild_pid)],
                        capture_output=True, check=False,
                    )
                else:
                    import signal
                    try:
                        os.kill(grandchild_pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

    def test_kill_tree_kills_root_via_handle(self, monkeypatch):
        """Copilot review finding on PR #4600: the root process must be
        terminated via its own Popen handle (``proc.kill()``), never a bare
        PID string alone -- a handle can't be confused by PID reuse, since
        the OS keeps that exact PID reserved as long as any handle to it
        stays open."""
        from agent_worktrees import push_timeout

        calls: list[str] = []
        fake_proc = types.SimpleNamespace(
            pid=999999, poll=lambda: None, kill=lambda: calls.append("kill"),
        )
        monkeypatch.setattr(push_timeout, "contained_test_mode", lambda: True)
        push_timeout._kill_tree(fake_proc)
        assert calls == ["kill"], "root must be killed via its own Popen handle"

    def test_kill_tree_refuses_pid_sweep_for_already_exited_process(self, monkeypatch):
        """Copilot review finding on PR #4600 ("add a direct regression
        proving a stale or mismatched identity is refused"): once
        ``proc.poll()`` shows the process has ALREADY exited on its own,
        its PID may already have been reused by an unrelated process --
        the PID-based sweep must be skipped entirely rather than risk
        acting on a now-unverifiable identity, even outside test
        containment. The handle-bound ``proc.kill()`` is still safe and
        still runs regardless (killing an already-exited process is a
        harmless no-op)."""
        from agent_worktrees import push_timeout

        monkeypatch.setattr(push_timeout, "contained_test_mode", lambda: False)
        sweep_called = []
        monkeypatch.setattr(
            push_timeout.subprocess, "run",
            lambda *a, **k: sweep_called.append(True),
        )
        fake_proc = types.SimpleNamespace(pid=999999, poll=lambda: 0, kill=lambda: None)
        push_timeout._kill_tree(fake_proc)
        assert sweep_called == [], "a PID whose process already exited must not be swept"

    def test_kill_tree_sweeps_pid_tree_for_still_running_process(self, monkeypatch):
        """The complement of the above: a CONFIRMED-still-running process
        (``poll() is None``) is safe to sweep by PID, since our own open
        handle guarantees that exact PID hasn't been reused."""
        from agent_worktrees import push_timeout

        monkeypatch.setattr(push_timeout, "contained_test_mode", lambda: False)
        monkeypatch.setattr(push_timeout.platform, "system", lambda: "Windows")
        sweep_called = []
        monkeypatch.setattr(
            push_timeout.subprocess, "run",
            lambda *a, **k: sweep_called.append(a),
        )
        fake_proc = types.SimpleNamespace(pid=999999, poll=lambda: None, kill=lambda: None)
        push_timeout._kill_tree(fake_proc)
        assert sweep_called, "a confirmed-still-running process should be swept by PID"
        assert "999999" in sweep_called[0][0]

    def test_kill_tree_skips_group_sweep_under_test_containment(self, monkeypatch):
        """Copilot review finding on PR #4600: under
        COPILOT_EXTENSIONS_TEST_CONTAINED=1, detached_kwargs() deliberately
        leaves a POSIX child in the CALLER's own process group -- a killpg
        sweep there would kill the test harness's own worker, not just the
        stalled command's tree. The PID-based sweep must be skipped
        entirely in that mode (root-only kill via the handle is still
        performed)."""
        from agent_worktrees import push_timeout

        monkeypatch.setattr(push_timeout, "contained_test_mode", lambda: True)
        sweep_called = []
        monkeypatch.setattr(
            push_timeout.subprocess, "run",
            lambda *a, **k: sweep_called.append(True),
        )
        fake_proc = types.SimpleNamespace(pid=999999, poll=lambda: None, kill=lambda: None)
        push_timeout._kill_tree(fake_proc)
        assert sweep_called == [], "PID-based sweep must not run under test containment"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_is_cwd_inside_same_dir(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert is_cwd_inside(str(tmp_path)) is True

    def test_is_cwd_inside_subdir(self, tmp_path: Path, monkeypatch):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        monkeypatch.chdir(subdir)
        assert is_cwd_inside(str(tmp_path)) is True

    def test_is_cwd_outside(self, tmp_path: Path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.chdir(other)
        assert is_cwd_inside(str(tmp_path / "elsewhere")) is False

    def test_resolve_to_anchor_with_git_dir(self, tmp_path: Path):
        """If .git is a directory, return path unchanged."""
        (tmp_path / ".git").mkdir()
        assert resolve_to_anchor(tmp_path) == tmp_path

    def test_resolve_to_anchor_no_git(self, tmp_path: Path):
        """If no .git at all, return path unchanged (fallback)."""
        assert resolve_to_anchor(tmp_path) == tmp_path


# ---------------------------------------------------------------------------
# Cross-account authentication (#29)
# ---------------------------------------------------------------------------

from agent_worktrees import git_ops as go  # noqa: E402


class TestCrossAccountAuth:
    @pytest.mark.parametrize("url,owner", [
        ("https://github.com/ThomasMichon/copilot-extensions.git", "ThomasMichon"),
        ("https://github.com/octo-org/repo", "octo-org"),
        ("git@github.com:ThomasMichon/copilot-extensions.git", "ThomasMichon"),
        ("ssh://git@github.com/owner/repo.git", "owner"),
        ("https://gitlab.com/owner/repo.git", None),
        ("/local/path/repo", None),
    ])
    def test_parse_github_owner(self, url, owner):
        assert go._parse_github_owner(url) == owner

    @pytest.mark.parametrize("url,slug", [
        ("https://host/gitea/example-user/test-chamber.git", "example-user/test-chamber"),
        ("https://github.com/owner/copilot-extensions.git", "owner/copilot-extensions"),
        ("git@github.com:owner/repo.git", "owner/repo"),
        ("ssh://git@host/owner/repo", "owner/repo"),
        ("https://host/deep/path/org/proj.git/", "org/proj"),
        # Azure DevOps https remotes: {project}/_git/{repo} -> project/repo.
        ("https://your-org.visualstudio.com/Developer/_git/example-marketplace",
         "Developer/example-marketplace"),
        ("https://dev.azure.com/your-org/Developer/_git/example-marketplace",
         "Developer/example-marketplace"),
        # Azure DevOps ssh (v3/{org}/{project}/{repo}) has no _git segment.
        ("git@ssh.dev.azure.com:v3/your-org/Developer/example-marketplace",
         "Developer/example-marketplace"),
    ])
    def test_remote_slug(self, monkeypatch, url, slug):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: url)
        assert go.remote_slug("origin", cwd=".") == slug

    def test_remote_slug_none_when_no_url(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: None)
        assert go.remote_slug("origin", cwd=".") is None

    def test_auth_args_empty_when_no_token(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: None)
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_empty_for_non_github(self, monkeypatch):
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://gitlab.com/o/r.git")
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_injects_header_with_token(self, monkeypatch):
        import base64
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        # Owner differs from the active account -> cross-account: inject.
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: "ghp_secret")
        args = go._auth_config_args("origin", cwd=".")
        assert args[0] == "-c"
        expected = base64.b64encode(b"x-access-token:ghp_secret").decode()
        assert args[1] == f"http.extraheader=AUTHORIZATION: basic {expected}"

    def test_auth_args_for_url_injects_before_any_checkout_exists(self, monkeypatch):
        """The URL-based variant backs the initial 'git clone' itself, which
        runs before any repo/remote exists for _auth_config_args's
        cwd-based remote-name lookup to resolve."""
        import base64
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: "ghp_secret")
        args = go._auth_config_args_for_url("https://github.com/Owner/r.git")
        expected = base64.b64encode(b"x-access-token:ghp_secret").decode()
        assert args == ["-c", f"http.extraheader=AUTHORIZATION: basic {expected}"]

    def test_auth_args_for_url_empty_for_non_github(self, monkeypatch):
        assert go._auth_config_args_for_url("https://gitlab.com/o/r.git") == []

    def test_auth_args_for_url_empty_for_plain_http(self, monkeypatch):
        """The injected header would otherwise carry the bearer token over
        an unencrypted connection for a plain http:// GitHub remote."""
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: "ghp_secret")
        assert go._auth_config_args_for_url("http://github.com/Owner/r.git") == []

    def test_auth_args_empty_for_plain_http_via_remote_name(self, monkeypatch):
        """Same guarantee through the remote-name entry point used by the
        existing fetch/push paths, not only the URL-based clone path."""
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "http://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "DifferentUser")
        monkeypatch.setattr(go, "_gh_token_for_owner", lambda owner: "ghp_secret")
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_empty_when_owner_is_active_account(self, monkeypatch):
        """#900: when the repo owner *is* the active gh account, skip injection
        so the working credential helper isn't overridden by a possibly
        push-scopeless OAuth token. Case-insensitive."""
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/Owner/r.git")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "owner")  # case differs
        # Token would be available, but the gate must short-circuit before it.
        monkeypatch.setattr(go, "_gh_token_for_owner",
                            lambda owner: pytest.fail("should not be called"))
        assert go._auth_config_args("origin", cwd=".") == []

    def test_auth_args_honors_account_override(self, monkeypatch):
        """An explicit repos.yaml account: overrides the derived remote owner,
        so the injected credential authenticates as the account (not the org)."""
        import base64

        from agent_worktrees import repos as repos_mod
        monkeypatch.setattr(go, "_remote_url", lambda remote, *, cwd: "https://github.com/example-org/r.git")
        # Registry maps this owner's repo to a different account login.
        monkeypatch.setattr(
            repos_mod, "account_for_github_owner",
            lambda owner: "host-acct" if owner == "example-org" else owner,
        )
        monkeypatch.setattr(go, "_active_gh_account", lambda: "example-org")
        seen: list[str] = []

        def _tok(account):
            seen.append(account)
            return "acct_secret"

        monkeypatch.setattr(go, "_gh_token_for_owner", _tok)
        args = go._auth_config_args("origin", cwd=".")
        # Effective account (host-acct) != active (example-org) -> inject it.
        assert seen == ["host-acct"]
        expected = base64.b64encode(b"x-access-token:acct_secret").decode()
        assert args == ["-c", f"http.extraheader=AUTHORIZATION: basic {expected}"]

    def test_active_gh_account_parses_active_marker(self, monkeypatch):
        out = (
            "github.com\n"
            "  \u2713 Logged in to github.com account WorkAcct (keyring)\n"
            "  - Active account: false\n"
            "  \u2713 Logged in to github.com account PersonalAcct (keyring)\n"
            "  - Active account: true\n"
        )
        go._active_gh_account.cache_clear()
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            go.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""),
        )
        assert go._active_gh_account() == "PersonalAcct"
        go._active_gh_account.cache_clear()

    def test_active_gh_account_single_account_fallback(self, monkeypatch):
        out = "github.com\n  \u2713 Logged in to github.com account Solo (keyring)\n"
        go._active_gh_account.cache_clear()
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            go.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""),
        )
        assert go._active_gh_account() == "Solo"
        go._active_gh_account.cache_clear()

    def test_list_gh_accounts_parses_all(self, monkeypatch):
        out = (
            "github.com\n"
            "  \u2713 Logged in to github.com account ThomasMichon (keyring)\n"
            "  - Active account: true\n"
            "  - Token scopes: 'repo'\n"
            "  \u2713 Logged in to github.com account example-operator (keyring)\n"
            "  - Active account: false\n"
        )
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(
            go.subprocess, "run",
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out, stderr=""),
        )
        # EMU logins with underscores must survive.
        assert go.list_gh_accounts() == ["ThomasMichon", "example-operator"]

    def test_list_gh_accounts_empty_without_gh(self, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: None)
        assert go.list_gh_accounts() == []


    def test_push_falls_back_to_plain_when_injected_auth_403s(self, monkeypatch):
        """#900: a token-injected push that fails must retry once *without* the
        override so the default credential helper can authenticate."""
        monkeypatch.setattr(
            go, "_auth_config_args",
            lambda remote, *, cwd: ["-c", "http.extraheader=AUTHORIZATION: basic x"],
        )
        calls: list[bool] = []

        def fake_git(*args, **kwargs):
            injected = "http.extraheader=AUTHORIZATION: basic x" in args
            calls.append(injected)
            # Injected push 403s; plain push (no override) succeeds.
            rc = 1 if injected else 0
            return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        assert bool(go.push("origin", "master", cwd=".")) is True
        assert calls == [True, False]  # injected first, then plain fallback

    def test_push_no_fallback_when_no_injected_auth(self, monkeypatch):
        """When no override was injected, a failed push must NOT silently
        retry -- it simply returns a falsy PushResult carrying the stderr."""
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        calls: list[int] = []

        def fake_git(*args, **kwargs):
            calls.append(1)
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="remote: version-consistency violations\n"
                       "error: failed to push some refs")

        monkeypatch.setattr(go, "git", fake_git)
        res = go.push("origin", "master", cwd=".")
        assert bool(res) is False
        assert len(calls) == 1  # no fallback attempt
        # #993: the real git stderr is surfaced, and a hook decline is NOT a
        # fast-forward race, so it must not be retried by the caller.
        assert "version-consistency" in res.stderr
        assert res.retryable is False

    def test_push_nonff_failure_is_retryable(self, monkeypatch):
        """#993: a non-fast-forward rejection IS a race the caller should
        fetch+rebase+retry -- classified retryable from git's stderr."""
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        monkeypatch.setattr(go, "git", lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="",
            stderr=" ! [rejected]        master -> master (fetch first)\n"
                   "error: failed to push some refs"))
        res = go.push("origin", "master", cwd=".")
        assert bool(res) is False
        assert res.retryable is True

    def test_push_success_returns_truthy_no_stderr(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        monkeypatch.setattr(go, "git", lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="", stderr=""))
        res = go.push("origin", "master", cwd=".")
        assert bool(res) is True
        assert res.retryable is False

    def test_redact_args_strips_extraheader(self):
        cmd = ["git", "-c", "http.extraheader=AUTHORIZATION: basic c2VjcmV0", "push"]
        redacted = go._redact_args(cmd)
        assert "http.extraheader=<redacted>" in redacted
        assert not any("c2VjcmV0" in a for a in redacted)

    def test_git_error_message_redacts_token(self):
        err = GitError(
            ["git", "-c", "http.extraheader=AUTHORIZATION: basic c2VjcmV0", "push"],
            1, "denied",
        )
        assert "c2VjcmV0" not in str(err)
        assert "<redacted>" in str(err)
        assert all("c2VjcmV0" not in a for a in err.cmd)


class TestPinGitCredential:
    """A plain ``git fetch``/``pull`` run by anything other than this tool's
    own account-aware calls only ever sees whatever ``gh`` account is
    currently "active", independent of which account a repo actually needs.
    ``pin_git_credential`` persists a repo-local override so any plain git
    client resolves the correct login."""

    @pytest.fixture(autouse=True)
    def _no_active_gh_account_by_default(self, monkeypatch):
        """Determinism guard: without this, ``pin_git_credential`` calls
        ``_active_gh_account()``, which shells out to the REAL ``gh auth
        status`` on whatever machine runs this suite. A test using a
        plausible-looking real login (e.g. the EMU-mapped
        ``operator_enterprise`` example) would then pass or fail depending on
        which account happens to be authenticated locally -- exactly the
        kind of environment-dependent flake a unit test must not have. Tests
        exercising the active-account branch explicitly override this with
        their own ``monkeypatch.setattr(go, "_active_gh_account", ...)``."""
        monkeypatch.setattr(go, "_active_gh_account", lambda: None)

    def test_noop_when_login_or_host_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        assert go.pin_git_credential(tmp_path, "") is False
        assert go.pin_git_credential(tmp_path, "someone", host="") is False

    @pytest.mark.parametrize("login", [
        "o'neil",  # single quote breaks out of the helper's '<login>' quoting
        "a`whoami`",
        "a$(whoami)",
        "a; rm -rf /",
        "a b",
        "a\nb",
        "safelogin\n",  # a trailing newline must not slip past a "^...$" match
    ])
    def test_noop_when_login_has_unsafe_characters(
        self, tmp_path: Path, monkeypatch, login: str,
    ):
        """The helper is a ``!``-prefixed shell script; login/host are never
        escaped, only allowlisted -- an unsafe character must be rejected
        outright rather than risk a shell-injected credential helper."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)
        assert go.pin_git_credential(repo, login) is False

    @pytest.mark.parametrize("host", [
        "gh;evil.com", "gh evil.com", "gh$(x).com", "github.com\n",
    ])
    def test_noop_when_host_has_unsafe_characters(
        self, tmp_path: Path, monkeypatch, host: str,
    ):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)
        assert go.pin_git_credential(repo, "someone", host=host) is False

    def test_allows_underscore_login(self, tmp_path: Path, monkeypatch):
        """EMU-mapped logins in this codebase use underscores (e.g.
        'operator_enterprise'), which is not a real GitHub username character
        but must still be allowed -- only shell metacharacters are rejected."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)
        assert go.pin_git_credential(repo, "operator_enterprise") is True

    def test_skips_when_login_is_active_account(self, tmp_path: Path, monkeypatch):
        """#900's own rationale, reapplied here: when login IS the active gh
        account, the inherited default helper already authenticates
        correctly -- forcing a gh-auth-token-backed helper risks a
        scope-limited OAuth token turning a working plain push into a 403."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "SomeUser")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)
        # Case-insensitive match against the active account.
        assert go.pin_git_credential(repo, "someuser") is False
        result = sp.run(
            ["git", "-C", str(repo), "config", "--local",
             "credential.https://github.com.username"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0  # never written

    def test_pins_when_login_differs_from_active_account(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        monkeypatch.setattr(go, "_active_gh_account", lambda: "SomeUser")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)
        assert go.pin_git_credential(repo, "other-user") is True

    def test_clears_stale_pin_when_login_becomes_active_account(self, tmp_path: Path, monkeypatch):
        """A repo previously pinned to 'other-user', whose account_map is
        later corrected to what is now the active gh account, must not keep
        forcing the stale login -- the config must actually be cleared, not
        just left untouched, or 'the active helper already works' would be
        false for this checkout."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)

        monkeypatch.setattr(go, "_active_gh_account", lambda: "SomeUser")
        assert go.pin_git_credential(repo, "other-user") is True  # establish the stale pin

        monkeypatch.setattr(go, "_active_gh_account", lambda: "other-user")
        assert go.pin_git_credential(repo, "other-user") is False  # now the active account

        result = sp.run(
            ["git", "-C", str(repo), "config", "--local",
             "credential.https://github.com.username"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0  # stale username cleared
        helpers = sp.run(
            ["git", "-C", str(repo), "config", "--local", "--get-all",
             "credential.https://github.com.helper"],
            capture_output=True, text=True,
        )
        assert helpers.returncode != 0 or helpers.stdout.strip() == ""  # stale helper cleared

    def test_noop_when_gh_unavailable(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: None)
        assert go.pin_git_credential(tmp_path, "someone") is False

    def test_noop_when_path_not_a_directory(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        assert go.pin_git_credential(tmp_path / "does-not-exist", "someone") is False

    def test_noop_when_not_a_git_working_tree(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()
        assert go.pin_git_credential(plain_dir, "someone") is False

    def test_pins_local_config_on_a_real_repo(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)

        assert go.pin_git_credential(repo, "operator_enterprise") is True

        username = sp.run(
            ["git", "-C", str(repo), "config", "--local",
             "credential.https://github.com.username"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert username == "operator_enterprise"

        helpers = sp.run(
            ["git", "-C", str(repo), "config", "--local", "--get-all",
             "credential.https://github.com.helper"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        # A leading empty entry resets any inherited (global/system) helper
        # chain for this host before the pinned helper is appended.
        assert helpers[0] == ""
        assert "operator_enterprise" in helpers[-1]
        assert "gh auth token" in helpers[-1]

    def test_idempotent_on_repeated_calls(self, tmp_path: Path, monkeypatch):
        """Re-pinning (e.g. a re-run of the backfill command) must not pile up
        duplicate helper entries."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)

        assert go.pin_git_credential(repo, "operator_enterprise") is True
        assert go.pin_git_credential(repo, "operator_enterprise") is True

        helpers = sp.run(
            ["git", "-C", str(repo), "config", "--local", "--get-all",
             "credential.https://github.com.helper"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        assert helpers == ["", helpers[-1]]

    def test_concurrent_pins_never_interleave(self, tmp_path: Path, monkeypatch):
        """Two threads racing to pin the same checkout to different logins
        must leave a self-consistent result: the on-disk username always
        matches the login embedded in the (single) surviving helper entry,
        never a mix of one thread's username with another's helper."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        import threading
        sp.run(["git", "init", "-q", str(repo)], check=True)

        results: list[bool] = []
        lock = threading.Lock()

        def _pin(login: str) -> None:
            for _ in range(5):
                ok = go.pin_git_credential(repo, login)
                with lock:
                    results.append(ok)

        threads = [
            threading.Thread(target=_pin, args=("acct-a",)),
            threading.Thread(target=_pin, args=("acct-b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(results)

        username = sp.run(
            ["git", "-C", str(repo), "config", "--local",
             "credential.https://github.com.username"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        helpers = sp.run(
            ["git", "-C", str(repo), "config", "--local", "--get-all",
             "credential.https://github.com.helper"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        # Exactly the reset + one helper survive (never extra entries from an
        # interleaved unset-all/add sequence), and the surviving username and
        # helper agree on the same login.
        assert helpers == ["", helpers[-1]]
        assert username in helpers[-1]

    def test_pins_for_custom_host(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "repo"
        repo.mkdir()
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)

        assert go.pin_git_credential(repo, "acct", host="github.example.com") is True
        username = sp.run(
            ["git", "-C", str(repo), "config", "--local",
             "credential.https://github.example.com.username"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert username == "acct"

    def test_pins_a_bare_repository(self, tmp_path: Path, monkeypatch):
        """A bare anchor (agent-worktrees' own pattern -- a normal ``git
        init``/``clone`` layout with ``core.bare`` forced ``true`` afterward,
        so it can never be edited directly) has no work tree to be "inside",
        but a plain fetch/pull can still run there directly (an unattended
        machine-maintenance task might do exactly this) and needs the same
        pin."""
        monkeypatch.setattr(go.shutil, "which", lambda _: "/usr/bin/gh")
        repo = tmp_path / "bare-repo"
        import subprocess as sp
        sp.run(["git", "init", "-q", str(repo)], check=True)
        sp.run(["git", "-C", str(repo), "config", "core.bare", "true"], check=True)

        assert go.pin_git_credential(repo, "operator_enterprise") is True

        username = sp.run(
            ["git", "-C", str(repo), "config", "--local",
             "credential.https://github.com.username"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert username == "operator_enterprise"


class TestFetchTimeout:
    """#1709: a network ``fetch`` is bounded so an unreachable remote can't
    hang push-changes / create-pr / pr-status / sync indefinitely."""

    def test_fetch_passes_default_timeout(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None,
                     no_hooks=False):
            captured["timeout"] = timeout
            captured["args"] = args
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(go, "git", fake_git)
        go.fetch("origin", cwd=".")
        assert captured["timeout"] == go.DEFAULT_FETCH_TIMEOUT
        assert captured["args"] == ("fetch", "origin", "--quiet")

    def test_fetch_timeout_override(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout=None: (
            captured.__setitem__("timeout", timeout),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        go.fetch("origin", cwd=".", timeout=5)
        assert captured["timeout"] == 5

    def test_fetch_stall_becomes_giterror_not_hang(self, monkeypatch):
        """A stall past the bound surfaces as a GitError (rc 124) the
        best-effort callers already handle -- never an unbounded hang."""
        import subprocess
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            raise subprocess.TimeoutExpired(cmd=["git", *args], timeout=timeout)

        monkeypatch.setattr(go, "git", fake_git)
        with pytest.raises(GitError) as exc:
            go.fetch("origin", cwd=".")
        assert exc.value.returncode == 124
        assert "timed out" in str(exc.value)

    def test_fetch_none_timeout_preserves_unbounded(self, monkeypatch):
        monkeypatch.setattr(go, "_auth_config_args", lambda remote, *, cwd: [])
        captured = {}
        monkeypatch.setattr(go, "git", lambda *a, cwd=None, check=True,
                            capture=True, timeout="sentinel": (
            captured.__setitem__("timeout", timeout),
            types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        go.fetch("origin", cwd=".", timeout=None)
        assert captured["timeout"] is None


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TestDataModels:
    def test_worktree_state_values(self):
        assert WorktreeState.ACTIVE == "active"
        assert WorktreeState.COMPLETED == "completed"
        assert WorktreeState.GONE == "gone"

    def test_worktree_state_info_defaults(self):
        info = WorktreeStateInfo(state=WorktreeState.ACTIVE)
        assert info.ahead == 0
        assert info.behind == 0
        assert info.dirty == 0
        assert info.branch_drift is False
        assert info.current_branch is None


class TestRefineStateWithSession:
    """The CONVO refinement shared by the status bar and list --classify."""

    def test_convo_is_canonical_state(self):
        # CONVO must be a first-class enum value so every surface (status bar,
        # `list --json --classify`) reports the same vocabulary.
        assert WorktreeState.CONVO == "convo"

    def test_unused_with_turns_becomes_convo(self):
        assert (
            refine_state_with_session(WorktreeState.UNUSED, 7)
            == WorktreeState.CONVO
        )

    def test_unused_without_turns_stays_unused(self):
        assert (
            refine_state_with_session(WorktreeState.UNUSED, 0)
            == WorktreeState.UNUSED
        )

    def test_other_states_unaffected_by_turns(self):
        for st in (
            WorktreeState.DIRTY,
            WorktreeState.WIP,
            WorktreeState.COMPLETED,
            WorktreeState.ACTIVE,
            WorktreeState.ORPHAN,
            WorktreeState.GONE,
        ):
            assert refine_state_with_session(st, 12) == st


# --- classification git timeout -> honest UNKNOWN (perf hang fix) -----------


class TestClassifyGitTimeout:
    """A stalled git spawn during classification must degrade to UNKNOWN for
    that one worktree -- never a fabricated concrete state, never a raised
    timeout that hangs the picker's per-worktree loop."""

    def test_git_passes_timeout_through(self, monkeypatch):
        import subprocess

        from agent_worktrees import git_ops as go

        captured = {}

        def fake_run(cmd, **kw):
            captured.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        go.git("status", "--porcelain", timeout=7)
        assert captured["timeout"] == 7

    def test_git_default_timeout_is_none(self, monkeypatch):
        """Callers that don't opt in keep the historical unbounded behavior."""
        import subprocess

        from agent_worktrees import git_ops as go

        captured = {}

        def fake_run(cmd, **kw):
            captured.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(go.subprocess, "run", fake_run)
        go.git("status")
        assert captured["timeout"] is None

    def test_classify_worktree_timeout_reports_unknown(self, tmp_path, monkeypatch):
        import subprocess

        from agent_worktrees import git_ops as go

        (tmp_path / ".git").mkdir()

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            # Branch detection succeeds so classification reaches the git ops.
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return subprocess.CompletedProcess(
                    ["git", *args], 0, "worktree/x\n", "")
            # The first classification probe stalls past the bound.
            raise subprocess.TimeoutExpired(
                cmd=["git", *args], timeout=timeout or go._CLASSIFY_GIT_TIMEOUT)

        monkeypatch.setattr(go, "git", fake_git)

        info = go.classify_worktree(str(tmp_path), "worktree/x")
        # Honest "couldn't determine", not a confidently-wrong concrete state.
        assert info.state == go.WorktreeState.UNKNOWN
        # Branch metadata from the successful pre-git read is still reported.
        assert info.current_branch == "worktree/x"
        assert info.fetch_failed is False  # fetch=False was never requested

    def test_classify_worktree_timeout_with_fetch_reports_fetch_failed(
        self, tmp_path, monkeypatch,
    ):
        # worktree-finality-and-obligations Phase 5 follow-up: a timeout
        # gives no confirmation the requested fetch ever completed (it may
        # have stalled on the fetch itself or a later git call) -- so
        # fetch=True must propagate fetch_failed=True through the timeout
        # path too, not just the explicit nonzero-exit fetch failure.
        import subprocess

        from agent_worktrees import git_ops as go

        (tmp_path / ".git").mkdir()

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            if args[:2] == ("rev-parse", "--abbrev-ref"):
                return subprocess.CompletedProcess(
                    ["git", *args], 0, "worktree/x\n", "")
            raise subprocess.TimeoutExpired(
                cmd=["git", *args], timeout=timeout or go._CLASSIFY_GIT_TIMEOUT)

        monkeypatch.setattr(go, "git", fake_git)

        info = go.classify_worktree(str(tmp_path), "worktree/x", fetch=True)
        assert info.state == go.WorktreeState.UNKNOWN
        assert info.fetch_requested is True
        assert info.fetch_failed is True


class TestClassifyGitProcessCount:
    """Classification avoids separate merge-base and rev-list count spawns."""

    @staticmethod
    def _run(monkeypatch, responses):
        calls = []

        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            calls.append(args)
            return responses(args)

        monkeypatch.setattr(go, "git", fake_git)
        info = go._classify_git_state(
            Path("."),
            "worktree/x",
            "origin/main",
            fetch=False,
            remote="origin",
            default_branch="main",
            actual_branch="worktree/x",
            drift=False,
        )
        return info, calls

    def test_dirty_ahead_path_gets_counts_without_merge_base(self, monkeypatch):
        def responses(args):
            if args[:1] == ("status",):
                return types.SimpleNamespace(
                    returncode=0, stdout=" M changed.txt\n", stderr="")
            if args[:1] == ("rev-list",):
                return types.SimpleNamespace(
                    returncode=0, stdout="2\t0\n", stderr="")
            if args[:2] == ("--no-pager", "log"):
                return types.SimpleNamespace(
                    returncode=0, stdout="Pending work\n", stderr="")
            raise AssertionError(f"unexpected git call: {args}")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.DIRTY
        assert (info.ahead, info.behind, info.dirty) == (2, 0, 1)
        assert [call[0] for call in calls] == [
            "status", "rev-list", "--no-pager",
        ]
        assert calls[1] == (
            "rev-list", "--left-right", "--count",
            "worktree/x...origin/main",
        )

    def test_diverged_orphan_still_requires_failed_merge_base(self, monkeypatch):
        def responses(args):
            if args[:1] == ("status",):
                return types.SimpleNamespace(
                    returncode=0, stdout="", stderr="")
            if args[:1] == ("rev-list",):
                return types.SimpleNamespace(
                    returncode=0, stdout="1 4\n", stderr="")
            if args[:1] == ("merge-base",):
                return types.SimpleNamespace(
                    returncode=1, stdout="", stderr="")
            raise AssertionError(f"unexpected git call: {args}")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.ORPHAN
        assert [call[0] for call in calls] == [
            "status", "rev-list", "merge-base",
        ]

    def test_failed_count_preserves_orphan_detection(self, monkeypatch):
        def responses(args):
            if args[:1] == ("status",):
                return types.SimpleNamespace(
                    returncode=0, stdout="", stderr="")
            if args[:1] == ("rev-list",):
                return types.SimpleNamespace(
                    returncode=128, stdout="", stderr="bad revision")
            if args[:1] == ("merge-base",):
                return types.SimpleNamespace(
                    returncode=1, stdout="", stderr="bad revision")
            raise AssertionError(f"unexpected git call: {args}")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.ORPHAN
        assert [call[0] for call in calls] == [
            "status", "rev-list", "merge-base",
        ]

    def test_patch_equivalent_ahead_branch_never_needs_merge_base(
        self, monkeypatch,
    ):
        def responses(args):
            if args[:1] == ("status",):
                stdout = ""
            elif args[:1] == ("rev-list",):
                stdout = "2 0\n"
            elif args[:2] == ("--no-pager", "log"):
                stdout = "Already landed\n"
            elif args[:1] == ("cherry",):
                stdout = "- deadbeef\n- cafef00d\n"
            else:
                raise AssertionError(f"unexpected git call: {args}")
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")

        info, calls = self._run(monkeypatch, responses)

        assert info.state == WorktreeState.COMPLETED
        assert (info.ahead, info.behind) == (2, 0)
        assert "merge-base" not in [call[0] for call in calls]


class TestClassifyGitStateFetchFailed:
    """worktree-finality-and-obligations Phase 5 follow-up: a requested fetch
    that itself fails must be reported honestly (fetch_failed=True), not
    silently treated as refreshed evidence, even though classification still
    proceeds on the stale local refs (#discussion_r4008048471)."""

    @staticmethod
    def _run(monkeypatch, *, fetch_returncode, responses):
        def fake_git(*args, cwd=None, check=True, capture=True, timeout=None):
            if args[:1] == ("fetch",):
                return types.SimpleNamespace(
                    returncode=fetch_returncode, stdout="", stderr="",
                )
            return responses(args)

        monkeypatch.setattr(go, "git", fake_git)
        return go._classify_git_state(
            Path("."),
            "worktree/x",
            "origin/main",
            fetch=True,
            remote="origin",
            default_branch="main",
            actual_branch="worktree/x",
            drift=False,
        )

    def _clean_completed_responses(self, args):
        if args[:1] == ("status",):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:1] == ("rev-list",):
            return types.SimpleNamespace(returncode=0, stdout="0 0\n", stderr="")
        if args[:2] == ("--no-pager", "reflog"):
            return types.SimpleNamespace(
                returncode=0, stdout="commit: init\n", stderr="",
            )
        raise AssertionError(f"unexpected git call: {args}")

    def test_successful_fetch_reports_fetch_failed_false(self, monkeypatch):
        info = self._run(
            monkeypatch, fetch_returncode=0,
            responses=self._clean_completed_responses,
        )
        assert info.state == WorktreeState.COMPLETED
        assert info.fetch_requested is True
        assert info.fetch_failed is False

    def test_failed_fetch_reports_fetch_failed_true(self, monkeypatch):
        info = self._run(
            monkeypatch, fetch_returncode=1,
            responses=self._clean_completed_responses,
        )
        # Classification still proceeds on stale local refs...
        assert info.state == WorktreeState.COMPLETED
        # ...but the caller is told the fetch itself failed.
        assert info.fetch_requested is True
        assert info.fetch_failed is True

    def test_no_fetch_requested_always_reports_false(self, monkeypatch):
        info, _calls = TestClassifyGitProcessCount._run(
            monkeypatch, self._clean_completed_responses,
        )
        assert info.fetch_requested is False
        assert info.fetch_failed is False


# ---------------------------------------------------------------------------
# worktree_suffix / merge_squash -- never publish the raw worktree_id
# ---------------------------------------------------------------------------

class TestWorktreeSuffix:
    def test_extracts_trailing_dash_token(self):
        assert worktree_suffix("example-host-20260917-125245-3a94") == "3a94"

    def test_no_dash_id_is_not_returned_verbatim(self):
        """No trailing token to extract -- must still never publish the raw
        (potentially identifying) id verbatim."""
        wid = "nodashesatall"
        suffix = worktree_suffix(wid)
        assert suffix != wid

    def test_no_dash_id_is_deterministic(self):
        wid = "nodashesatall"
        assert worktree_suffix(wid) == worktree_suffix(wid)


class TestMergeSquash:
    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "r"
        repo.mkdir()
        git("init", "-b", "master", cwd=repo)
        git("config", "user.email", "t@example.com", cwd=repo)
        git("config", "user.name", "Test", cwd=repo)
        (repo / "a.txt").write_text("one\n")
        git("add", "-A", cwd=repo)
        git("commit", "-m", "initial", cwd=repo)
        return repo

    def test_commit_message_never_contains_the_raw_worktree_id(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        git("checkout", "-b", "feature", cwd=repo)
        (repo / "b.txt").write_text("two\n")
        git("add", "-A", cwd=repo)
        git("commit", "-m", "feature work", cwd=repo)
        git("checkout", "master", cwd=repo)

        worktree_id = "example-host-20260917-125245-3a94"
        assert merge_squash("feature", worktree_id, cwd=repo) is True

        subject = git("log", "-1", "--format=%s", cwd=repo).stdout.strip()
        assert worktree_id not in subject
        assert subject == "squash: merge worktree 3a94"
