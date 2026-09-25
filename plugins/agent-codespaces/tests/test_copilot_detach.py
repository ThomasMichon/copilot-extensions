"""Tests for ``agent-codespaces copilot <name> --detach/--stop`` -- the
agent-facing, no-TTY CLI-mode lifecycle (every venue/bridge/Owner seam faked).
"""

from __future__ import annotations

import argparse
import io
import json
import shlex
import types

import pytest
import venue_copilot
from agent_codespaces import config as cs_config
from agent_codespaces import connection_owner as owner
from agent_codespaces import copilot_detach as detach
from agent_codespaces import copilot_venue
from agent_codespaces import session_forwards


def _args(**kw):
    base = dict(
        name="cs-1", worktree_id=None, driver="orchestrator", seed="do the task",
        seed_file=None, copilot_args=["--no-ask-user"], register_timeout=0.0,
        ensure_mux=True, dry_run=False, effort=None, force=False, force_claim=False,
        detach=True, stop=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def seams(monkeypatch):
    calls = types.SimpleNamespace(
        holds=[], releases=[], reserve=[], release_res=[], remote=[], ssh=[], deregistered=[], ref_payloads=[],
        live_rows={"anchor-example-web@cs-1": {"session_id": "sid-42", "venue": {"target": "cs-1"}}},
        claim_rows=[{"reservation_id": "r1", "claimed_by_session_id": "sid-42"}],
    )
    monkeypatch.setattr(
        cs_config, "load_merged_config",
        lambda *a, **k: types.SimpleNamespace(
            resolved_workspace_folder_for=lambda repo: "/workspaces/example-web"),
    )
    import agent_codespaces.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle, "list_codespaces",
        lambda: [types.SimpleNamespace(name="cs-1", repository="example/example-web-vessel")],
    )
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a: None)
    monkeypatch.setattr(copilot_venue, "_ensure_agent_bridge_plugin", lambda n: None)
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda *a, **k: 41234)
    monkeypatch.setattr(owner, "ensure_owner_running", lambda cfg: True)
    monkeypatch.setattr(owner, "hold", lambda *a, **k: calls.holds.append((a, k)))
    monkeypatch.setattr(owner, "release", lambda *a, **k: calls.releases.append((a, k)))
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: None)

    async def _fwd(name, timeout=0):
        return True

    monkeypatch.setattr(session_forwards, "await_owner_bridge_forward", _fwd)
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda n, p: True)
    def _remote(n, c, timeout=60.0, input_bytes=None):
        calls.remote.append(c)
        if input_bytes is not None:
            calls.ref_payloads.append(input_bytes)
            return 0, "/home/codespace/.agent-bridge/refs/batch-1\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", _remote)
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)

    def _reserve(scope, ttl_seconds, venue):
        calls.reserve.append((scope, venue))
        return {"reservation_id": "r1"}

    monkeypatch.setattr(venue_copilot, "reserve_cli_mode", _reserve)
    monkeypatch.setattr(
        venue_copilot, "get_cli_mode_reservation",
        lambda scope: calls.claim_rows.pop(0) if calls.claim_rows else {"reservation_id": "r1"},
    )
    monkeypatch.setattr(
        venue_copilot, "release_cli_mode",
        lambda scope, reservation_id=None: calls.release_res.append((scope, reservation_id)) or 1,
    )
    monkeypatch.setattr(
        venue_copilot, "deregister_live_session",
        lambda sid: calls.deregistered.append(sid) or True,
    )
    monkeypatch.setattr(venue_copilot, "live_session_for", lambda handle: calls.live_rows.get(handle, {}))
    return calls


def _ssh(calls, *, stdout="", stderr="", code=0, rc=None):
    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        remote = remote_cmd_builder(["/stage/example-agent"]) if remote_cmd_builder else ns.remote_cmd
        calls.ssh.append({"ns": ns, "remote": remote, "settle": settle_on_disconnect})
        result = types.SimpleNamespace(exit_code=code, stdout=stdout, stderr=stderr)
        out = result_sink(result) if result_sink else code
        return out if rc is None else rc
    return fake


