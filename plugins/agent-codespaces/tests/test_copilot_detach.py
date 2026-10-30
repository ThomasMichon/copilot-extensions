"""Tests for ``agent-codespaces copilot <name> --detach/--stop`` -- the
agent-facing, no-TTY CLI-mode lifecycle (every venue/bridge/Owner seam faked).
"""

from __future__ import annotations

import argparse
import io
import json
import shlex
import types
import dataclasses
import sqlite3
import subprocess
from venue_copilot.refs import deliver_note as _real_deliver_note

import pytest
import venue_copilot
from agent_codespaces import config as cs_config
from agent_codespaces import connection_owner as owner
from agent_codespaces import copilot_detach as detach
from agent_codespaces import copilot_venue
from agent_codespaces import owner_local_forwards
from agent_codespaces import session_forwards


def _args(**kw):
    base = dict(
        name="cs-1", worktree_id=None, driver="orchestrator", seed="do the task",
        seed_file=None, copilot_args=["--no-ask-user"], register_timeout=0.0,
        ensure_mux=True, dry_run=False, effort=None, force=False, force_claim=False,
        detach=True, stop=False, no_relay=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def seams(monkeypatch):
    monkeypatch.setattr("agent_codespaces.hosting_policy.require_codespace_hosting",
                        lambda *args, **kwargs: None)
    calls = types.SimpleNamespace(
        holds=[], releases=[], reserve=[], release_res=[], remote=[], ssh=[], deregistered=[], ref_payloads=[],
        live_rows={"anchor-example-web@cs-1": {"session_id": "sid-42", "venue": {"target": "cs-1"}},
                   "sid-42": {"session_id": "sid-42", "venue": {"target": "cs-1"}}},
        claim_rows=[{"reservation_id": "r1", "claimed_by_session_id": "sid-42"}], messages=[], pending={},
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
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a, **k: None)
    monkeypatch.setattr(
        copilot_venue,
        "github_credential_preflight",
        lambda n: types.SimpleNamespace(ok=True, to_dict=lambda: {"ok": True}),
    )
    # Hermetic: never read the developer's own ~/.copilot/settings.json model.
    monkeypatch.setattr(detach, "with_supervisor", lambda venue, ref=None: dict(venue))
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: [])
    monkeypatch.setattr(copilot_venue, "_ensure_agent_bridge_plugin", lambda n: None)
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda *a, **k: 41234)
    # A rejoin always probes the daemon's protocol: an alias-capable one here.
    monkeypatch.setattr(venue_copilot, "_daemon_health", lambda port: {"protocol_version": 21})
    monkeypatch.setattr(owner, "ensure_owner_running", lambda cfg: True)
    monkeypatch.setattr(owner, "hold", lambda *a, **k: calls.holds.append((a, k)))
    monkeypatch.setattr(owner, "release", lambda *a, **k: calls.releases.append((a, k)))
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: None)
    def pending(name, tenant, value):
        if value is None:
            calls.pending.pop(tenant, None)
        else:
            calls.pending[tenant] = value
    monkeypatch.setattr(owner, "record_pending_delivery", pending)
    monkeypatch.setattr(owner, "get_pending_delivery", lambda name, tenant: calls.pending.get(tenant))

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
    monkeypatch.setattr(venue_copilot, "live_session_for",
                        lambda handle, strict=False: calls.live_rows.get(handle, {}))
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda sid, note, **kwargs: calls.messages.append((sid, note, kwargs)) or True)
    return calls


def test_repository_refusal_precedes_owner_reservation_and_launch(seams, monkeypatch, capsys):
    def denied(*args, **kwargs):
        raise RuntimeError("expose_agent: false")

    monkeypatch.setattr("agent_codespaces.hosting_policy.require_codespace_hosting", denied)
    assert detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED)) == 1
    assert seams.holds == []
    assert seams.reserve == []
    assert seams.ssh == []
    assert seams.remote == []
    captured = capsys.readouterr()
    assert "expose_agent: false" in captured.out + captured.err


def _ssh(calls, *, stdout="", stderr="", code=0, rc=None):
    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        if hasattr(ns, "plugin_prepared_context"):
            ns.plugin_prepared_context.update({
                "workspace": "/workspaces/example-web", "plugin_context": {
                    "version": 1, "cwd": "/workspaces/example-web",
                    "plugins": [{"projected_source": "example-agent@local"}],
                    "logical_payloads": {"example-agent@local": "/stage/example-agent"},
                },
            })
        remote = remote_cmd_builder(["/stage/example-agent"]) if remote_cmd_builder else ns.remote_cmd
        calls.ssh.append({"ns": ns, "remote": remote, "settle": settle_on_disconnect})
        payload = detach._last_json(stdout)
        if payload.get("ok"):
            payload.setdefault("bridge_seed_deferred", True)
            payload.setdefault("bridge_seed_operation", "r1")
            stdout_result = json.dumps(payload)
        else:
            stdout_result = stdout
        result = types.SimpleNamespace(exit_code=code, stdout=stdout_result, stderr=stderr)
        out = result_sink(result) if result_sink else code
        return out if rc is None else rc
    return fake


def _prepared_namespace(ns):
    ns.plugin_prepared_context.update({
        "workspace": "/workspaces/example-web", "plugin_context": {
            "version": 1, "cwd": "/workspaces/example-web",
            "plugins": [{"projected_source": "example-agent@local"}],
            "logical_payloads": {"example-agent@local": "/stage/example-agent"},
        },
    })


def _refused_delivery(sid, note, kwargs):
    from venue_copilot.refs import owned_result
    return dataclasses.replace(owned_result(
        False, session_id=sid, operation=kwargs["operation"],
        note=note, context=kwargs["prepared_context"],
    ), status="refused")


def test_pending_delivery_roundtrips_in_existing_owner_hold_and_survives_heartbeat(monkeypatch, tmp_path):
    import contextlib
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(owner, "_owner_lock", contextlib.nullcontext)
    owner.hold("synthetic", "cli:scope", mux_session="wt-scope", confirmed=True)
    pending = {"version": 1, "status": "unknown", "operation": "operation", "session_id": "native"}
    owner.record_pending_delivery("synthetic", "cli:scope", pending)
    owner.heartbeat("synthetic", "cli:scope")
    owner.hold("synthetic", "cli:scope", mux_session="wt-scope", confirmed=True)
    assert owner.get_pending_delivery("synthetic", "cli:scope") == pending
    with pytest.raises(RuntimeError, match="must be reconciled"):
        owner.hold("synthetic", "cli:scope", mux_session="wt-new", fresh=True)
    owner.record_pending_delivery("synthetic", "cli:scope", None)
    assert owner.get_pending_delivery("synthetic", "cli:scope") is None


