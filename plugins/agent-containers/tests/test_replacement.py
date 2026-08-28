"""Restricted replacement safety tests with synthetic Docker/session state."""

from __future__ import annotations

import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from agent_containers import replacement
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.lifecycle import DockerContainerInfo
from agent_containers.rescue import RescueError


def _member(name: str = "sandbox-1") -> DockerContainerInfo:
    return DockerContainerInfo(
        name=name,
        container_id=f"{name}-instance",
        image="example/agent",
        state="running",
        status="Up",
        fleet="sandbox",
        security_profile="restricted",
    )


def _config() -> tuple[ContainersConfig, FleetConfig]:
    fleet = FleetConfig(
        image="example/agent",
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
        exec_user="agent",
    )
    return ContainersConfig(fleets={"sandbox": fleet}), fleet


def _safe_defaults(monkeypatch, info: DockerContainerInfo) -> None:
    monkeypatch.setattr(
        replacement,
        "deploy_hold",
        lambda *_args, **_kwargs: nullcontext(
            SimpleNamespace(
                token="hold-token",  # noqa: S106
                expires_at=time.time() + 1000,
            )
        ),
    )
    monkeypatch.setattr(replacement, "get_lease", lambda _name: None)
    monkeypatch.setattr(
        replacement,
        "active_session_admissions",
        lambda _name: [],
    )
    monkeypatch.setattr(
        replacement,
        "get_container",
        lambda _config, _name: info,
    )
    monkeypatch.setattr(replacement, "inspect_state", lambda _name: None)
    monkeypatch.setattr(
        replacement,
        "inspect_container",
        lambda name: {
            "Id": name,
            "State": {"StartedAt": "2026-01-01T00:00:00Z"},
            "Config": {
                "Labels": {
                    "agent-containers.security-home": "/home/agent",
                }
            },
            "HostConfig": {
                "Tmpfs": {
                    "/home/agent": "",
                    "/workspace": "",
                    "/tmp": "",  # noqa: S108
                    "/run": "",
                }
            },
            "Mounts": [],
        },
    )
    monkeypatch.setattr(
        replacement,
        "resolve_executable",
        lambda *_args, **_kwargs: ("/bin/bash", "/home/agent"),
    )
    monkeypatch.setattr(
        replacement,
        "restricted_policy_errors",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        replacement,
        "verify_deploy_hold",
        lambda _name, _token: None,
    )
    monkeypatch.setattr(
        replacement,
        "mark_deploy_hold_uncertain",
        lambda _name, _token: None,
    )
    monkeypatch.setattr(
        replacement,
        "record_telemetry_loss",
        lambda **_kwargs: {"status": "abandoned"},
    )
    monkeypatch.setattr(
        replacement,
        "verified_capture_for_instance",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        replacement,
        "_rescue_pin_context",
        lambda *_args, **_kwargs: nullcontext(SimpleNamespace()),
    )
    monkeypatch.setattr(
        replacement,
        "verify_pinned_capture",
        lambda _pin: None,
    )


def test_live_copilot_session_blocks_recreate(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness(
            "active",
            ["11111111-1111-4111-8111-111111111111"],
            [],
        ),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active session must not be rescued for destruction")
        ),
    )
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active session must not be removed")
        ),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="recreate",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "deferred"
    assert "active Copilot" in result.reason


def test_unknown_session_probe_blocks_removal(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness(
            "unknown",
            [],
            [],
            "docker exec failed",
        ),
    )
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unknown liveness must not be removed")
        ),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=True,
    )

    assert result.status == "deferred"
    assert "unknown" in result.reason


def test_lease_is_supplementary_block_after_idle_probe(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness(
            "idle",
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        replacement,
        "get_lease",
        lambda _name: SimpleNamespace(effort="example-effort"),
    )
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("leased member must not be removed")
        ),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "deferred"
    assert result.reason == "container has an active effort lease"