_CREATED = json.dumps({
    "ok": True, "created": True, "resumed": False, "seed_submitted": True,
    "session": "wt-anchor-example-web",
}, indent=2)


def test_detach_success_reports_exact_session_and_keeps_forwards(seams, capsys):
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout="noise\n" + _CREATED))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["session_id"] == "sid-42"
    assert out["scope_id"] == "anchor-example-web@cs-1"
    assert out["mux_session"] == "wt-anchor-example-web"
    assert out["created"] is True and out["seeded"] is True
    assert out["commands"]["nudge"].startswith("agent-bridge send sid-42")
    (first_args, first_kw), (last_args, last_kw) = seams.holds
    assert first_args == last_args == ("cs-1", "cli:anchor-example-web@cs-1")
    assert first_kw == {"daemon_port": 41234, "mux_session": "wt-anchor-example-web", "fresh": True}
    # Confirmed only once the session is registered and seeded.
    assert last_kw == {"daemon_port": 41234, "mux_session": "wt-anchor-example-web",
                       "confirmed": True}
    assert seams.releases == []  # the session keeps its Owner tenant
    assert seams.release_res == [("anchor-example-web@cs-1", "r1")]  # exact release
    assert seams.reserve[0][1] == {
        "kind": "codespace", "target": "cs-1", "mux_session_name": "wt-anchor-example-web",
    }
    launch = seams.ssh[0]
    assert launch["settle"] is False  # claim stays active while the session runs
    remote = launch["remote"]
    assert remote.startswith("cd /workspaces/example-web && " + detach._VENUE_TOOLING + " && python3 -c ")
    assert detach.trust_folder_command("/workspaces/example-web") in remote
    assert "&& { agent-worktrees get project" in remote
    assert "agent-worktrees register example-web --base-repo --no-agent >&2; }" in remote
    argv = shlex.split("agent-worktrees embody" + remote.split(" && agent-worktrees embody", 1)[1])
    assert argv[:3] == ["agent-worktrees", "embody", "--anchor"]
    assert argv[argv.index("--bridge-scope-id") + 1] == "anchor-example-web@cs-1"
    assert "--copilot-arg=--plugin-dir=/stage/example-agent" in argv
    assert "--copilot-arg=--no-ask-user" in argv
    assert argv[argv.index("--seed") + 1] == "do the task"
    assert launch["ns"].auth_cache_warmup is True and launch["ns"].no_provision is False


def test_missing_codespace_fails_before_claiming_anything(seams, monkeypatch, capsys):
    import agent_codespaces.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "list_codespaces", lambda: [])
    claims = []
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a: claims.append(a) or None)
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams))
    assert rc == 1 and claims == [] and seams.holds == [] and seams.ssh == []
    assert "was not found" in json.loads(capsys.readouterr().out)["error"]


def test_launch_commands_carry_the_claim_owner_it_used(seams, capsys):
    rc = detach.cmd_detach(_args(effort="task-7"), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    assert commands["attach"] == "agent-codespaces copilot cs-1 --effort task-7"
    assert commands["stop"] == "agent-codespaces copilot cs-1 --stop --effort task-7"
    assert seams.ssh[0]["ns"].effort == "task-7"


def test_detach_rejoin_of_running_session_does_not_reseed(seams, capsys):
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=resumed))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["resumed"] is True and out["seeded"] is False
    assert seams.remote == []  # nothing killed


def test_seed_never_submitted_tears_down_and_releases(seams, capsys):
    unready = json.dumps({"ok": True, "created": True, "seed_submitted": False,
                          "seed_reason": "prompt not ready"})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=unready))
    assert rc == 1
    assert "seed was not submitted" in capsys.readouterr().err
    assert any("kill-session" in c for c in seams.remote)
    assert seams.releases and seams.releases[0][0] == ("cs-1", "cli:anchor-example-web@cs-1")
    assert seams.release_res == [("anchor-example-web@cs-1", "r1")]


def test_unregistered_session_is_an_explicit_failure(seams, capsys):
    seams.claim_rows.clear()  # reservation never claimed
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 1
    assert "never registered" in capsys.readouterr().err
    assert any("kill-session" in c for c in seams.remote)
    assert seams.releases