@pytest.mark.parametrize("expiry", ["absolute-lease", "stale-heartbeat"])
@pytest.mark.parametrize("embedded", [False, True])
def test_pending_journal_outlives_tenant_expiry_without_renewing_connection(
    monkeypatch, tmp_path, expiry, embedded,
):
    import contextlib
    from ssh_manager.forward_keeper import KeeperStore

    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr(owner, "_owner_lock", contextlib.nullcontext)
    now = [100000.0]
    monkeypatch.setattr(owner.time, "time", lambda: now[0])
    hold = owner.hold(
        "synthetic", "cli:scope", mux_session="wt-scope", daemon_port=41234,
        confirmed=True, max_lease=10 if expiry == "absolute-lease" else 10000,
    )
    expires_at = hold.sessions["cli:scope"]["expires_at"]
    pending = {
        "version": 1, "status": "unknown", "operation": "operation", "session_id": "native",
        "creation_receipt": {"seed": {"text": "original creation task"}},
        "mux_session": "wt-scope", "daemon_port": 41234,
    }
    if embedded:
        raw = json.loads(owner.OWNER_FILE.read_text())
        raw["synthetic"]["sessions"]["cli:scope"]["pending_delivery"] = pending
        owner.OWNER_FILE.write_text(json.dumps(raw), encoding="utf-8")
    else:
        owner.record_pending_delivery("synthetic", "cli:scope", pending)
    now[0] += 11 if expiry == "absolute-lease" else owner.DEFAULT_TTL + 1
    assert owner.get_hold("synthetic") is None
    assert owner.get_pending_delivery("synthetic", "cli:scope") == pending
    assert json.loads(owner.OWNER_FILE.read_text()) == {}
    assert expires_at == 100000 + (10 if expiry == "absolute-lease" else 10000)
    owner.hold("synthetic", "other")
    owner.release("synthetic", "other")
    assert owner.get_pending_delivery("synthetic", "cli:scope") == pending
    owner.record_pending_delivery("synthetic", "cli:scope", None)
    assert owner.get_pending_delivery("synthetic", "cli:scope") is None
    assert not KeeperStore(tmp_path / "pending-deliveries").state_path("synthetic").exists()


_CREATED = json.dumps({
    "ok": True, "created": True, "resumed": False, "seed_submitted": False,
    "bridge_seed_deferred": True, "bridge_seed_operation": "r1",
    "session": "wt-anchor-example-web",
}, indent=2)


@pytest.mark.parametrize("child_alive", [False, True])
@pytest.mark.parametrize("failure", [None, "copy", "install"])
def test_detached_live_ledger_requires_preparation_before_remote_decision(
    seams, monkeypatch, capsys, child_alive, failure,
):
    seams.live_rows["anchor-example-web@cs-1"]["status"] = "live"
    monkeypatch.setattr("agent_codespaces.launch_policy.refused_exit_code", lambda *a: None)
    events = []
    result = json.dumps({"ok": True, "created": not child_alive, "resumed": child_alive})
    remote = _ssh(seams, stdout=result)

    def ssh(args, **kwargs):
        if not args.skip_plugin_preparation:
            events.append("prepare")
            assert args.stage_plugins == [{"source": "sample@synthetic", "enable": False}]
            if failure:
                raise RuntimeError(f"plugin {failure} failed")
        events.append("reuse" if child_alive else "create")
        return remote(args, **kwargs)

    assert detach.cmd_detach(
        _args(seed=None, stage_plugins=[{"source": "sample@synthetic", "enable": False}]),
        ssh_session=ssh,
    ) == (1 if failure else 0)
    assert events == (["prepare"] if failure else ["prepare", "reuse" if child_alive else "create"])
    if not failure:
        out = json.loads(capsys.readouterr().out)
        assert out["created"] is not child_alive and out["resumed"] is child_alive
    if child_alive:
        assert not any("tmux kill-session" in command for command in seams.remote)


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
    assert launch["ns"].timeout == 1320.0  # 300s lifecycle lock + 900s seed cap + 120s overhead
    remote = launch["remote"]
    assert remote.startswith("cd /workspaces/example-web && " + detach._VENUE_TOOLING + " && python3 -c ")
    assert detach.trust_folder_command("/workspaces/example-web") in remote
    assert "&& { agent-worktrees get project" in remote
    assert "agent-worktrees register example-web --base-repo --no-agent >&2; }" in remote
    argv = shlex.split("agent-worktrees embody" + remote.split(" && agent-worktrees embody", 1)[1])
    assert argv[:3] == ["agent-worktrees", "embody", "--anchor"]
    assert argv[argv.index("--bridge-scope-id") + 1] == "anchor-example-web@cs-1"
    assert "--copilot-arg=--plugin-dir=/stage/example-agent" not in argv
    assert "--copilot-arg=--no-ask-user" in argv
    assert "--seed" not in argv and "--seed-ready-timeout" not in argv
    assert argv[argv.index("--bridge-seed-operation") + 1] == "r1"
    assert "--bridge-seed-pending" in argv
    assert seams.messages[0][1] == "do the task"
    assert seams.messages[0][2]["prepared_context"]["plugins"] == [{"projected_source": "example-agent@local"}]
    assert launch["ns"].auth_cache_warmup is True and launch["ns"].no_provision is False


def test_missing_codespace_fails_before_claiming_anything(seams, monkeypatch, capsys):
    import agent_codespaces.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "list_codespaces", lambda: [])
    claims = []
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a, **k: claims.append(a) or None)
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams))
    assert rc == 1 and claims == [] and seams.holds == [] and seams.ssh == []
    assert "was not found" in json.loads(capsys.readouterr().out)["error"]


def test_launch_commands_carry_the_claim_owner_it_used(seams, capsys):
    rc = detach.cmd_detach(_args(effort="task-7"), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    commands = json.loads(capsys.readouterr().out)["commands"]
    assert commands["attach"] == "agent-codespaces copilot cs-1 --effort task-7"
    assert commands["rejoin"] == "agent-codespaces copilot cs-1 --detach --effort task-7"
    assert commands["stop"] == "agent-codespaces copilot cs-1 --stop --effort task-7"
    assert seams.ssh[0]["ns"].effort == "task-7"


def test_detach_rejoin_of_running_session_does_not_reseed(seams, capsys):
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=resumed))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["resumed"] is True and out["seeded"] is False
    assert seams.messages == []
    assert seams.remote == []  # nothing killed


def test_seed_never_submitted_but_registered_is_delivered_over_bridge(seams, monkeypatch, capsys):
    from venue_copilot import refs as venue_refs

    sent = []
    monkeypatch.setattr(venue_refs, "deliver_note", lambda sid, note, **kw: sent.append((sid, note)) or True)
    unready = json.dumps({"ok": True, "created": True, "seed_submitted": False,
                          "seed_reason": "not-ready-timeout"})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=unready))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["seed_delivery"] == "bridge"
    assert out["seeded"] is True
    assert sent == [("sid-42", "do the task")]
    assert not any("kill-session" in c for c in seams.remote)
    assert seams.holds[-1][1]["confirmed"] is True
    assert seams.releases == []
    assert seams.release_res == [("anchor-example-web@cs-1", "r1")]


def test_a_typed_but_unsubmitted_seed_is_not_resent_over_bridge(seams, monkeypatch, capsys):
    """An echo-confirmed draft may still be in Copilot's input; a bridge copy
    could run the task twice, so the session is kept and the seed reported failed."""
    from venue_copilot import refs as venue_refs

    sent = []
    monkeypatch.setattr(venue_refs, "deliver_note", lambda *a, **k: sent.append(a) or True)
    drafted = json.dumps({"ok": True, "created": True, "seeded": True, "seed_submitted": False,
                          "seed_reason": "enter-failed"})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=drafted))
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert "never typed" in out["error"]
    assert sent == []