def test_idle_session_rescues_before_remove(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    calls = []
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness(
            "idle",
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: calls.append("rescue")
        or {"status": "verified", "session_count": 1},
    )
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: calls.append("remove"),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="recreate",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "removed"
    assert result.rescue["status"] == "verified"
    assert calls == ["rescue", "remove"]


def test_rescue_failure_leaves_old_container(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness(
            "idle",
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RescueError("stream interrupted")
        ),
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "deferred"
    assert "rescue failed" in result.reason
    assert removed == []


def test_force_abandon_accepts_only_rescue_loss(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness(
            "idle",
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RescueError("stream interrupted")
        ),
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=True,
    )

    assert result.status == "removed"
    assert result.telemetry_abandoned is True
    assert removed == [True]


def test_noncooperative_probe_reads_live_and_stale_markers(monkeypatch):
    info = _member()
    session_live = "11111111-1111-4111-8111-111111111111"
    session_stale = "22222222-2222-4222-8222-222222222222"
    calls = []

    def fake_docker(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "ROOT\tpresent\n"
                f"LOCK\t{session_live}\t123\tlive\n"
                f"LOCK\t{session_stale}\t456\tstale\n"
                "PROCESS_SCAN\tok\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(replacement, "_docker", fake_docker)

    result = replacement.probe_session_liveness(
        info, user="agent", bash_path="/bin/bash", home="/home/agent"
    )

    assert result.state == "active"
    assert result.active_sessions == [session_live]
    assert result.stale_sessions == [session_stale]
    script = calls[0][-1]
    assert "/proc/$pid" in script
    assert "kill -0" not in script
    assert "/bin/bash" in calls[0]
    assert "--noprofile" in calls[0]
    assert "--norc" in calls[0]
    assert "LD_PRELOAD=" in calls[0]
    assert "BASH_ENV=" in calls[0]


def test_noncooperative_probe_failure_is_unknown(monkeypatch):
    info = _member()
    monkeypatch.setattr(
        replacement,
        "_docker",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="container unavailable",
        ),
    )

    result = replacement.probe_session_liveness(
        info, user="agent", bash_path="/bin/bash", home="/home/agent"
    )

    assert result.state == "unknown"
    assert result.reason == "container unavailable"


def test_liveness_timeout_is_unknown_and_deferred(monkeypatch):
    info = _member()
    monkeypatch.setattr(
        replacement,
        "_docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("docker exec timed out after 30s")
        ),
    )

    result = replacement.probe_session_liveness(
        info,
        user="agent",
        bash_path="/bin/bash",
        home="/home/agent",
    )

    assert result.state == "unknown"
    assert "timed out" in result.reason


def test_missing_session_root_is_distinct_and_process_backstop_is_idle(
    monkeypatch,
):
    info = _member()
    monkeypatch.setattr(
        replacement,
        "_docker",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="ROOT\tabsent\nPROCESS_SCAN\tok\n",
            stderr="",
        ),
    )

    result = replacement.probe_session_liveness(
        info, user="agent", bash_path="/bin/bash", home="/home/agent"
    )

    assert result.state == "idle"
    assert result.session_state == "absent"


def test_process_without_matching_marker_makes_liveness_unknown(monkeypatch):
    info = _member()
    monkeypatch.setattr(
        replacement,
        "_docker",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="ROOT\tpresent\nPROCESS\t77\nPROCESS_SCAN\tok\n",
            stderr="",
        ),
    )

    result = replacement.probe_session_liveness(
        info, user="agent", bash_path="/bin/bash", home="/home/agent"
    )

    assert result.state == "unknown"
    assert "no matching live session marker" in result.reason


def test_incomplete_process_backstop_defers_instead_of_assuming_idle(monkeypatch):
    info = _member()
    monkeypatch.setattr(
        replacement,
        "_docker",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="ROOT\tpresent\nPROCESS_SCAN\tpartial\n",
            stderr="",
        ),
    )

    result = replacement.probe_session_liveness(
        info, user="agent", bash_path="/bin/bash", home="/home/agent"
    )

    assert result.state == "unknown"
    assert result.reason == "process backstop was incomplete"


def test_paused_probe_is_unknown_without_docker_exec(monkeypatch):
    info = _member()
    info.state = "paused"
    monkeypatch.setattr(
        replacement,
        "_docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("paused member must be unpaused by lifecycle first")
        ),
    )

    result = replacement.probe_session_liveness(
        info, user="agent", bash_path="/bin/bash", home="/home/agent"
    )

    assert result.state == "unknown"
    assert result.reason == "container is paused"