def test_old_venue_tooling_fails_closed(seams, capsys):
    rc = detach.cmd_detach(
        _args(),
        ssh_session=_ssh(seams, stderr="embody: error: unrecognized arguments: --bridge-scope-id", code=2),
    )
    assert rc == 1
    assert "too old" in capsys.readouterr().err
    assert seams.remote == []  # nothing was created, nothing to kill
    assert seams.releases


def test_busy_claim_touches_nothing(seams, monkeypatch):
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a: 75)
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams))
    assert rc == 75
    assert seams.holds == [] and seams.ssh == [] and seams.reserve == []


def test_no_host_bridge_fails_before_any_hold(seams, monkeypatch, capsys):
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda *a, **k: None)
    assert detach.cmd_detach(_args(), ssh_session=_ssh(seams)) == 1
    assert seams.holds == []


def test_unreachable_bridge_path_releases_the_hold(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda n, p: False)
    assert detach.cmd_detach(_args(), ssh_session=_ssh(seams)) == 1
    assert "authenticated probe failed" in capsys.readouterr().err
    assert seams.releases and seams.ssh == []


def test_dry_run_has_no_side_effects(seams, capsys):
    rc = detach.cmd_detach(_args(dry_run=True), ssh_session=_ssh(seams))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["scope_id"] == "anchor-example-web@cs-1"
    assert seams.holds == [] and seams.ssh == [] and seams.reserve == []