@pytest.mark.parametrize("daemon_has_aliases", [True, False])
def test_a_resumed_seed_needs_a_daemon_that_follows_renames(
    seams, monkeypatch, capsys, daemon_has_aliases,
):
    """A resume may re-register under a new id after the claim: the seed is only
    reported delivered when the daemon carries it across that rename."""
    from venue_copilot import refs as venue_refs

    sent = []
    monkeypatch.setattr(
        venue_refs, "deliver_note",
        lambda sid, note, **kw: sent.append({k: v for k, v in kw.items() if k != "operation"})
        or (True if daemon_has_aliases else _refused_delivery(sid, note, kw)),
    )
    unready = json.dumps({"ok": True, "created": True, "seed_submitted": False})
    rc = detach.cmd_detach(_args(copilot_args=["--resume=abc"]), ssh_session=_ssh(seams, stdout=unready))
    assert rc == (0 if daemon_has_aliases else 1)
    assert len(sent) == 1 and sent[0]["min_daemon_protocol"] == 41
    assert sent[0]["prepared_context"]["version"] == 1
    out = json.loads(capsys.readouterr().out)
    if daemon_has_aliases:
        assert out["seed_delivery"] == "bridge" and out["seeded"] is True
    else:
        assert "not admitted" in out["error"]


def test_a_resume_whose_placeholder_exited_and_resumed_id_never_registered_fails(seams, capsys):
    """The placeholder that claimed exited and the resumed id never registered
    (seen live: a bridge that refused the resumed id's new process): there is no
    session, so the launch fails rather than report the gone placeholder."""
    seams.live_rows = {}
    resumed = json.dumps({"ok": True, "created": True, "seed_submitted": False})
    rc = detach.cmd_detach(_args(copilot_args=["--resume=sid-9"], seed=None),
                           ssh_session=_ssh(seams, stdout=resumed))
    assert rc != 0
    text = capsys.readouterr().out
    out = json.loads(text[text.index("{"):])
    assert out["ok"] is False and "never registered" in out["error"]


def test_a_resume_from_a_new_process_keeps_its_venue_and_reports_the_resumed_id(seams, capsys):
    """The placeholder that claimed exited; the resumed id registered from a new
    process (an extension-host restart): the claim is renewed with the venue."""
    seams.live_rows = {"sid-9": {"session_id": "sid-9"}}  # sid-42 (the placeholder) is gone
    seams.claim_rows += [{"reservation_id": "r1", "claimed_by_session_id": "sid-42"},  # still the stale claim
                         {"reservation_id": "r1", "claimed_by_session_id": "sid-9"}]  # the renewal, claimed
    resumed = json.dumps({"ok": True, "created": True, "seed_submitted": False})
    rc = detach.cmd_detach(_args(copilot_args=["--resume=sid-9"], seed=None),
                           ssh_session=_ssh(seams, stdout=resumed))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["session_id"] == "sid-9"
    assert len(seams.reserve) == 2 and seams.reserve[1] == seams.reserve[0]  # same scope + venue
    assert seams.release_res[0] == ("anchor-example-web@cs-1", "r1")

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
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a, **k: 75)
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams))
    assert rc == 75
    assert seams.holds == [] and seams.ssh == [] and seams.reserve == []


def test_github_credential_unavailable_warns_and_continues(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        copilot_venue,
        "github_credential_preflight",
        lambda n: types.SimpleNamespace(
            ok=False,
            detail="no github credential",
            reason_code="github-credential-unavailable",
            remedy="sign in",
            to_dict=lambda: {
                "ok": False,
                "reason_code": "github-credential-unavailable",
                "remedy": "sign in",
            },
        ),
    )

    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    err = capsys.readouterr().err
    assert "github-credential-unavailable" in err
    assert seams.holds and seams.ssh


def test_github_credential_ambiguity_warns_and_continues(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        copilot_venue,
        "github_credential_preflight",
        lambda n: types.SimpleNamespace(
            ok=False,
            detail="ambiguous github credential",
            reason_code="github-credential-ambiguous",
            remedy="bind account",
            to_dict=lambda: {"ok": False},
        ),
    )

    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    err = capsys.readouterr().err
    assert "github-credential-ambiguous" in err
    assert seams.holds and seams.ssh


def test_no_relay_skips_github_credential_preflight(seams, monkeypatch):
    monkeypatch.setattr(
        copilot_venue,
        "github_credential_preflight",
        lambda n: (_ for _ in ()).throw(AssertionError("must not preflight")),
    )

    rc = detach.cmd_detach(
        _args(no_relay=True), ssh_session=_ssh(seams, stdout=_CREATED),
    )

    assert rc == 0


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


def test_detached_session_mirrors_the_callers_model(seams, monkeypatch, capsys):
    seen = []

    def _model_args(existing):
        seen.append(list(existing))
        return ["--model=example-model", "--reasoning-effort=high"]

    monkeypatch.setattr(detach, "model_copilot_args", _model_args)
    rc = detach.cmd_detach(_args(dry_run=True), ssh_session=_ssh(seams))
    assert rc == 0
    args = json.loads(capsys.readouterr().out)["copilot_args"]
    assert seen == [["--no-ask-user"]]
    assert args[:3] == ["--no-ask-user", "--model=example-model", "--reasoning-effort=high"]
    assert args[3].startswith("--session-id=")


def test_seed_file_from_stdin(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach.sys, "stdin", io.StringIO("line 1\nline \"2\"\n"))
    rc = detach.cmd_detach(_args(seed=None, seed_file="-"), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == 0
    remote = seams.ssh[0]["remote"]
    staged, embody = remote.split(" && agent-worktrees embody", 1)
    assert "/.agent-bridge/seeds/" not in staged and "printf %s " not in staged
    argv = shlex.split("agent-worktrees embody" + embody)
    assert "--seed" not in argv
    assert seams.messages[0][1] == "line 1\nline \"2\"\n"


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


def test_stop_keep_claim_never_settles_the_claim_on_disconnect(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False, keep_claim=True),
                         ssh_session=_ssh(seams, stdout="STOPPED\n"))
    assert rc == 0
    assert "kill-session" in seams.ssh[0]["remote"]
    assert seams.ssh[0]["settle"] is False  # the task keeps the box for finalize
    assert seams.deregistered == ["sid-42"]  # the session itself is still fully stopped


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
    assert parser.parse_args(["copilot", "cs-1"]).ttl_seconds is None
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


def test_ttl_seconds_is_usage_error_with_detach_or_stop(monkeypatch, capsys):
    monkeypatch.setattr(detach, "cmd_detach", lambda a, ssh_session: 99)
    monkeypatch.setattr(detach, "cmd_stop", lambda a, ssh_session: 99)

    assert copilot_venue.cmd_copilot(
        _args(ttl_seconds=10.0), interactive_ssh=None, ssh_session=object(),
    ) == 2
    assert copilot_venue.cmd_copilot(
        _args(detach=False, stop=True, ttl_seconds=10.0),
        interactive_ssh=None,
        ssh_session=object(),
    ) == 2
    assert "--ttl-seconds applies only to attached mode" in capsys.readouterr().err


