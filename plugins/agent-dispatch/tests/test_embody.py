"""Tests for the agent-worktrees embody spawn backend (CLI-backed autopilot)."""

from __future__ import annotations

import json
import os
import subprocess
import types

import pytest

from agent_dispatch import embody, procutil


def _completed(stdout: str, *, returncode: int = 0, stderr: str = ""):
    return types.SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_autopilot_prompt_mentions_task_verbs_and_deferred_completion():
    prompt = embody.autopilot_worker_prompt("abc123", worker_id="w9")
    assert "abc123" in prompt
    assert "w9" in prompt
    assert "without `--url`" in prompt
    assert "resolves the live local coordinator" in prompt
    # The default (no route) bakes no coordinator endpoint into the worker.
    assert "http://" not in prompt
    # The full deferred-completion worker loop, driven under the worktree
    # identity (owner-less claim/start/complete so the task owner stays
    # machine/worktree and live-session tracking can join it).
    assert "agent-dispatch claim --task abc123" in prompt
    assert "agent-dispatch start abc123" in prompt
    assert "agent-dispatch steer take abc123 --all" in prompt
    assert "agent-dispatch complete abc123" in prompt
    # The progress-beat rhythm (Phase 7 Channel B): report at transitions.
    assert "agent-dispatch progress abc123" in prompt
    assert "--summary" in prompt
    # Autopilot + the deferred-completion guarantee (do not complete early).
    assert "autopilot" in prompt.lower()
    assert "not mark it complete before" in prompt.lower()
    # Contract-net evaluation window (dev55): claim under the tight evaluation
    # lease, assess, then accept (start) / decline (yield --exclude-self) / retire
    # (abandon --duplicate-of).
    assert "agent-dispatch claim --task abc123 --evaluation" in prompt
    assert "evaluat" in prompt.lower()
    assert "agent-dispatch yield abc123 --exclude-self worktree" in prompt
    assert "agent-dispatch abandon abc123 --duplicate-of" in prompt


def test_headless_autopilot_prompt_uses_explicit_worker_identity():
    prompt = embody.autopilot_worker_prompt(
        "abc123",
        worker_id="headless-1234",
        all_repos=True,
        explicit_worker_identity=True,
    )
    assert (
        "agent-dispatch claim --task abc123 --evaluation "
        "--worker headless-1234 --all-repos"
    ) in prompt
    assert "agent-dispatch start abc123 headless-1234" in prompt
    assert "agent-dispatch steer take abc123 headless-1234 --all" in prompt
    assert "agent-dispatch progress abc123 headless-1234" in prompt
    assert "agent-dispatch complete abc123 headless-1234" in prompt
    assert "agent-dispatch yield abc123 headless-1234 --note <why>" in prompt
    assert "--exclude-self worktree" not in prompt


def test_conclude_disposable_worker_requests_exact_managed_removal(monkeypatch):
    calls = []
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess,
        "run",
        lambda cmd, **_kwargs: calls.append(cmd)
        or _completed('{"action":"removed","managed_gc_eligible":false}'),
    )

    result = embody.conclude_disposable_worker("wt-review", "session-review")

    assert calls[0] == [
        "/usr/bin/agent-worktrees",
        "conclude-disposable",
        "--worktree",
        "wt-review",
        "--policy",
        "disposable-cli",
        "--owner",
        "agent-dispatch",
        "--remove",
        "--json",
        "--session",
        "session-review",
    ]
    assert result["action"] == "removed"


def test_conclude_dispatch_attempt_requests_exact_managed_removal(monkeypatch):
    calls = []
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess,
        "run",
        lambda cmd, **_kwargs: calls.append(cmd)
        or _completed('{"action":"removed","managed_gc_eligible":false}'),
    )

    result = embody.conclude_dispatch_attempt(
        "wt-review",
        "session-review",
        "dispatch-task:task-1:1",
    )

    assert calls[0] == [
        "/usr/bin/agent-worktrees",
        "conclude-disposable",
        "--worktree",
        "wt-review",
        "--policy",
        "dispatch-attempt",
        "--reservation",
        "dispatch-task:task-1:1",
        "--owner",
        "agent-dispatch",
        "--remove",
        "--json",
        "--session",
        "session-review",
    ]
    assert result["action"] == "removed"


def test_conclude_disposable_worker_preservation_skip_is_returned(monkeypatch):
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess,
        "run",
        lambda _cmd, **_kwargs: _completed(
            '{"action":"skipped","managed_gc_eligible":false}'
        ),
    )

    result = embody.conclude_disposable_worker("wt-live", "session-live")

    assert result["action"] == "skipped"