def test_seed_file_from_stdin(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach.sys, "stdin", io.StringIO("line 1\nline \"2\"\n"))
    rc = detach.cmd_detach(_args(seed=None, seed_file="-"), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    remote = seams.ssh[0]["remote"]
    staged, embody = remote.split(" && agent-worktrees embody", 1)
    # The multi-line task is staged verbatim to a file; a one-line pointer is typed.
    assert "printf %s " in staged and "/.agent-bridge/seeds/" in staged
    assert shlex.split(staged.split("printf %s ", 1)[1].split(" > ", 1)[0]) == ["line 1\nline \"2\"\n"]
    argv = shlex.split("agent-worktrees embody" + embody)
    pointer = argv[argv.index("--seed") + 1]
    assert "\n" not in pointer and pointer.startswith("Read the task file ~/.agent-bridge/seeds/")


def test_oversized_seed_is_refused(seams, capsys):
    rc = detach.cmd_detach(_args(seed="x" * (detach.MAX_SEED_CHARS + 1)), ssh_session=_ssh(seams))
    assert rc == 1
    assert seams.holds == []


def test_worktree_mode_forwards_the_real_worktree_id(seams, capsys):
    rc = detach.cmd_detach(_args(worktree_id="wt-7"), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    assert "register" not in seams.ssh[0]["remote"]  # worktree mode never adopts
    argv = shlex.split(seams.ssh[0]["remote"].split(" && ", 1)[1])
    assert argv[argv.index("--worktree-id") + 1] == "wt-7"
    assert argv[argv.index("--bridge-scope-id") + 1] == "wt-7@cs-1"


def test_stop_verifies_before_releasing(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False), ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0
    assert "kill-session" in seams.ssh[0]["remote"] and seams.ssh[0]["settle"] is True
    assert seams.release_res == [("anchor-example-web@cs-1", None)]
    assert seams.releases[0][0] == ("cs-1", "cli:anchor-example-web@cs-1")
    assert seams.deregistered == ["sid-42"]
    assert json.loads(capsys.readouterr().out)["deregistered"] == "sid-42"


def test_stop_without_a_live_session_deregisters_nothing(seams, capsys):
    seams.live_rows.clear()
    rc = detach.cmd_stop(_args(stop=True, detach=False), ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0 and seams.deregistered == []


def test_stop_never_deregisters_a_session_of_another_venue(seams, capsys):
    seams.live_rows["anchor-example-web@cs-1"]["venue"] = {"target": "cs-other"}
    rc = detach.cmd_stop(_args(stop=True, detach=False), ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0 and seams.deregistered == []


def test_stop_that_cannot_verify_releases_nothing(seams, capsys):
    rc = detach.cmd_stop(
        _args(stop=True, detach=False),
        ssh_session=_ssh(seams, stdout="STILL_RUNNING\n", code=3),
    )
    assert rc == 1
    assert seams.releases == [] and seams.release_res == [] and seams.deregistered == []


def test_stop_of_a_shutdown_codespace_never_boots_it(seams, monkeypatch, capsys):
    import agent_codespaces.lifecycle as lifecycle

    listed = []
    monkeypatch.setattr(
        lifecycle, "list_codespaces",
        lambda: listed.append(1) or [types.SimpleNamespace(
            name="cs-1", repository="example/example-web-vessel", state="Shutdown")],
    )
    rc = detach.cmd_stop(_args(stop=True, detach=False), ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0 and seams.ssh == []  # nothing to kill on a stopped box
    assert listed == [1]  # one listing serves both the plan and the state
    assert seams.release_res == [("anchor-example-web@cs-1", None)]
    assert seams.releases[0][0] == ("cs-1", "cli:anchor-example-web@cs-1")
    assert seams.deregistered == ["sid-42"]
    out = json.loads(capsys.readouterr().out)
    assert out["already_shutdown"] is True and out["stopped"] is True


def test_stop_of_an_available_codespace_still_verifies_the_kill(seams, monkeypatch, capsys):
    import agent_codespaces.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle, "list_codespaces",
        lambda: [types.SimpleNamespace(
            name="cs-1", repository="example/example-web-vessel", state="Available")],
    )
    rc = detach.cmd_stop(_args(stop=True, detach=False), ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0 and "kill-session" in seams.ssh[0]["remote"]
    assert "already_shutdown" not in json.loads(capsys.readouterr().out)


def test_parser_exposes_detach_lifecycle_flags():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    copilot_venue.add_copilot_subparser(sub)
    args = parser.parse_args([
        "copilot", "cs-1", "--detach", "--seed-file", "-", "--copilot-arg=--autopilot",
        "--register-timeout", "30", "--dry-run",
    ])
    assert args.detach and args.seed_file == "-" and args.copilot_args == ["--autopilot"]
    assert args.register_timeout == 30.0 and args.dry_run
    with pytest.raises(SystemExit):
        parser.parse_args(["copilot", "cs-1", "--detach", "--stop"])


def test_cmd_copilot_routes_detach_and_stop(monkeypatch):
    seen = []
    monkeypatch.setattr(detach, "cmd_detach", lambda a, ssh_session: seen.append("detach") or 0)
    monkeypatch.setattr(detach, "cmd_stop", lambda a, ssh_session: seen.append("stop") or 0)
    assert copilot_venue.cmd_copilot(_args(), interactive_ssh=None, ssh_session=object()) == 0
    assert copilot_venue.cmd_copilot(
        _args(detach=False, stop=True), interactive_ssh=None, ssh_session=object(),
    ) == 0
    assert seen == ["detach", "stop"]


def test_venue_reported_mux_name_wins_for_probe_and_handle(seams, capsys):
    other = json.dumps({"ok": True, "created": True, "seed_submitted": True,
                        "session": "wt-anchor-renamed"})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=other))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mux_session"] == "wt-anchor-renamed"
    assert seams.holds[-1][1]["mux_session"] == "wt-anchor-renamed"
    assert seams.holds[-1][1]["confirmed"] is True


def test_stop_targets_the_recorded_mux_session(seams, monkeypatch, capsys):
    held = types.SimpleNamespace(sessions={
        "cli:anchor-example-web@cs-1": {"mux_session": "wt-anchor-renamed"},
    })
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    rc = detach.cmd_stop(_args(stop=True, detach=False), ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0
    assert "=wt-anchor-renamed" in seams.ssh[0]["remote"]


def test_short_single_line_seed_is_typed_directly():
    typed, prefix = detach.seed_delivery("  fix the flaky test  ", "x@cs-1")
    assert typed == "fix the flaky test" and prefix == ""


def test_long_single_line_seed_is_staged():
    typed, prefix = detach.seed_delivery("y" * 500, "x@cs-1")
    assert prefix and "\n" not in typed and typed.startswith("Read the task file")


def test_trust_folder_snippet_adds_once_and_never_clobbers(tmp_path):
    import os
    import subprocess
    import sys as _sys

    home = tmp_path / "home"
    (home / ".copilot").mkdir(parents=True)
    cfg = home / ".copilot" / "config.json"
    cfg.write_text(json.dumps({"trustedFolders": ["/other"], "keep": 1}), encoding="utf-8")
    snippet = detach._TRUST_FOLDER.split("-c ", 1)[1].strip()
    code = shlex.split(snippet)[0]
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    for _ in range(2):  # idempotent
        subprocess.run([_sys.executable, "-c", code, "/workspaces/example-web"], env=env, check=True)
    got = json.loads(cfg.read_text(encoding="utf-8"))
    assert got == {"trustedFolders": ["/other", "/workspaces/example-web"], "keep": 1}
    # Copilot's own file carries a `//` header; it must survive the rewrite.
    cfg.write_text("// managed automatically\n{\n  \"staff\": true\n}\n", encoding="utf-8")
    subprocess.run([_sys.executable, "-c", code, "/workspaces/example-web"], env=env, check=True)
    text = cfg.read_text(encoding="utf-8")
    assert text.startswith("// managed automatically\n")
    assert json.loads(text.split("\n", 1)[1]) == {
        "staff": True, "trustedFolders": ["/workspaces/example-web"],
    }
    cfg.write_text("{ not json", encoding="utf-8")
    subprocess.run([_sys.executable, "-c", code, "/workspaces/example-web"], env=env, check=True)
    assert cfg.read_text(encoding="utf-8") == "{ not json"  # left untouched


def test_missing_venue_tooling_fails_with_a_precise_message(seams, capsys):
    rc = detach.cmd_detach(
        _args(), ssh_session=_ssh(seams, stderr="bash: line 16: agent-worktrees: command not found", code=127),
    )
    assert rc == 1
    assert "agent-worktrees is not installed on the CodeSpace" in capsys.readouterr().err
    assert seams.releases  # nothing left held


def test_venue_tooling_prefix_is_valid_bash(tmp_path):
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash or "WindowsApps" in bash:
        pytest.skip("no POSIX bash")
    result = subprocess.run([bash, "-n", "-c", detach._VENUE_TOOLING], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_new_session_id_added_unless_resuming():
    fresh = detach.with_new_session(["--no-ask-user"])
    assert fresh[0] == "--no-ask-user" and fresh[1].startswith("--session-id=")
    for resume in (["--resume=abc"], ["--continue"], ["--session-id=x"], ["-r"]):
        assert detach.with_new_session(resume) == resume


def test_launch_passes_a_new_session_id(seams, capsys):
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    assert "--copilot-arg=--session-id=" in seams.ssh[0]["remote"]


def test_launch_holds_a_fresh_generation(seams, capsys):
    detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))
    assert seams.holds[0][1].get("fresh") is True


def test_failed_rejoin_restores_the_running_sessions_tenant(seams, monkeypatch, capsys):
    """A transient failure while rejoining must not cut off the live session's forwards."""
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True,
             "expires_at": 123.0, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda n, p: False)
    assert detach.cmd_detach(_args(), ssh_session=_ssh(seams)) == 1
    assert seams.releases == []
    restore = [k for _a, k in seams.holds if k.get("restore") is not None]
    assert restore and restore[0]["restore"] == prior


def test_reverse_forward_specs_are_validated():
    assert detach.parse_reverse_forwards(["9222:50111", "4321:4321"]) == {9222: 50111, 4321: 4321}
    for bad in (["9222"], ["x:1"], ["0:1"], ["9222:70000"], ["9222:1", "9222:2"]):
        with pytest.raises(ValueError):
            detach.parse_reverse_forwards(bad)


def test_launch_holds_requested_reverse_forwards(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_venue_ports_listening", lambda n, ports: {p: True for p in ports})
    rc = detach.cmd_detach(_args(reverse_forwards=["9222:50111"]), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    assert seams.holds[0][1].get("reverse_forwards") == {9222: 50111}
    out = json.loads(capsys.readouterr().out)
    assert out["reverse_forwards"] == {"9222": 50111}
    assert out["reverse_forwards_ready"] == {"9222": True}


def test_venue_port_probe_retries_until_every_port_listens(monkeypatch):
    replies = [(0, "9222\n", ""), (0, "9222\n4321\n", "")]
    monkeypatch.setattr(detach, "_remote", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._venue_ports_listening("cs-1", [4321, 9222]) == {4321: True, 9222: True}
    assert replies == []


def test_venue_port_probe_reports_a_port_that_never_binds(monkeypatch):
    calls = []
    monkeypatch.setattr(detach, "_remote", lambda *a, **k: calls.append(1) or (0, "", ""))
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._venue_ports_listening("cs-1", [9222], attempts=3) == {9222: False}
    assert len(calls) == 3


def test_launch_without_reverse_forwards_keeps_existing_ones(seams, capsys):
    detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))
    assert "reverse_forwards" not in seams.holds[0][1]


def test_bad_reverse_forward_fails_before_holding(seams, capsys):
    assert detach.cmd_detach(_args(reverse_forwards=["nope"]), ssh_session=_ssh(seams)) == 1
    assert seams.holds == []


def test_failed_rejoin_restores_the_sessions_reverse_forwards(seams, monkeypatch, capsys):
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True,
             "expires_at": 123.0, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior},
                                 reverse_forwards={"9222": 50111})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda n, p: False)
    detach.cmd_detach(_args(reverse_forwards=["9222:50222"]), ssh_session=_ssh(seams))
    restore = [k for _a, k in seams.holds if k.get("restore") is not None]
    assert restore[0]["reverse_forwards"] == {"9222": 50111}