def test_venue_reported_mux_name_wins_for_probe_and_handle(seams, capsys):
    other = json.dumps({"ok": True, "created": True, "seed_submitted": False,
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


def test_short_attached_single_line_seed_keeps_legacy_typing_helper():
    typed, prefix = venue_copilot.seed_delivery("  fix the flaky test  ", "x@cs-1")
    assert typed == "fix the flaky test" and prefix == ""


def test_long_attached_single_line_seed_keeps_legacy_staging_helper():
    typed, prefix = venue_copilot.seed_delivery("y" * 500, "x@cs-1")
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
    if bash and "system32" in bash.lower():
        from pathlib import Path
        git = shutil.which("git")
        native = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        bash = str(native) if native and native.is_file() else None
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
    assert restore and restore[0]["restore"] == {**prior, "assigned_local_forwards": {}}


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
    assert detach.parse_local_forwards(["41909", "8080:3000", "0:3001"]) == {
        41909: 41909, 8080: 3000, 0: 3001,
    }
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


def test_launch_reports_assigned_local_forward_from_owner_hold(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        owner_local_forwards, "read_active_local_forwards", lambda: {"cs-1": {49152: 3000}},
    )
    calls = {"get_hold": 0}

    def get_hold(*a, **k):
        calls["get_hold"] += 1
        if calls["get_hold"] == 1:
            return None
        return types.SimpleNamespace(
            local_forwards={"49152": 3000},
            assigned_local_forwards={"49152": 3000},
        )

    monkeypatch.setattr(owner, "get_hold", get_hold)

    rc = detach.cmd_detach(_args(local_forwards=["0:3000"]), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    assert seams.holds[0][1].get("local_forwards") == {0: 3000}
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"49152": 3000}
    assert out["local_forwards_ready"] == {"49152": True}
    assert out["commands"]["rejoin"] == "agent-codespaces copilot cs-1 --detach"


def test_repeated_dynamic_local_forward_reuses_prior_assigned_port(seams, monkeypatch, capsys):
    prior = types.SimpleNamespace(
        sessions={"cli:anchor-example-web@cs-1": {
            "mux_session": "wt-anchor-example-web", "confirmed": True,
        }},
        reverse_forwards={},
        local_forwards={"49152": 3000},
        assigned_local_forwards={"49152": 3000},
    )
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: prior)
    monkeypatch.setattr(
        owner_local_forwards, "read_active_local_forwards", lambda: {"cs-1": {49152: 3000}},
    )
    monkeypatch.setattr(
        owner, "hold",
        lambda *a, **k: seams.holds.append((a, {**k, "local_forwards": {49152: 3000}})),
    )

    rc = detach.cmd_detach(_args(local_forwards=["0:3000"]), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    assert seams.holds[0][1].get("local_forwards") == {49152: 3000}
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"49152": 3000}
    assert out["local_forwards_ready"] == {"49152": True}
    assert "local_forwards_pending" not in out


def test_rejoin_reports_current_reassigned_dynamic_local_forward(seams, monkeypatch, capsys):
    prior = types.SimpleNamespace(
        sessions={"cli:anchor-example-web@cs-1": {
            "mux_session": "wt-anchor-example-web", "confirmed": True,
        }},
        reverse_forwards={},
        local_forwards={"49152": 3000},
        assigned_local_forwards={"49152": 3000},
    )
    current = types.SimpleNamespace(
        local_forwards={"49153": 3000},
        assigned_local_forwards={"49153": 3000},
    )
    calls = {"get_hold": 0}

    def get_hold(*a, **k):
        calls["get_hold"] += 1
        return prior if calls["get_hold"] == 1 else current

    monkeypatch.setattr(owner, "get_hold", get_hold)
    monkeypatch.setattr(
        owner_local_forwards, "read_active_local_forwards", lambda: {"cs-1": {49153: 3000}},
    )

    rc = detach.cmd_detach(_args(seed=None), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"49153": 3000}
    assert out["local_forwards_ready"] == {"49153": True}


def test_fixed_local_forward_same_venue_as_prior_dynamic_reports_without_assignment_wait(
    seams, monkeypatch, capsys,
):
    prior = types.SimpleNamespace(
        sessions={"cli:anchor-example-web@cs-1": {
            "mux_session": "wt-anchor-example-web", "confirmed": True,
        }},
        reverse_forwards={},
        local_forwards={"49152": 3000},
        assigned_local_forwards={"49152": 3000},
    )
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: prior)
    monkeypatch.setattr(detach, "_host_ports_listening", lambda ports: {p: True for p in ports})
    monkeypatch.setattr(
        owner_local_forwards,
        "read_active_local_forwards",
        lambda: (_ for _ in ()).throw(AssertionError("fixed request must not consult Owner-owned readiness")),
    )

    rc = detach.cmd_detach(
        _args(local_forwards=["8080:3000"]), ssh_session=_ssh(seams, stdout=_CREATED),
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"8080": 3000}
    assert out["local_forwards_ready"] == {"8080": True}
    assert "local_forwards_pending" not in out


def test_dynamic_local_forward_assignment_timeout_reports_pending_success(seams, capsys):
    rc = detach.cmd_detach(_args(local_forwards=["0:3000"]), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["session_id"] == "sid-42"
    assert out["commands"]["rejoin"] == "agent-codespaces copilot cs-1 --detach"
    assert out["local_forwards_pending"] == {"0": 3000}
    assert "local_forwards" not in out
    assert "pre-upgrade Owner" in out["error"]


def test_rejoin_whose_owner_never_reports_keeps_a_prior_dynamic_forward_pending(
    seams, monkeypatch, capsys,
):
    prior = types.SimpleNamespace(
        sessions={"cli:anchor-example-web@cs-1": {
            "mux_session": "wt-anchor-example-web", "confirmed": True,
        }},
        reverse_forwards={},
        local_forwards={"49152": 3000},
        assigned_local_forwards={"49152": 3000},
    )
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: prior)

    def never_ready(*_a, **_k):
        raise TimeoutError("the Connection Owner beacon never became ready")

    monkeypatch.setattr(detach, "_reported_local_forwards", never_ready)
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards_pending"] == {"0": 3000}
    assert "never became ready" in out["error"]


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


def test_failed_rejoin_restores_assigned_local_forward_provenance(seams, monkeypatch, capsys):
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True,
             "expires_at": 123.0, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior},
                                 local_forwards={"41909": 5000},
                                 assigned_local_forwards={"41909": 5000})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda n, p: False)
    detach.cmd_detach(_args(local_forwards=["6000"]), ssh_session=_ssh(seams))
    restore = [k for _a, k in seams.holds if k.get("restore") is not None]
    assert restore[0]["restore"]["assigned_local_forwards"] == {"41909": 5000}
    assert restore[0]["local_forwards"] == {"41909": 5000}


def test_host_port_probe_reports_a_port_that_never_binds(monkeypatch):
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach._host_ports_listening([1], attempts=2) == {1: False}

def test_failed_fresh_launch_after_a_dead_session_releases(seams, monkeypatch, capsys):
    prior = {"mux_session": "wt-anchor-example-web", "confirmed": True, "generation": "g-old"}
    held = types.SimpleNamespace(sessions={"cli:anchor-example-web@cs-1": prior})
    monkeypatch.setattr(owner, "get_hold", lambda *a, **k: held)
    seams.claim_rows.clear()
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
    assert "/home/codespace/.agent-bridge/refs/batch-1/trace.har" not in launch
    assert "do the task" not in launch
    assert "do the task" in seams.messages[0][1] and "refs/batch-1/trace.har" in seams.messages[0][1]
    assert len(seams.messages) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["refs_delivered"] == "message"
    assert any("trace.har" in line for line in out["ref_files"])