def test_conclude_disposable_worker_surfaces_managed_removal_failure(monkeypatch):
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess,
        "run",
        lambda _cmd, **_kwargs: _completed(
            '{"error":"worktree became live"}',
            returncode=1,
        ),
    )

    with pytest.raises(
        embody.DisposableConclusionError,
        match="worktree became live",
    ):
        embody.conclude_disposable_worker("wt-raced", "session-raced")


def test_autopilot_prompt_threads_shared_moniker_route():
    """A --shared route stamps the stable moniker onto every worker command --
    a label, never a raw endpoint (no URL is ever baked in)."""
    prompt = embody.autopilot_worker_prompt("abc123", worker_id="w9", route=" --shared")
    assert "agent-dispatch --shared show abc123" in prompt
    assert "agent-dispatch --shared claim --task abc123 --evaluation" in prompt
    assert "agent-dispatch --shared complete abc123" in prompt
    # No bare (route-less) lifecycle command leaks through.
    assert "agent-dispatch show abc123" not in prompt
    assert "http://" not in prompt


def test_autopilot_prompt_pins_cross_repo_task_lane():
    prompt = embody.autopilot_worker_prompt(
        "abc123",
        worker_id="w9",
        repo="github.com/o/n",
    )
    assert (
        "agent-dispatch claim --task abc123 --evaluation "
        "--repo github.com/o/n"
    ) in prompt
    assert "agent-dispatch list --repo github.com/o/n" in prompt


def test_autopilot_prompt_carries_explicit_all_repos_claim_mode():
    prompt = embody.autopilot_worker_prompt(
        "abc123",
        worker_id="w9",
        all_repos=True,
    )
    assert (
        "agent-dispatch claim --task abc123 --evaluation --all-repos"
        in prompt
    )
    assert "agent-dispatch list --all-repos" in prompt


def test_autopilot_prompt_carries_goal_loop_contract():
    prompt = embody.autopilot_worker_prompt("abc123", worker_id="w9")
    # The seed reads the durable goal + done-criteria + prior progress log and
    # resumes rather than restarting (the resumable-goal contract).
    assert "agent-dispatch show abc123" in prompt
    assert "goal" in prompt.lower()
    assert "done_criteria" in prompt or "done-criteria" in prompt.lower()
    assert "progress_log" in prompt
    assert "resume" in prompt.lower()
    # An explicit loop: work -> progress -> re-check done-criteria -> repeat.
    assert "loop" in prompt.lower()
    # A plain one-shot task (no goal) still behaves as before.
    assert "one-shot" in prompt.lower()


def test_embody_available_false_without_cli(monkeypatch):
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: None)
    assert embody.embody_available() is False


def test_spawn_embodied_worker_unavailable_when_no_cli(monkeypatch):
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: None)
    with pytest.raises(embody.EmbodyUnavailable):
        embody.spawn_embodied_worker(
            "t1", worker_id="w1"
        )


def test_launch_prefix_prefers_versioned_runtime_over_cmd_shim(monkeypatch, tmp_path):
    """The autopilot seed carries cmd.exe metacharacters (``&``, ``()``, ``<>``,
    backtick). Launching the Windows ``agent-worktrees.cmd`` shim makes cmd.exe
    re-parse ``%*`` and corrupt the seed (WinError 2, BatBadBut). So when the
    agent-worktrees versioned runtime is installed, its slot interpreter +
    ``-m agent_worktrees`` is preferred (resolved the canonical way via the
    ``current-version`` marker), bypassing any ``.cmd`` shim entirely."""
    slot_py = tmp_path / ".agent-worktrees" / "versions" / "1.5.3-dev9" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    slot_py.parent.mkdir(parents=True)
    slot_py.write_text("")  # only needs to exist as a file
    (tmp_path / ".agent-worktrees" / "current-version").write_text("1.5.3-dev9")
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    # Even with a .cmd binstub on PATH, the versioned interpreter wins.
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-worktrees.cmd"
    )
    prefix = embody._agent_worktrees_launch_prefix()
    assert prefix == [str(slot_py), "-m", "agent_worktrees"]
    # The launcher is a real interpreter, never a shell shim that re-parses args.
    assert not prefix[0].lower().endswith((".cmd", ".bat"))


def test_launch_prefix_falls_back_to_binstub_on_posix(monkeypatch, tmp_path):
    """Without an installed versioned runtime, fall back to the ``agent-worktrees``
    binstub on PATH **on POSIX only** (its shims are plain exec scripts -- no
    cmd.exe re-parse)."""
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "posix")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: "/usr/bin/agent-worktrees"
    )
    assert embody._agent_worktrees_launch_prefix() == ["/usr/bin/agent-worktrees"]