def test_local_forward_specs_are_validated():
    assert detach.parse_local_forwards(["41909", "8080:3000"]) == {41909: 41909, 8080: 3000}
    for bad in (["x"], ["0"], ["70000"], ["1:x"], ["41909:1", "41909:2"]):
        with pytest.raises(ValueError):
            detach.parse_local_forwards(bad)


def test_launch_holds_requested_local_forwards_and_reports_readiness(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_host_ports_listening", lambda ports: {p: True for p in ports})
    rc = detach.cmd_detach(_args(local_forwards=["41909"]), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    assert seams.holds[0][1].get("local_forwards") == {41909: 41909}
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"41909": 41909}
    assert out["local_forwards_ready"] == {"41909": True}


def test_launch_without_local_forwards_keeps_existing_ones(seams, capsys):
    detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))
    assert "local_forwards" not in seams.holds[0][1]


def test_failed_rejoin_restores_the_sessions_local_forwards(seams, monkeypatch, capsys):
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True,
             "expires_at": 123.0, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior},
                                 local_forwards={"41909": 41909})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda n, p: False)
    detach.cmd_detach(_args(local_forwards=["5000"]), ssh_session=_ssh(seams))
    restore = [k for _a, k in seams.holds if k.get("restore") is not None]
    assert restore[0]["local_forwards"] == {"41909": 41909}