def test_paused_member_unpauses_before_probe_and_rescue(monkeypatch):
    config, fleet = _config()
    paused = _member()
    paused.state = "paused"
    running = _member()
    states = iter([paused, running, running, running])
    _safe_defaults(monkeypatch, paused)
    monkeypatch.setattr(
        replacement,
        "get_container",
        lambda *_args: next(states),
    )
    calls = []
    monkeypatch.setattr(
        replacement,
        "unpause_container",
        lambda name: calls.append(("unpause", name)),
    )
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: (
            calls.append(("probe", None))
            or replacement.SessionLiveness("idle", [], [])
        ),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: calls.append(("rescue", None))
        or {"status": "verified"},
    )
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: calls.append(("remove", None)),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        paused,
        operation="recreate",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "removed"
    assert [name for name, _value in calls] == [
        "unpause",
        "probe",
        "rescue",
        "probe",
        "remove",
    ]


def test_paused_member_unpause_failure_defers_even_with_force(monkeypatch):
    config, fleet = _config()
    paused = _member()
    paused.state = "paused"
    _safe_defaults(monkeypatch, paused)
    monkeypatch.setattr(
        replacement,
        "unpause_container",
        lambda _name: (_ for _ in ()).throw(RuntimeError("unpause failed")),
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        paused,
        operation="remove",
        force_remove=True,
        force_abandon=True,
    )

    assert result.status == "deferred"
    assert "could not be inspected" in result.reason
    assert removed == []


def test_stopped_member_requires_explicit_telemetry_abandon(monkeypatch):
    config, fleet = _config()
    stopped = _member()
    stopped.state = "exited"
    _safe_defaults(monkeypatch, stopped)
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    deferred = replacement.destroy_restricted_member(
        config,
        fleet,
        stopped,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )
    accepted = replacement.destroy_restricted_member(
        config,
        fleet,
        stopped,
        operation="remove",
        force_remove=True,
        force_abandon=True,
    )

    assert deferred.status == "deferred"
    assert "tmpfs evidence is unavailable" in deferred.reason
    assert accepted.status == "removed"
    assert accepted.telemetry_abandoned is True
    assert removed == [True]


def test_stopped_member_reuses_verified_rescue_from_prior_down(monkeypatch):
    config, fleet = _config()
    stopped = _member()
    stopped.state = "exited"
    _safe_defaults(monkeypatch, stopped)
    verified = {
        "status": "verified",
        "capture_id": "capture-a",
        "container_instance": stopped.container_id,
    }
    monkeypatch.setattr(
        replacement,
        "verified_capture_for_instance",
        lambda *_args: verified,
    )
    monkeypatch.setattr(
        replacement,
        "record_telemetry_loss",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("verified prior rescue must not be abandoned")
        ),
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        stopped,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "removed"
    assert result.rescue == verified
    assert result.telemetry_abandoned is False
    assert removed == [True]


def test_stopped_restart_same_id_rejects_prior_run_capture(monkeypatch):
    config, fleet = _config()
    stopped = _member()
    stopped.state = "exited"
    _safe_defaults(monkeypatch, stopped)
    inspections = iter(
        [
            {
                "Id": stopped.container_id,
                "State": {"StartedAt": "run-b"},
                "Config": {
                    "Labels": {
                        "agent-containers.security-home": "/home/agent",
                    }
                },
                "HostConfig": {"Tmpfs": {}},
                "Mounts": [],
            },
            {
                "Id": stopped.container_id,
                "State": {"StartedAt": "run-b"},
            },
        ]
    )
    monkeypatch.setattr(
        replacement,
        "inspect_container",
        lambda _name: next(inspections),
    )
    seen_generations = []

    def prior_capture(_name, _container_id, generation):
        seen_generations.append(generation)
        return None

    monkeypatch.setattr(
        replacement,
        "verified_capture_for_instance",
        prior_capture,
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        stopped,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert seen_generations == ["run-b"]
    assert result.status == "deferred"
    assert "explicit telemetry abandonment" in result.reason
    assert removed == []


@pytest.mark.parametrize("state", ["restarting", "removing", "unknown", "dead"])
def test_nonterminal_nonrunning_state_always_defers(monkeypatch, state):
    config, fleet = _config()
    member = _member()
    member.state = state
    _safe_defaults(monkeypatch, member)
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        member,
        operation="remove",
        force_remove=True,
        force_abandon=True,
    )

    assert result.status == "deferred"
    assert "transitional or unknown" in result.reason
    assert removed == []


def test_unsafe_restricted_policy_blocks_destructive_lifecycle(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "restricted_policy_errors",
        lambda *_args, **_kwargs: [
            "security policy fingerprint is stale",
            "host bind mounts are present",
        ],
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="recreate",
        force_remove=True,
        force_abandon=True,
    )

    assert result.status == "deferred"
    assert "host bind mounts are present" in result.reason
    assert "security policy fingerprint is stale" not in result.reason
    assert removed == []