@pytest.mark.parametrize("created", [False, True])
def test_owned_readiness_refusal_never_falls_back_or_reports_seeded(
    seams, monkeypatch, capsys, tmp_path, created,
):
    attempted = []
    def refuse(sid, note, **kwargs):
        attempted.append(kwargs)
        return _real_deliver_note(
            sid, note, **kwargs,
            run=lambda *a, **k: types.SimpleNamespace(returncode=69, stdout=json.dumps({
                "owned_delivery_version": 1, "admission_status": "not_admitted",
                "reason": "plugins_not_ready", "error": "private readiness detail",
            })),
        )
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        refuse)
    output = json.dumps({"ok": True, "created": created})
    args = _args() if created else _args(ref_files=[_ref_file(tmp_path)])
    assert detach.cmd_detach(args, ssh_session=_ssh(seams, stdout=output)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and "not admitted" in report["error"]
    assert report["refusal_reason"] == "plugins_not_ready"
    assert "plugins_not_ready" in report["error"]
    assert "private readiness detail" not in json.dumps(report)
    assert len(attempted) == 1 and attempted[0]["prepared_context"]["version"] == 1
    assert any("tmux kill-session" in command for command in seams.remote) is created


def test_deferred_creation_seed_is_queued_once_and_finished_by_receipt(seams, capsys):
    receipt = {"seed": {"text": "creation task", "handoff_id": "bridge-original-operation"}}
    output = json.dumps({
        "ok": True, "created": True, "bridge_seed_deferred": True,
        "bridge_seed_operation": "original-operation", "deferred_creation_seed": receipt,
    })
    assert detach.cmd_detach(_args(worktree_id="wt-7", seed=None),
                             ssh_session=_ssh(seams, stdout=output)) == 0
    assert len(seams.messages) == 1 and seams.messages[0][1] == "creation task"
    assert seams.messages[0][2]["operation"] == "original-operation"
    assert any("agent-worktrees bridge-seed-settle --worktree-id wt-7" in value for value in seams.remote)
    assert json.loads(capsys.readouterr().out)["seed_delivery"] == "bridge"


def test_admitted_seed_with_lost_responses_retains_owner_and_rejoins_same_operation(
    seams, monkeypatch, capsys, tmp_path,
):
    queue = sqlite3.connect(tmp_path / "admitted.sqlite")
    queue.execute("CREATE TABLE messages (key TEXT PRIMARY KEY, body TEXT)")
    attempts = []
    lost = [True]
    def run(argv, **kwargs):
        key = argv[argv.index("--idempotency-key") + 1]
        attempts.append((list(argv), kwargs["input"]))
        queue.execute("INSERT OR IGNORE INTO messages VALUES (?,?)", (key, kwargs["input"]))
        queue.commit()
        if lost[0]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            "accepted": True, "session_id": "sid-42", "message_id": 1, "duplicate": True,
            "receipt": {"session_id": "sid-42", "stage": "admitted"},
        }))
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda sid, note, **kwargs: _real_deliver_note(
                            sid, note, run=run, **{k: v for k, v in kwargs.items() if k != "run"}))
    receipt = {"seed": {"text": "creation task", "handoff_id": "bridge-r1"}}
    output = json.dumps({**json.loads(_CREATED), "deferred_creation_seed": receipt})
    assert detach.cmd_detach(_args(worktree_id="wt-7"), ssh_session=_ssh(seams, stdout=output)) == 75
    report = json.loads(capsys.readouterr().out)
    assert report["pending"] is True and report["pending_delivery"]["status"] == "unknown"
    assert queue.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert not any("tmux kill-session" in value for value in seams.remote)
    assert seams.release_res == [] and seams.releases == []
    pending = seams.pending["cli:wt-7@cs-1"]
    assert pending["creation_receipt"] == receipt and pending["operation"] == "r1"
    # Connection expiry does not remove the separately retained delivery.
    monkeypatch.setattr(owner, "get_hold", lambda *args: None)
    def deny_new_launch(*args, **kwargs):
        raise RuntimeError("expose_agent: false must not block retained delivery settlement")
    monkeypatch.setattr("agent_codespaces.hosting_policy.admit_detached_launch", deny_new_launch)
    # A failed reconciliation preserves the ledger and never relaunches.
    assert detach.cmd_detach(_args(worktree_id="wt-7"), ssh_session=_ssh(seams)) == 75
    capsys.readouterr()
    lost[0] = False
    assert detach.cmd_detach(_args(worktree_id="wt-7"), ssh_session=_ssh(seams)) == 0
    assert json.loads(capsys.readouterr().out)["message_id"] == 1
    assert len(seams.ssh) == 1 and seams.pending == {}
    recovery_holds = [(a, kw) for a, kw in seams.holds if ":delivery-recovery:" in a[1]]
    assert len(recovery_holds) == 2
    assert len({a[1] for a, _ in recovery_holds}) == 2
    assert all(kw["daemon_port"] == 41234 and kw["mux_session"] == pending["mux_session"]
               and kw["max_lease"] == 600 for _, kw in recovery_holds)
    assert all(":delivery-recovery:" in a[1] for a, _ in seams.releases)
    assert queue.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    assert all(value == attempts[0] for value in attempts)
    queue.close()


def test_vanished_pending_delivery_rechecks_hosting_before_new_launch(seams, monkeypatch, capsys):
    snapshots = iter([{"status": "admitted"}, None])
    monkeypatch.setattr(owner, "get_pending_delivery", lambda *args: next(snapshots))
    def deny(*args, **kwargs):
        raise RuntimeError("expose_agent: false")
    monkeypatch.setattr("agent_codespaces.hosting_policy.admit_detached_launch", deny)
    assert detach.cmd_detach(
        _args(worktree_id="wt-7"), ssh_session=_ssh(seams),
    ) == 1
    assert "expose_agent: false" in json.loads(capsys.readouterr().out)["error"]
    assert seams.ssh == [] and seams.holds == []


def test_pending_recovery_probe_failure_retains_journal_without_task_or_mux_launch(
    seams, monkeypatch, capsys,
):
    from venue_copilot.refs import owned_result

    context = {"version": 1, "cwd": "/workspace", "plugins": []}
    pending = {
        **owned_result(False, session_id="sid-42", operation="operation",
                       note="original task", context=context).recovery(),
        "envelope": {"prompt": "original task", "plugin_context": context},
        "mux_session": "original-mux", "daemon_port": 41234,
    }
    seams.pending["cli:wt-7@cs-1"] = pending
    monkeypatch.setattr(detach, "_bridge_path_ok", lambda name, port: False)
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda *a, **kw: pytest.fail("unverified forward cannot authorize replay"))
    assert detach.cmd_detach(_args(worktree_id="wt-7"), ssh_session=_ssh(seams)) == 75
    assert json.loads(capsys.readouterr().out)["pending"] is True
    assert seams.pending["cli:wt-7@cs-1"] == pending
    assert seams.ssh == [] and seams.release_res == []
    assert len(seams.holds) == len(seams.releases) == 1
    assert seams.holds[0][0][1] == seams.releases[0][0][1]
    assert seams.holds[0][0][1].startswith("cli:wt-7@cs-1:delivery-recovery:")


def test_confirmed_seed_retries_only_settlement_after_native_session_exit(seams, monkeypatch, capsys):
    from venue_copilot.refs import owned_result

    def admit(sid, note, **kwargs):
        return dataclasses.replace(owned_result(
            False, session_id=sid, operation=kwargs["operation"],
            note=note, context=kwargs["prepared_context"],
        ), status="admitted", message_id=17)
    monkeypatch.setattr("venue_copilot.refs.deliver_note", admit)
    receipt = {"seed": {"text": "creation task", "handoff_id": "bridge-r1"}}
    original_remote = detach._remote
    fail_settlement = [True]
    def remote(name, command, **kwargs):
        if "bridge-seed-settle" in command and fail_settlement[0]:
            return 1, "", "settlement unavailable"
        return original_remote(name, command, **kwargs)
    monkeypatch.setattr(detach, "_remote", remote)
    output = json.dumps({**json.loads(_CREATED), "deferred_creation_seed": receipt})
    args = _args(worktree_id="wt-7")
    assert detach.cmd_detach(args, ssh_session=_ssh(seams, stdout=output)) == 75
    capsys.readouterr()
    assert seams.pending["cli:wt-7@cs-1"]["status"] == "admitted"
    fail_settlement[0] = False
    monkeypatch.setattr("venue_copilot.refs.deliver_note",
                        lambda *a, **kw: pytest.fail("the native session has exited; do not resend"))
    holds = len(seams.holds)
    assert detach.cmd_detach(args, ssh_session=_ssh(seams)) == 0
    assert json.loads(capsys.readouterr().out)["message_id"] == 17
    assert seams.pending == {} and len(seams.ssh) == 1
    assert len(seams.holds) == holds