def test_launch_prefix_no_ps1_fallback_on_windows(monkeypatch, tmp_path):
    """On Windows, with no versioned runtime, do NOT fall back to the ``.ps1``
    binstub (``subprocess`` cannot exec it -> WinError 2). Return ``None`` so the
    caller degrades deliberately (the #974 fix)."""
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "nt")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-worktrees.ps1"
    )
    assert embody._agent_worktrees_launch_prefix() is None


def test_launch_prefix_none_when_unresolvable(monkeypatch, tmp_path):
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "posix")
    monkeypatch.setattr(procutil.shutil, "which", lambda _n: None)
    assert embody._agent_worktrees_launch_prefix() is None


def test_spawn_embodied_worker_builds_embody_new_command(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(embody.subprocess, "run", fake_run)

    embody.spawn_embodied_worker(
        "task-9", worker_id="embody-1",
    )
    cmd = captured["cmd"]
    assert cmd[:2] == ["/usr/bin/agent-worktrees", "embody"]
    # A fresh parallel worktree, JSON output, and the driver banner.
    assert "--new" in cmd
    assert "--json" in cmd
    assert cmd[cmd.index("--driver") + 1] == "agent-dispatch"
    # The seed carries the autopilot worker prompt for this task/worker.
    seed = cmd[cmd.index("--seed") + 1]
    assert "task-9" in seed and "embody-1" in seed
    # No verify-timeout appended when not requested.
    assert "--verify-timeout" not in cmd


def test_spawn_embodied_worker_can_target_existing_worktree(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(embody.subprocess, "run", fake_run)

    embody.spawn_embodied_worker(
        "task-9", worker_id="embody-1", worktree_id="wt-reviewer",
    )

    cmd = captured["cmd"]
    assert "--new" not in cmd
    assert cmd[cmd.index("--worktree-id") + 1] == "wt-reviewer"


def test_create_worktree_returns_id_and_path(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(
            returncode=0,
            stdout='{"worktree": {"id": "wt-new", "path": "/tmp/wt-new"}}',
            stderr="",
        )

    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(embody.subprocess, "run", fake_run)

    result = embody.create_worktree(
        project="widgets",
        interface="acp",
        task_id="task-1",
        reservation_key="dispatch-task:task-1:1",
        attempt=1,
        driver="agent-dispatch",
        supervisor="supervisor-1",
    )

    assert result == {"session": None, "worktree": "wt-new", "path": "/tmp/wt-new"}
    assert captured["cmd"] == [
        "/usr/bin/agent-worktrees",
        "--project",
        "widgets",
        "create",
        "--no-owner",
        "--origin",
        "delegate",
        "--interface",
        "acp",
        "--dispatch-task-id",
        "task-1",
        "--dispatch-reservation-key",
        "dispatch-task:task-1:1",
        "--dispatch-attempt",
        "1",
        "--dispatch-driver",
        "agent-dispatch",
        "--dispatch-supervisor",
        "supervisor-1",
        "--json",
    ]


def test_prepare_reusable_worktree_replaces_confirmed_missing(monkeypatch):
    monkeypatch.setattr(
        embody,
        "resolve_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            embody.WorktreeNotFound("missing")
        ),
    )
    monkeypatch.setattr(
        embody,
        "create_worktree",
        lambda **_kwargs: {
            "worktree": "wt-fresh",
            "path": "/tmp/wt-fresh",
        },
    )

    prepared = embody.prepare_reusable_worktree(
        {"id": "task-1", "repo": "example.com/acme/widgets"},
        {
            "key": "dispatch-task:task-1:1",
            "attempt": 1,
            "worktree": "wt-missing",
        },
        interface="acp",
        driver="agent-dispatch",
        supervisor="supervisor-1",
    )

    assert prepared == {
        "worktree": "wt-fresh",
        "path": "/tmp/wt-fresh",
        "created": True,
        "replaced": True,
        "ownership": "created",
    }


def test_prepare_reusable_worktree_holds_on_indeterminate_resolution(
    monkeypatch,
):
    created = []
    monkeypatch.setattr(
        embody,
        "resolve_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            embody.EmbodyUnavailable("registry unavailable")
        ),
    )
    monkeypatch.setattr(
        embody,
        "create_worktree",
        lambda **_kwargs: created.append(True),
    )

    with pytest.raises(embody.EmbodyUnavailable, match="registry unavailable"):
        embody.prepare_reusable_worktree(
            {"id": "task-1", "repo": "example.com/acme/widgets"},
            {
                "key": "dispatch-task:task-1:1",
                "attempt": 1,
                "worktree": "wt-unknown",
            },
            interface="cli",
            driver="agent-dispatch",
            supervisor="supervisor-1",
        )

    assert created == []


def test_resolve_worktree_bypasses_display_cache(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(
            returncode=0,
            stdout='{"worktrees":[{"id":"wt-review","path":"/tmp/wt-review"}]}',
            stderr="",
        )

    monkeypatch.setattr(
        embody,
        "_agent_worktrees_launch_prefix",
        lambda: ["/usr/bin/agent-worktrees"],
    )
    monkeypatch.setattr(embody.subprocess, "run", fake_run)

    resolved = embody.resolve_worktree("wt-review", project="widgets")

    assert resolved == {
        "worktree": "wt-review",
        "path": "/tmp/wt-review",
    }
    assert captured["cmd"] == [
        "/usr/bin/agent-worktrees",
        "--project",
        "widgets",
        "list",
        "--json",
        "--fresh",
    ]


@pytest.mark.parametrize(
    "status",
    ["finalizing", "finalized", "complete", "completed", "orphaned"],
)
def test_resolve_worktree_rejects_terminal_checkout(monkeypatch, status):
    monkeypatch.setattr(
        embody,
        "_agent_worktrees_launch_prefix",
        lambda: ["/usr/bin/agent-worktrees"],
    )
    monkeypatch.setattr(
        embody.subprocess,
        "run",
        lambda *_args, **_kwargs: types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "worktrees": [
                        {
                            "id": "wt-finalized",
                            "path": "/tmp/wt-finalized",
                            "status": status,
                        }
                    ]
                }
            ),
            stderr="",
        ),
    )

    with pytest.raises(embody.WorktreeNotFound, match="terminal"):
        embody.resolve_worktree("wt-finalized", project="widgets")