def test_second_liveness_probe_blocks_new_activity_before_remove(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    probes = iter(
        [
            replacement.SessionLiveness("idle", [], []),
            replacement.SessionLiveness(
                "active",
                ["11111111-1111-4111-8111-111111111111"],
                [],
            ),
        ]
    )
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: next(probes),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: {"status": "verified"},
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "deferred"
    assert "active Copilot" in result.reason
    assert removed == []


def test_generation_change_before_action_defers_same_container_id(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    inspections = iter(
        [
            {
                "Id": info.container_id,
                "State": {"StartedAt": "run-a"},
                "Config": {
                    "Labels": {
                        "agent-containers.security-home": "/home/agent",
                    }
                },
                "HostConfig": {"Tmpfs": {}},
                "Mounts": [],
            },
            {
                "Id": info.container_id,
                "State": {"StartedAt": "run-b"},
            },
        ]
    )
    monkeypatch.setattr(
        replacement,
        "inspect_container",
        lambda _name: next(inspections),
    )
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness("idle", [], []),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: {
            "status": "verified",
            "capture_id": "capture-a",
            "container_generation": "run-a",
        },
    )
    removed = []
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: removed.append(True),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "deferred"
    assert "generation changed" in result.reason
    assert removed == []


def test_hold_ownership_is_verified_immediately_before_remove(monkeypatch):
    config, fleet = _config()
    info = _member()
    _safe_defaults(monkeypatch, info)
    monkeypatch.setattr(
        replacement,
        "probe_session_liveness",
        lambda *_args, **_kwargs: replacement.SessionLiveness("idle", [], []),
    )
    monkeypatch.setattr(
        replacement,
        "capture_restricted_sessions",
        lambda *_args, **_kwargs: {"status": "verified"},
    )
    calls = []
    monkeypatch.setattr(
        replacement,
        "verify_deploy_hold",
        lambda _name, token: calls.append(("verify", token)),
    )
    monkeypatch.setattr(
        replacement,
        "remove_container",
        lambda *_args, **_kwargs: calls.append(("remove", None)),
    )

    result = replacement.destroy_restricted_member(
        config,
        fleet,
        info,
        operation="remove",
        force_remove=True,
        force_abandon=False,
    )

    assert result.status == "removed"
    assert calls[-4:] == [
        ("verify", "hold-token"),
        ("remove", None),
        ("verify", "hold-token"),
        ("verify", "hold-token"),
    ]


def test_action_is_bounded_and_confirmed_before_hold_release(monkeypatch):
    info = _member()
    calls = []
    monkeypatch.setattr(
        replacement,
        "verify_deploy_hold",
        lambda _name, _token: calls.append("verify"),
    )

    replacement._perform_action(
        info.name,
        "hold-token",
        time.time() + 60,
        info,
        action=lambda _current, timeout: calls.append(("action", timeout)),
        confirm=lambda _current: calls.append("confirm") or True,
        action_timeout=120,
    )

    assert calls[0] == "verify"
    assert calls[1][0] == "action"
    assert 0 < calls[1][1] <= 55
    assert calls[2:] == ["verify", "confirm", "verify"]


def test_action_refuses_when_hold_has_no_reserved_time(monkeypatch):
    info = _member()
    monkeypatch.setattr(
        replacement,
        "verify_deploy_hold",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("expired hold must not reach action")
        ),
    )

    with pytest.raises(replacement.ProviderAdmissionError, match="no action time"):
        replacement._perform_action(
            info.name,
            "hold-token",
            time.time() + 1,
            info,
            action=lambda *_args: None,
            confirm=lambda _current: True,
            action_timeout=120,
        )


def test_unconfirmed_action_marks_hold_uncertain(monkeypatch):
    info = _member()
    marked = []
    monkeypatch.setattr(replacement, "verify_deploy_hold", lambda *_args: None)
    monkeypatch.setattr(
        replacement,
        "mark_deploy_hold_uncertain",
        lambda name, token: marked.append((name, token)),
    )

    with pytest.raises(RuntimeError, match="docker action failed"):
        replacement._perform_action(
            info.name,
            "hold-token",
            time.time() + 120,
            info,
            action=lambda *_args: (_ for _ in ()).throw(
                RuntimeError("docker action failed")
            ),
            confirm=lambda _current: True,
            action_timeout=60,
        )

    assert marked == [(info.name, "hold-token")]