def test_ref_files_for_a_running_session_are_sent_as_a_message(seams, tmp_path, monkeypatch, capsys):
    from venue_copilot import refs as venue_refs

    sent = []
    monkeypatch.setattr(venue_refs, "deliver_note", lambda sid, note, **kw: sent.append((sid, note)) or True)
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})
    rc = detach.cmd_detach(
        _args(ref_files=[_ref_file(tmp_path)]), ssh_session=_ssh(seams, stdout=resumed),
    )
    assert rc == 0
    assert sent and sent[0][0] == "sid-42" and "refs/batch-1/trace.har" in sent[0][1]
    assert json.loads(capsys.readouterr().out)["refs_delivered"] == "message"


@pytest.mark.parametrize("copilot_args", [["--resume=abc"], []])
@pytest.mark.parametrize("daemon_has_aliases", [True, False])
def test_a_resumed_rejoin_sends_ref_notes_only_through_a_daemon_that_follows_renames(
    seams, tmp_path, monkeypatch, capsys, daemon_has_aliases, copilot_args,
):
    """A rejoin can claim a still-resuming session's placeholder id: an older
    daemon would strand the note in that placeholder's inbox, so it is refused.
    A flagless rejoin too: its flags say nothing about how the session started."""
    from venue_copilot import refs as venue_refs

    sent = []
    monkeypatch.setattr(
        venue_refs, "deliver_note",
        lambda sid, note, **kw: sent.append({k: v for k, v in kw.items() if k != "operation"})
        or (True if daemon_has_aliases else _refused_delivery(sid, note, kw)),
    )
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})
    rc = detach.cmd_detach(
        _args(ref_files=[_ref_file(tmp_path)], copilot_args=copilot_args),
        ssh_session=_ssh(seams, stdout=resumed),
    )
    assert rc == (0 if daemon_has_aliases else 1)
    assert len(sent) == 1 and sent[0]["min_daemon_protocol"] == 41
    assert sent[0]["prepared_context"]["version"] == 1
    out = json.loads(capsys.readouterr().out)
    if daemon_has_aliases:
        assert out["refs_delivered"] == "message"
    else:
        assert "not admitted" in out["error"]


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
        _prepared_namespace(ns)
        seams.ssh.append({"ns": ns, "remote": remote_cmd_builder(["/stage/x"]), "settle": settle_on_disconnect})
        return result_sink(outcomes.pop(0))

    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach.cmd_detach(_args(), ssh_session=fake) == 0
    assert len(seams.ssh) == 2
    assert json.loads(capsys.readouterr().out)["session_id"] == "sid-42"


@pytest.mark.parametrize("with_refs", [False, True])
def test_a_lost_create_result_then_a_rejoin_never_reports_the_seed_delivered(
    seams, monkeypatch, capsys, tmp_path, with_refs,
):
    """The first attempt may have created the session (its JSON was lost); the
    retry rejoins it. The seed's fate is unknown: not resent, not claimed --
    nor its reference note, which rode in that same seed."""
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True,
                           "bridge_seed_deferred": True, "bridge_seed_operation": "r1",
                           "session": "wt-anchor-example-web"})
    outcomes = [types.SimpleNamespace(exit_code=255, stdout="", stderr="ssh: connection reset"),
                types.SimpleNamespace(exit_code=0, stdout=rejoined, stderr="")]

    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        _prepared_namespace(ns)
        seams.ssh.append({"ns": ns, "remote": remote_cmd_builder(["/stage/x"]), "settle": settle_on_disconnect})
        return result_sink(outcomes.pop(0))

    from venue_copilot import refs

    delivered = []
    monkeypatch.setattr(refs, "deliver_note", lambda *a, **k: delivered.append((a, k)) or True)
    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    args = _args(ref_files=[_ref_file(tmp_path)]) if with_refs else _args()
    assert detach.cmd_detach(args, ssh_session=fake) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["seeded"] is True and out["seed_delivery"] == "bridge"
    assert len(delivered) == 1 and delivered[0][1]["prepared_context"]["version"] == 1
    if with_refs:
        assert out["refs_delivered"] == "message"


@pytest.mark.parametrize("retry_reports, delivery", [
    ({}, "unconfirmed"),  # no outcome: the lost attempt may have delivered it
    ({"seeded": True}, "typed"),  # a concrete outcome stands
    ({"seed_deferred": True, "seed_reason": "not-ready-timeout"}, "deferred"),
])
def test_a_lost_worktree_launch_result_without_a_host_seed_never_hides_the_pending_seed(
    seams, monkeypatch, capsys, retry_reports, delivery,
):
    """A --worktree-id launch with no host seed whose first result was lost
    may already have delivered the worktree's pending seed: the rejoining
    retry reports it unconfirmed unless it reports a concrete outcome."""
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True,
                           "session": "wt-wt-7", **retry_reports})
    outcomes = [types.SimpleNamespace(exit_code=255, stdout="", stderr="ssh: connection reset"),
                types.SimpleNamespace(exit_code=0, stdout=rejoined, stderr="")]

    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        seams.ssh.append(1)
        return result_sink(outcomes.pop(0))

    from venue_copilot import refs

    monkeypatch.setattr(refs, "deliver_note", lambda *a, **k: pytest.fail("must not resend"))
    assert detach.cmd_detach(_args(worktree_id="wt-7", seed=None), ssh_session=fake) == 1
    out = json.loads(capsys.readouterr().out)
    assert "never typed" in out["error"]


def test_a_flagless_rejoin_on_a_daemon_without_aliases_reports_a_provisional_handle(
    seams, monkeypatch, capsys,
):
    """No resume flag on this call, but the running worker may still be loading
    an earlier --resume: once the launch is a rejoin, a protocol-20 daemon's
    handle is reported provisional."""
    monkeypatch.setattr(venue_copilot, "_daemon_health", lambda port: {"protocol_version": 20})
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True,
                           "session": "wt-anchor-example-web"})
    assert detach.cmd_detach(_args(seed=None), ssh_session=_ssh(seams, stdout=rejoined)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_handle"] == "provisional" and "live-session aliases" in out["handle_warning"]


def test_an_implicit_resume_on_a_daemon_without_aliases_reports_a_provisional_handle(
    seams, monkeypatch, capsys,
):
    """No resume flag from the host, but embody resumed the existing worktree's
    head itself (``resume_session``): a protocol-20 daemon's handle is provisional."""
    monkeypatch.setattr(venue_copilot, "_daemon_health", lambda port: {"protocol_version": 20})
    created = json.dumps({"ok": True, "created": True, "session": "wt-wt-7",
                          "resume_session": "head-1"})
    assert detach.cmd_detach(_args(worktree_id="wt-7", seed=None),
                             ssh_session=_ssh(seams, stdout=created)) == 0
    assert json.loads(capsys.readouterr().out)["session_handle"] == "provisional"


def test_a_rejoin_with_a_host_seed_still_reports_the_pending_seed_outcome(seams, monkeypatch, capsys):
    """A rejoin ignores the host seed but still runs the worktree's own pending
    seed: that attempt's outcome is reported, not hidden."""
    from venue_copilot import refs as venue_refs

    monkeypatch.setattr(venue_refs, "deliver_note", lambda *a, **k: True)
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True, "session": "wt-wt-7",
                           "seed_unconfirmed": True, "seed_reason": "enter-failed"})
    assert detach.cmd_detach(_args(worktree_id="wt-7"), ssh_session=_ssh(seams, stdout=rejoined)) == 1
    assert "never typed" in json.loads(capsys.readouterr().out)["error"]