def test_host_port_probe_reports_a_port_that_never_binds(monkeypatch):
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._host_ports_listening([1], attempts=2) == {1: False}

def test_failed_fresh_launch_after_a_dead_session_releases(seams, monkeypatch, capsys):
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    created_but_unseeded = _CREATED.replace('"seed_submitted": true', '"seed_submitted": false')
    assert detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=created_but_unseeded)) == 1
    assert seams.releases


def _ref_file(tmp_path, name="trace.har", body='{"log": {"entries": []}}'):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_ref_files_are_copied_before_launch_and_named_in_the_seed(seams, tmp_path, capsys):
    rc = detach.cmd_detach(
        _args(ref_files=[_ref_file(tmp_path)]), ssh_session=_ssh(seams, stdout=_CREATED),
    )
    assert rc == 0
    assert seams.ref_payloads and "base64 -d | tar -xzf -" in seams.remote[0]
    launch = seams.ssh[0]["remote"]
    assert "/home/codespace/.agent-bridge/refs/batch-1/trace.har" in launch  # in the staged seed
    assert "do the task" in launch
    out = json.loads(capsys.readouterr().out)
    assert out["refs_delivered"] == "seed"
    assert any("trace.har" in line for line in out["ref_files"])


def test_ref_files_for_a_running_session_are_sent_as_a_message(seams, tmp_path, monkeypatch, capsys):
    from agent_codespaces import venue_refs

    sent = []
    monkeypatch.setattr(venue_refs, "deliver_note", lambda sid, note: sent.append((sid, note)) or True)
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})
    rc = detach.cmd_detach(
        _args(ref_files=[_ref_file(tmp_path)]), ssh_session=_ssh(seams, stdout=resumed),
    )
    assert rc == 0
    assert sent and sent[0][0] == "sid-42" and "refs/batch-1/trace.har" in sent[0][1]
    assert json.loads(capsys.readouterr().out)["refs_delivered"] == "message"