def test_prepare_reusable_worktree_replaces_finalized_checkout(monkeypatch):
    monkeypatch.setattr(
        embody,
        "resolve_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            embody.WorktreeNotFound("worktree is terminal")
        ),
    )
    monkeypatch.setattr(
        embody,
        "create_worktree",
        lambda **_kwargs: {
            "worktree": "wt-fresh",
            "path": "/tmp/wt-fresh",
        },
    )

    prepared = embody.prepare_reusable_worktree(
        {"id": "task-1", "repo": "example.com/acme/widgets"},
        {
            "key": "dispatch-task:task-1:2",
            "attempt": 2,
            "worktree": "wt-finalized",
            "session_handle": "local-body:session-unknown",
        },
        interface="acp",
        driver="agent-dispatch",
        supervisor="supervisor-1",
    )

    assert prepared["worktree"] == "wt-fresh"
    assert prepared["replaced"] is True
    assert prepared["ownership"] == "created"


def test_spawn_embodied_worker_passes_verify_timeout(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess, "run",
        lambda cmd, **kw: (captured.__setitem__("cmd", cmd)
                           or types.SimpleNamespace(returncode=0, stdout="", stderr="")),
    )
    embody.spawn_embodied_worker(
        "t", worker_id="w", verify_timeout=30,
    )
    cmd = captured["cmd"]
    assert cmd[cmd.index("--verify-timeout") + 1] == "30"


def test_spawn_embodied_worker_threads_project_as_global(monkeypatch):
    """--project is an agent-worktrees GLOBAL option, so it must precede the
    `embody` subcommand -- letting a CWD-neutral caller name the project."""
    captured = {}
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess, "run",
        lambda cmd, **kw: (captured.__setitem__("cmd", cmd)
                           or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")),
    )
    embody.spawn_embodied_worker(
        "t", worker_id="w", project="test-chamber",
    )
    cmd = captured["cmd"]
    # [exe, "--project", "test-chamber", "embody", ...] -- project BEFORE embody.
    assert cmd[:4] == [
        "/usr/bin/agent-worktrees", "--project", "test-chamber", "embody",
    ]
    assert cmd.index("--project") < cmd.index("embody")