def test_the_reservation_outlives_every_launch_attempt_and_registration(seams, monkeypatch, capsys):
    ttls: list[float] = []
    monkeypatch.setattr(
        venue_copilot, "reserve_cli_mode",
        lambda scope, ttl_seconds, venue: ttls.append(ttl_seconds) or {"reservation_id": "r1"},
    )
    args = _args()
    assert detach.cmd_detach(args, ssh_session=_ssh(seams, stdout=_CREATED)) == 0
    launch = max(args.register_timeout + 300.0, detach._SEEDED_LAUNCH_TIMEOUT)
    assert detach._SEEDED_LAUNCH_TIMEOUT > detach._LIFECYCLE_LOCK_WAIT + detach._SEED_READY_HARD_CAP
    assert ttls and ttls[0] >= detach._LAUNCH_ATTEMPTS * launch + args.register_timeout


@pytest.mark.parametrize("worktree_id", ["wt-7", None])
def test_a_worktree_launch_without_a_seed_still_budgets_for_its_pending_seed(
    seams, monkeypatch, capsys, worktree_id,
):
    """A --worktree-id launch can consume the remote worktree's pending seed
    with no host-side seed or refs; its readiness wait can reach the hard cap,
    so the SSH budget (and the reservation) get the same floor. An anchor
    launch with nothing to seed keeps the shorter budget."""
    ttls: list[float] = []
    monkeypatch.setattr(
        venue_copilot, "reserve_cli_mode",
        lambda scope, ttl_seconds, venue: ttls.append(ttl_seconds) or {"reservation_id": "r1"},
    )
    args = _args(worktree_id=worktree_id, seed=None)
    assert detach.cmd_detach(args, ssh_session=_ssh(seams, stdout=_CREATED)) == 0
    floor = detach._LAUNCH_ATTEMPTS * detach._SEEDED_LAUNCH_TIMEOUT
    assert (ttls[0] >= floor) is (worktree_id is not None)


@pytest.mark.parametrize("embody_says, delivery, seeded", [
    ({"seed_unconfirmed": True, "seed_reason": "enter-failed"}, "unconfirmed", False),
    ({"seed_deferred": True, "seed_reason": "not-ready-timeout"}, "deferred", False),
    ({"seed_lost": True, "seed_reason": "not-ready-timeout"}, "lost", False),
    ({"seeded": True, "seed_submitted": True}, "typed", True),
])
def test_a_pending_seed_outcome_is_reported_without_a_host_seed(
    seams, monkeypatch, capsys, embody_says, delivery, seeded,
):
    """A --worktree-id launch with no host seed reports how embody fared with
    the worktree's pending seed (submitted, kept for later, or a possible
    draft) -- and never resends it over the bridge."""
    from venue_copilot import refs as venue_refs

    monkeypatch.setattr(venue_refs, "deliver_note", lambda *a, **k: pytest.fail("must not resend"))
    embodied = json.dumps({"ok": True, "created": True, "session": "wt-wt-7", **embody_says})
    rc = detach.cmd_detach(_args(worktree_id="wt-7", seed=None),
                           ssh_session=_ssh(seams, stdout=embodied))
    assert rc == (1 if embody_says.get("seeded") or embody_says.get("seed_unconfirmed") else 0)
    out = json.loads(capsys.readouterr().out)
    if rc:
        assert "never typed" in out["error"]
        return
    assert (out["seed_delivery"], out["seeded"]) == (delivery, seeded)
    if delivery != "typed":
        assert embody_says["seed_reason"] in out["warning"]
    if delivery == "deferred":  # still stored: a manual send would run it twice
        assert "agent-bridge send" not in out["warning"]


def test_launch_does_not_retry_a_genuine_remote_failure(seams, monkeypatch, capsys):
    def fake(ns, *, remote_cmd_builder=None, result_sink=None, settle_on_disconnect=True):
        seams.ssh.append(1)
        return result_sink(types.SimpleNamespace(exit_code=1, stdout='{"ok": false, "error": "boom"}', stderr=""))

    monkeypatch.setattr(detach.time, "sleep", lambda s: None)
    assert detach.cmd_detach(_args(), ssh_session=fake) == 1
    assert len(seams.ssh) == 1

def test_detached_session_records_its_supervising_worktree(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "with_supervisor",
                        lambda venue, ref=None: {**venue, "supervisor_ref": "host/example-harness/wt-1"})
    rc = detach.cmd_detach(_args(dry_run=True), ssh_session=_ssh(seams))
    assert rc == 0
    venue = json.loads(capsys.readouterr().out)["venue"]
    assert venue["kind"] == "codespace" and venue["supervisor_ref"] == "host/example-harness/wt-1"