def test_missing_ref_file_fails_before_touching_anything(seams, capsys):
    rc = detach.cmd_detach(_args(ref_files=["/no/such/trace.har"]), ssh_session=_ssh(seams))
    assert rc == 1
    assert "reference file not found" in capsys.readouterr().err
    assert seams.holds == [] and seams.ssh == [] and seams.reserve == []


def test_ref_file_requires_detach(capsys):
    args = argparse.Namespace(ref_files=["x"], detach=False, stop=False)
    assert copilot_venue.cmd_copilot(args, interactive_ssh=None) == 2


def test_bridge_probe_survives_a_transient_tunnel_reset(monkeypatch):
    results = [None, (0, "", "")]
    monkeypatch.setattr(detach, "_remote", lambda *a, **k: results.pop(0))
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._bridge_path_ok("cs-1", 41234) is True


def test_bridge_probe_gives_up_after_bounded_attempts(monkeypatch):
    calls = []
    monkeypatch.setattr(detach, "_remote", lambda *a, **k: calls.append(1) or (7, "", "refused"))
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._bridge_path_ok("cs-1", 41234) is False
    assert len(calls) == detach._PROBE_ATTEMPTS


def test_transient_connect_failure_is_a_json_failure_and_keeps_a_running_session(seams, monkeypatch, capsys):
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)

    def boom(*a, **k):
        raise RuntimeError("gh codespace ssh --config failed (rc=1)")

    assert detach.cmd_detach(_args(), ssh_session=boom) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "retry" in out["error"]
    assert seams.releases == []  # the running session's tenant was restored, not released


def test_remote_retries_a_failed_connect_once(monkeypatch):
    calls = []

    def fake_run(coro):
        coro.close()
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("gh codespace ssh --config failed: forcibly closed")
        return (0, "ok", "")

    monkeypatch.setattr(detach.asyncio, "run", fake_run)
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._remote("cs-1", "true") == (0, "ok", "")
    assert len(calls) == 2


def test_launch_retries_a_transient_transport_failure_then_succeeds(seams, monkeypatch, capsys):
    outcomes = [types.SimpleNamespace(exit_code=255, stdout="", stderr="ssh: connection reset"),
                types.SimpleNamespace(exit_code=0, stdout=_CREATED, stderr="")]

    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        seams.ssh.append({"ns": ns, "remote": remote_cmd_builder(["/stage/x"]), "settle": settle_on_disconnect})
        return result_sink(outcomes.pop(0))

    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach.cmd_detach(_args(), ssh_session=fake) == 0
    assert len(seams.ssh) == 2
    assert json.loads(capsys.readouterr().out)["session_id"] == "sid-42"


def test_launch_does_not_retry_a_genuine_remote_failure(seams, monkeypatch, capsys):
    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        seams.ssh.append(1)
        return result_sink(types.SimpleNamespace(exit_code=1, stdout='{"ok": false, "error": "boom"}', stderr=""))

    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach.cmd_detach(_args(), ssh_session=fake) == 1
    assert len(seams.ssh) == 1