def test_spawn_embodied_worker_omits_project_when_none(monkeypatch):
    """No --project (back-compat): the command is unchanged from CWD-discovery."""
    captured = {}
    monkeypatch.setattr(
        embody, "_agent_worktrees_launch_prefix", lambda: ["/usr/bin/agent-worktrees"]
    )
    monkeypatch.setattr(
        embody.subprocess, "run",
        lambda cmd, **kw: (captured.__setitem__("cmd", cmd)
                           or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")),
    )
    embody.spawn_embodied_worker("t", worker_id="w")
    cmd = captured["cmd"]
    assert "--project" not in cmd
    assert cmd[:2] == ["/usr/bin/agent-worktrees", "embody"]


def test_project_for_task_prefers_registry_name(monkeypatch):
    """The authoritative lane->name registry mapping wins when known."""
    monkeypatch.setattr(
        "agent_dispatch.identity.name_for_repo",
        lambda canonical: "test-chamber" if "test-chamber" in (canonical or "") else None,
    )
    task = {"repo": "gitea.example.com/example-user/test-chamber"}
    assert embody.project_for_task(task) == "test-chamber"


def test_project_for_task_falls_back_to_lane_tail(monkeypatch):
    """Unknown-to-registry lane -> last path segment as best effort."""
    monkeypatch.setattr(
        "agent_dispatch.identity.name_for_repo", lambda _canonical: None
    )
    assert embody.project_for_task(
        {"repo": "gitea.example/org/some-repo/"}
    ) == "some-repo"


def test_project_for_task_none_without_lane():
    assert embody.project_for_task({}) is None
    assert embody.project_for_task({"repo": ""}) is None


def test_fleet_spawn_threads_project_before_embody(monkeypatch):
    """The remote SSH body also runs CWD-neutral, so --project must ride the
    remote argv, before `embody`."""
    captured = {}
    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(
        embody, "run_ssh_command",
        lambda cmd, **kw: (captured.__setitem__("cmd", cmd)
                           or types.SimpleNamespace(returncode=0, stdout="{}", stderr="")),
    )
    embody.spawn_fleet_embodied_worker(
        "emancipation-cube", "t", origin="mantis-counter", owner="fleet-t-abc",
        worker_id="fleet-t-abc", project="test-chamber",
    )
    # cmd == [ssh, -o, BatchMode=yes, host, "<remote_cmd string>"]
    remote_cmd = captured["cmd"][-1]
    assert "--project test-chamber embody" in remote_cmd
    assert remote_cmd.index("--project") < remote_cmd.index("embody")


def test_spawn_worker_for_uses_embody_backend(monkeypatch):
    """`create --spawn --spawn-backend embody` routes to the embody backend."""
    from agent_dispatch import __main__ as m

    calls = {}

    def fake_spawn(task_id, **kwargs):
        calls["task_id"] = task_id
        calls["route"] = kwargs.get("route")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(embody, "embody_available", lambda: True)
    monkeypatch.setattr(embody, "spawn_embodied_worker", fake_spawn)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")

    args = types.SimpleNamespace(
        spawn_backend="embody", url=None, verify_timeout=0,
        spawn_agent="task-worker", run_async=False,
    )
    m._do_spawn(args, {"id": "T7"})
    assert calls["task_id"] == "T7"
    assert calls["route"] == ""  # default local discovery, no baked endpoint


def test_spawn_worker_for_embody_degrades_to_bridge(monkeypatch):
    """When agent-worktrees is absent, the embody backend falls back to bridge."""
    from agent_dispatch import __main__ as m
    from agent_dispatch import bridge

    bridge_calls = {}

    monkeypatch.setattr(embody, "embody_available", lambda: False)
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")

    def fake_bridge_spawn(task_id, **kwargs):
        bridge_calls["task_id"] = task_id
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(bridge, "spawn_worker", fake_bridge_spawn)

    args = types.SimpleNamespace(
        spawn_backend="embody", url=None, verify_timeout=0,
        spawn_agent="task-worker", run_async=False,
    )
    m._do_spawn(args, {"id": "T8"})
    assert bridge_calls["task_id"] == "T8"


# -- remote registered-agent probe (fleet preflight) -------------------------


def test_remote_registered_agent_names_none_when_no_ssh(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: None)
    assert embody.remote_registered_agent_names("pool-a") is None


def test_remote_registered_agent_names_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(
        embody.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 255, "", "unreachable"),
    )
    assert embody.remote_registered_agent_names("pool-a") is None


def test_remote_registered_agent_names_parses_over_ssh(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, '[{"name": "sweep-worker"}]', ""
        )

    monkeypatch.setattr(embody.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(embody, "run_ssh_command", fake_run)
    assert embody.remote_registered_agent_names("Pool-A") == {"sweep-worker"}
    # SSH to the lower-cased alias, running the JSON agents listing.
    assert seen["cmd"][0] == "/usr/bin/ssh"
    assert "pool-a" in seen["cmd"]
    assert seen["cmd"][-1] == "agent-bridge --json agents"