def test_a_bare_resume_keeps_the_flags_the_session_was_launched_with(seams, capsys):
    # The first launch records its flags; a wake that names only the session
    # (Harness Board's) comes back with them, not the host's defaults.
    first = _args(copilot_args=["--no-ask-user", "--reasoning-effort=max"], driver="orchestrator")
    assert detach.cmd_detach(first, ssh_session=_ssh(seams, stdout=_CREATED)) == 0
    assert "--driver orchestrator" in seams.ssh[-1]["remote"]
    capsys.readouterr()
    wake = _args(copilot_args=["--resume=sid-42"], driver=None, seed=None, dry_run=True)
    assert detach.cmd_detach(wake, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recalled"] == ["copilot_args", "driver"] and out["driver"] == "orchestrator"
    # Still a resume of that session (no new --session-id).
    assert out["copilot_args"] == ["--no-ask-user", "--reasoning-effort=max", "--resume=sid-42"]


def test_a_failed_launch_records_nothing(seams, capsys):
    bad = _args(copilot_args=["--reasoning-effort=max"], driver="orchestrator")
    assert detach.cmd_detach(bad, ssh_session=_ssh(seams, stdout="", code=1)) != 0
    capsys.readouterr()
    dry = _args(copilot_args=["--resume=sid-42"], driver=None, seed=None, dry_run=True)  # a bare wake
    assert detach.cmd_detach(dry, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert "recalled" not in out and out["copilot_args"] == ["--resume=sid-42"]


def test_a_recalled_session_gains_no_host_model_it_did_not_run_with(seams, monkeypatch, capsys):
    from agent_codespaces import launch_memory

    # Launched while model propagation was off: its record has no model flags.
    launch_memory.remember("cs-1", "cli:anchor-example-web@cs-1", [], "cli-mode", "sid-42")
    monkeypatch.setattr(detach, "model_copilot_args",
                        lambda existing: [] if any(a.startswith("--model") for a in existing)
                        else ["--model=host-today", "--reasoning-effort=high"])
    wake = _args(copilot_args=["--resume=sid-42"], driver=None, seed=None, dry_run=True)
    assert detach.cmd_detach(wake, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["recalled"] == ["copilot_args"] and out["copilot_args"] == ["--resume=sid-42"]
    # A resume of a session with no record still gets the host's model, as before.
    other = _args(copilot_args=["--resume=sid-99"], driver=None, seed=None, dry_run=True)
    assert detach.cmd_detach(other, ssh_session=_ssh(seams)) == 0
    assert json.loads(capsys.readouterr().out)["copilot_args"] == [
        "--resume=sid-99", "--model=host-today", "--reasoning-effort=high"]


def test_a_bare_resume_gets_the_sessions_forward_ports_back(seams, monkeypatch, capsys):
    # The Owner releases a stopped CodeSpace's forwards with its session, so a
    # wake that names only the session re-adds the --forward ports it ran with
    # (never its reverse forwards: their host end can move).
    monkeypatch.setattr(detach, "_host_ports_listening", lambda ports: {p: True for p in ports})
    first = _args(local_forwards=["4322", "0:4397"], reverse_forwards=["9222:24836"])
    assert detach.cmd_detach(first, ssh_session=_ssh(seams, stdout=_CREATED)) == 0
    capsys.readouterr()
    wake = _args(copilot_args=["--resume=sid-42"], driver=None, seed=None, dry_run=True)
    assert detach.cmd_detach(wake, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"4322": 4322, "0": 4397} and out["reverse_forwards"] == {}
    assert "local_forwards" in out["recalled"]
    # Explicit --forward replaces them; another model keeps them (ports are independent of flags).
    explicit = _args(copilot_args=["--resume=sid-42"], local_forwards=["5000"], driver=None, seed=None,
                     dry_run=True)
    assert detach.cmd_detach(explicit, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"5000": 5000} and "local_forwards" not in out.get("recalled", [])
    remodel = _args(copilot_args=["--resume=sid-42", "--model=m2"], driver=None, seed=None, dry_run=True)
    assert detach.cmd_detach(remodel, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["local_forwards"] == {"4322": 4322, "0": 4397} and out["recalled"] == ["local_forwards"]


def test_a_rejoin_that_sets_forwards_records_them_and_keeps_the_flags(seams, monkeypatch, capsys):
    from agent_codespaces import launch_memory

    monkeypatch.setattr(detach, "_host_ports_listening", lambda ports: {p: True for p in ports})
    tenant = "cli:anchor-example-web@cs-1"
    launch_memory.remember("cs-1", tenant, ["--no-ask-user"], "orchestrator", "sid-42", local_forwards=["4322:4322"])
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True})
    rejoin = _args(copilot_args=["--model=other"], local_forwards=["4331"], driver=None, seed=None)
    assert detach.cmd_detach(rejoin, ssh_session=_ssh(seams, stdout=rejoined)) == 0
    record = json.loads(launch_memory._path("cs-1", tenant).read_text())
    assert record["local_forwards"] == ["4331:4331"] and record["copilot_args"] == ["--no-ask-user"]

def test_a_rejoin_of_a_running_session_leaves_the_record_alone(seams, capsys):
    from agent_codespaces import launch_memory

    tenant = "cli:anchor-example-web@cs-1"
    launch_memory.remember("cs-1", tenant, ["--no-ask-user", "--reasoning-effort=max"], "orchestrator", "sid-42")
    before = launch_memory._path("cs-1", tenant).read_text()
    # A rejoin (say, to add a forward) of the running session: embody applies none of its flags.
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True})
    rejoin = _args(copilot_args=["--model=other"], driver=None, seed=None)
    assert detach.cmd_detach(rejoin, ssh_session=_ssh(seams, stdout=rejoined)) == 0
    assert launch_memory._path("cs-1", tenant).read_text() == before


def test_a_bare_rejoin_of_a_running_session_reports_nothing_recalled(seams, capsys):
    from agent_codespaces import launch_memory

    launch_memory.remember("cs-1", "cli:anchor-example-web@cs-1", ["--no-ask-user"], "orchestrator", "sid-42")
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True})
    bare = _args(copilot_args=["--resume=sid-42"], driver=None, seed=None)
    assert detach.cmd_detach(bare, ssh_session=_ssh(seams, stdout=rejoined)) == 0
    assert "recalled" not in json.loads(capsys.readouterr().out)  # the running session applied none of it


def test_the_record_keeps_the_model_the_session_actually_ran_with(seams, monkeypatch, capsys):
    # The launch filled the model from this host's settings; a later wake keeps
    # that model even after the host's own setting changed.
    host = ["first-model"]
    monkeypatch.setattr(detach, "model_copilot_args",
                        lambda existing: [] if any(a.startswith("--model") for a in existing)
                        else [f"--model={host[0]}"])
    first = _args(copilot_args=["--no-ask-user"], driver="orchestrator")
    assert detach.cmd_detach(first, ssh_session=_ssh(seams, stdout=_CREATED)) == 0
    host[0] = "changed-model"
    capsys.readouterr()
    wake = _args(copilot_args=["--resume=sid-42"], driver=None, seed=None, dry_run=True)
    assert detach.cmd_detach(wake, ssh_session=_ssh(seams)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["copilot_args"] == ["--no-ask-user", "--model=first-model", "--resume=sid-42"]


@pytest.mark.parametrize("status, asks_policy", [("live", False), (None, True)])
def test_only_a_launch_asks_the_launch_policy_not_a_rejoin(seams, monkeypatch, capsys, status, asks_policy):
    """A rejoin of a running worker launches nothing, so a refusing policy (a
    full core budget, say) never blocks reattaching to it."""
    seen = []
    monkeypatch.setattr(copilot_venue, "claim_or_exit_code", lambda a, **k: seen.append(k) or None)
    seams.live_rows["anchor-example-web@cs-1"]["status"] = status
    rejoined = json.dumps({"ok": True, "created": False, "resumed": True})
    assert detach.cmd_detach(_args(seed=None), ssh_session=_ssh(seams, stdout=rejoined)) == 0
    assert seen == [{"launch_policy": asks_policy}]


def test_a_rejoin_that_created_a_session_is_still_refused_and_stopped(seams, monkeypatch, capsys):
    """The worker stopped between the check and embody: the rejoin started a
    session after all, so the policy decides then, and a refused one is stopped."""
    from agent_codespaces import launch_policy

    monkeypatch.setattr(launch_policy, "refusal", lambda cs: "over the core budget")
    seams.live_rows["anchor-example-web@cs-1"]["status"] = "live"
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=_CREATED))
    assert rc == launch_policy.LAUNCH_REFUSED_EXIT
    assert any("kill-session" in c for c in seams.remote)
    assert "over the core budget" in capsys.readouterr().err


def test_a_refused_rejoin_stops_the_session_the_venue_actually_named(seams, monkeypatch):
    """The venue named the session it started differently than predicted: the
    refusal's cleanup must stop that session, not the predicted name."""
    from agent_codespaces import launch_policy

    monkeypatch.setattr(launch_policy, "refusal", lambda cs: "over the core budget")
    seams.live_rows["anchor-example-web@cs-1"]["status"] = "live"
    renamed = json.dumps({"ok": True, "created": True, "session": "wt-anchor-renamed"})
    rc = detach.cmd_detach(_args(), ssh_session=_ssh(seams, stdout=renamed))
    assert rc == launch_policy.LAUNCH_REFUSED_EXIT
    kills = [c for c in seams.remote if "kill-session" in c]
    assert kills and all("=wt-anchor-renamed" in c for c in kills)
