"""Tests for the singleton supervisor daemon (registered-supervision runtime).

Covered:

* **command building** -- a supervised-lane registration reconstructs the
  ``agent-dispatch supervise`` argv; an unsupported kind raises;
* **reconcile** -- start on register, stop on remove/pause, restart on spec
  change, backoff-gated + cap-bounded revive of a crashed unit, skip of an
  unsupported kind; and
* **serve** -- the single-instance election stands a second daemon down
  (pin-not-failover), and shutdown winds every unit down.

Everything the daemon touches outside itself -- the launcher, the clock, the
sleep, the coordinator client -- is injected as a fake, so no real process or
server is started.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from agent_dispatch.companion import CompanionIndeterminate
from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_reconcile import declaration_to_registration
from agent_dispatch.repository_issue_loops import expand_repository_issue_loop
from agent_dispatch.supervisor_daemon import (
    SupervisorDaemon,
    UnsupportedKind,
    _spec_fingerprint,
    build_command,
    supervisor_lease_scope,
)
from tests._helpers import TEST_REPO

# -- fakes -------------------------------------------------------------------


class FakeProc:
    def __init__(self):
        self._returncode: int | None = None
        self.terminated = False

    def crash(self, code: int = 1) -> None:
        self._returncode = code

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        if self._returncode is None:
            self._returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self._returncode or 0


class FakeLauncher:
    def __init__(self):
        self.launched: list[tuple[str, FakeProc]] = []
        self.commands: list[tuple[str, list[str]]] = []

    def launch(self, reg: dict, cmd: list[str]) -> FakeProc:
        proc = FakeProc()
        self.launched.append((reg["id"], proc))
        self.commands.append((reg["id"], cmd))
        return proc

    def proc_for(self, rid: str) -> FakeProc:
        return [p for (r, p) in self.launched if r == rid][-1]


class FakeCompanionController:
    def __init__(self):
        self.resolve_outcomes: list[object] = []
        self.health_outcomes: list[object] = []
        self.launched: list[tuple[str, FakeProc]] = []
        self.stopped: list[str] = []
        self.stopped_versions: list[str] = []
        self.retired: list[str] = []
        self.adopted_receipts: list[set[str]] = []
        self.recover_next = False

    def resolve(self, registration, *, machine, env):
        outcome = object()
        if self.resolve_outcomes:
            outcome = self.resolve_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            if outcome is None:
                return None
        resolved = dict(registration)
        resolved["companion_runtime"] = (
            outcome
            if isinstance(outcome, dict)
            else {"arguments": [], "environment": {}, "environment_digest": "same"}
        )
        return SimpleNamespace(registration=resolved)

    def launch(self, resolution, *, fingerprint):
        proc = FakeProc()
        rid = resolution.registration["id"]
        self.launched.append((rid, proc))
        recovered, self.recover_next = self.recover_next, False
        return SimpleNamespace(process=proc, recovered=recovered)

    def stop(self, resolution, process):
        self.stopped.append(resolution.registration["id"])
        self.stopped_versions.append(
            resolution.registration["plugin"]["version"]
        )
        process.terminate()

    def health(self, resolution):
        if not self.health_outcomes:
            return None
        outcome = self.health_outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def retire_crashed(self, resolution, process):
        self.retired.append(resolution.registration["id"])
        process.terminate()

    def reconcile_receipts(self, adopted_registration_ids):
        self.adopted_receipts.append(set(adopted_registration_ids))

    def proc_for(self, rid: str) -> FakeProc:
        return [p for (r, p) in self.launched if r == rid][-1]


class FakeClient:
    def __init__(self, regs: list[dict]):
        self._regs = regs
        self.lease_holder: str | None = None
        self.released: list[str] = []

    def set_regs(self, regs: list[dict]) -> None:
        self._regs = regs

    def list_registrations(self, *, machine=None, env=None, include_paused=True):
        out = []
        for r in self._regs:
            if machine is not None and r.get("machine") != machine:
                continue
            if env is not None and r.get("env", "default") != env:
                continue
            if not include_paused and r.get("status") == "paused":
                continue
            out.append(r)
        return out

    def acquire_schedule_lease(self, scope, holder, **kw):
        if self.lease_holder is None:
            self.lease_holder = holder
            return {"granted": True, "lease": {"holder": holder}}
        return {"granted": self.lease_holder == holder,
                "lease": {"holder": self.lease_holder}}

    def release_schedule_lease(self, scope, holder, **kw):
        self.released.append(scope)
        if self.lease_holder == holder:
            self.lease_holder = None
        return {"released": True}

    def get_schedule_lease(self, scope):
        return {"holder": self.lease_holder} if self.lease_holder else None


class Clock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeLock:
    def __init__(self, granted: bool = True):
        self._granted = granted
        self.acquired = False
        self.released = False

    def acquire(self) -> bool:
        if self._granted:
            self.acquired = True
        return self._granted

    def release(self) -> None:
        self.released = True


def _reg(rid, **over) -> dict:
    r = {
        "id": rid,
        "kind": "supervised-lane",
        "spec": {"repo": TEST_REPO, "max_concurrent": 1, "max_attempts": 3},
        "machine": "anomalous-potato",
        "env": "default",
        "status": "active",
    }
    r.update(over)
    return r


def _companion_reg(rid="companion", *, root="C:\\plugins\\index", version="1"):
    return _reg(
        rid,
        kind="plugin-companion",
        spec={"command": ["bin/service.py"], "config_provider": ["bin/config.py"]},
        plugin={
            "root": root,
            "source_path": root + "\\.github\\plugin\\plugin.json",
            "version": version,
            "activation_scopes": ["repo"],
        },
        runtime_revision={"plugin_root": root, "plugin_version": version},
    )


def _daemon(client, launcher, **kw):
    clock = kw.pop("clock", Clock())
    return SupervisorDaemon(
        client, "anomalous-potato", "default",
        launcher=launcher, sleep=lambda _s: None, clock=clock, **kw,
    )


# -- command building --------------------------------------------------------


def test_build_command_supervised_lane():
    reg = _reg("a", spec={
        "repo": TEST_REPO, "labels": ["x", "y"], "max_concurrent": 2,
        "max_attempts": 5, "headless_labels": ["y"], "headless_agent": "task-worker",
        "interval": 15.0,
    })
    cmd = build_command(reg, python="PY")
    assert cmd[:4] == ["PY", "-m", "agent_dispatch", "supervise"]
    assert "--repo" in cmd and TEST_REPO in cmd
    assert cmd.count("--label") == 2
    assert "--max-concurrent" in cmd and "2" in cmd
    assert "--headless-label" in cmd and "--headless-agent" in cmd
    # a supervised-lane does NOT run an evaluator -- that is the 'evaluator' kind
    assert "--evaluator" not in cmd


def test_build_command_threads_embody_backend_and_cli_labels():
    """A spec pinning the CLI backend + a cli-label opt-out round-trips into the
    supervise argv (headless is the default, so it is emitted only when pinned)."""
    reg = _reg("b", spec={
        "all_repos": True, "labels": ["x", "y"], "max_concurrent": 1,
        "embody_backend": "cli", "cli_labels": ["x"], "headless_agent": "task-worker",
        "interval": 30.0,
    })
    cmd = build_command(reg, python="PY")
    assert cmd[cmd.index("--embody-backend") + 1] == "cli"
    assert cmd[cmd.index("--cli-label") + 1] == "x"


def test_build_command_threads_disposable_cli_labels():
    reg = _reg("b", spec={
        "all_repos": True,
        "labels": ["review"],
        "max_concurrent": 1,
        "embody_backend": "cli",
        "disposable_cli_labels": ["review"],
        "interval": 30.0,
    })
    cmd = build_command(reg, python="PY")
    assert cmd[cmd.index("--disposable-cli-label") + 1] == "review"


def test_build_command_headless_default_emits_no_backend_flag():
    """A default (headless) lane spec carries no embody_backend/cli_labels, so the
    argv omits those flags -- headless is the default the command already assumes."""
    reg = _reg("c", spec={
        "all_repos": True, "labels": ["x"], "max_concurrent": 1,
        "headless_agent": "task-worker", "interval": 30.0,
    })
    cmd = build_command(reg, python="PY")
    assert "--embody-backend" not in cmd
    assert "--cli-label" not in cmd
    assert "--headless-label" not in cmd
    cmd = build_command(_reg("a", spec={"all_repos": True}), python="PY")
    assert "--all-repos" in cmd
    assert "--repo" not in cmd


def test_build_command_evaluator_inline_spec():
    reg = _reg("e", kind="evaluator", spec={
        "evaluator_spec": {"states": {}}, "all_repos": True, "labels": ["code-review"],
        "evaluator_ref": "review-loop",
    })
    materialized = {}

    def mat(name, payload):
        materialized[name] = payload
        return f"/run/{name}.json"

    cmd = build_command(reg, python="PY", materialize=mat)
    assert cmd[:4] == ["PY", "-m", "agent_dispatch", "supervise"]
    assert "--evaluator" in cmd and "/run/evaluator.json" in cmd
    assert "--all-repos" in cmd
    assert cmd[cmd.index("--evaluator-ref") + 1] == "review-loop"
    assert materialized["evaluator"] == {"states": {}}


def test_build_command_evaluator_path_ref():
    reg = _reg("e", kind="evaluator", spec={"evaluator": "eval.json", "repo": TEST_REPO})
    cmd = build_command(reg, python="PY")  # no materializer needed for a path ref
    assert "--evaluator" in cmd and "eval.json" in cmd


def test_build_command_schedule():
    reg = _reg("s", kind="schedule",
               spec={"id": "nightly", "repo": TEST_REPO, "interval_seconds": 3600})
    captured = {}

    def mat(name, payload):
        captured[name] = payload
        return f"/run/{name}.json"

    cmd = build_command(reg, python="PY", materialize=mat)
    assert cmd[:5] == ["PY", "-m", "agent_dispatch", "schedule", "serve"]
    assert "/run/schedule.json" in cmd
    # wrapped as a one-entry spec the timer producer consumes
    assert captured["schedule"] == {"schedules": [reg["spec"]]}


def test_build_command_emitter():
    reg = _reg("m", kind="emitter", spec={"url": "http://x", "port": 9400})
    cmd = build_command(reg, python="PY", materialize=lambda n, p: f"/run/{n}.json")
    assert cmd[:4] == ["PY", "-m", "agent_dispatch", "webhook"]
    assert "--config" in cmd and "/run/emitter.json" in cmd
    assert "--port" in cmd and "9400" in cmd


def test_build_command_periodic_emitter():
    reg = _reg(
        "m",
        kind="emitter",
        machine="anomalous-potato",
        spec={
            "id": "review-inbox",
            "command": ["review-emitter", "tick"],
            "interval_seconds": 3600,
        },
    )
    cmd = build_command(
        reg, python="PY", materialize=lambda n, p: f"/run/{n}.json"
    )
    assert cmd[:5] == ["PY", "-m", "agent_dispatch", "emitter", "serve"]
    assert "/run/emitter.json" in cmd
    assert cmd[cmd.index("--holder") + 1] == "anomalous-potato"


def test_repository_issue_loop_expansion_builds_periodic_emitter_command():
    source, _workers = expand_repository_issue_loop(
        {
            "name": "backlog",
            "kind": "repository-issue-loop",
            "repo": "example/project",
            "source": "repository-backlog",
            "cadence_seconds": 3600,
            "task_label": "repository-issue-work",
            "forge": {
                "provider": "github",
                "producer_login": "issue-bot",
            },
            "reservation": {"label": "agent-reserved"},
            "pool": {"body": {"type": "headless", "agent": "issue-worker"}},
        }
    )
    registration = declaration_to_registration(
        source, machine="host-a", env="default"
    )
    captured = {}

    def materialize(name, payload):
        captured[name] = payload
        return f"/run/{name}.json"

    command = build_command(
        registration, python="PY", materialize=materialize
    )

    assert command == [
        "PY",
        "-m",
        "agent_dispatch",
        "emitter",
        "serve",
        "/run/emitter.json",
        "--holder",
        "host-a",
    ]
    assert captured["emitter"]["repository_issue_loop"]["name"] == "backlog"


def test_build_command_needs_materializer_for_inline_spec():
    reg = _reg("s", kind="schedule", spec={"id": "n", "repo": TEST_REPO})
    with pytest.raises(UnsupportedKind):
        build_command(reg, python="PY")  # no materializer -> refused


def test_build_command_rejects_unsupported_kind():
    with pytest.raises(UnsupportedKind):
        build_command(_reg("a", kind="totally-unknown", spec={"x": 1}))


def test_build_command_does_not_launch_plugin_companion_contract():
    with pytest.raises(UnsupportedKind, match="plugin-companion"):
        build_command(
            _reg(
                "companion",
                kind="plugin-companion",
                spec={
                    "command": ["bin/serve"],
                    "stop_command": ["bin/stop"],
                    "health_probe": ["bin/health"],
                },
            )
        )


def test_plugin_companion_runtime_revision_changes_fingerprint():
    first = _reg("companion", kind="plugin-companion", spec={"command": ["bin/serve"]})
    first["runtime_revision"] = {
        "plugin_root": "/plugins/index",
        "plugin_owner": "index@example",
        "plugin_source_path": "/plugins/index/registrar/service.json",
        "plugin_version": "1.0.0",
        "activation_scopes": ["project:demo"],
    }
    second = dict(first)
    second["runtime_revision"] = {
        "plugin_root": "/plugins/index",
        "plugin_owner": "index@example",
        "plugin_source_path": "/plugins/index/registrar/service.json",
        "plugin_version": "1.0.1",
        "activation_scopes": ["project:demo"],
    }
    assert _spec_fingerprint(first) != _spec_fingerprint(second)

    for field, value in (
        ("plugin_source_path", "/plugins/index/registrar/replacement.json"),
        ("activation_scopes", ["project:other"]),
    ):
        changed = dict(first)
        changed["runtime_revision"] = dict(first["runtime_revision"])
        changed["runtime_revision"][field] = value
        assert _spec_fingerprint(first) != _spec_fingerprint(changed)


def test_lease_scope_format():
    assert supervisor_lease_scope("anomalous-potato", "default") == \
        "supervisor:anomalous-potato:default"
    assert supervisor_lease_scope(None, "") == "supervisor:local:default"


# -- reconcile ---------------------------------------------------------------


def test_reconcile_starts_a_unit_per_registration():
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    summary = d.reconcile_once()
    assert set(summary.started) == {"a", "b"}
    assert summary.running == ["a", "b"]
    # idempotent: a second reconcile with no change starts nothing
    summary2 = d.reconcile_once()
    assert summary2.started == []
    assert summary2.running == ["a", "b"]


def test_reconcile_spawns_registration_children_via_own_canonical_python(
    monkeypatch,
):
    """Regression test: a registration child must run under this daemon's own
    canonically-resolved current-version slot, never a bare sys.executable --
    the sibling fix to the coordinator's/supervisor-launcher's own spawn sites
    (see agent_dispatch.procutil.resolve_own_runtime_python)."""
    monkeypatch.setattr(
        "agent_dispatch.procutil.resolve_own_runtime_python",
        lambda: "CANONICAL-SLOT-PYTHON",
    )
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    assert launcher.commands[0][1][0] == "CANONICAL-SLOT-PYTHON"


def test_own_python_is_resolved_once_and_cached(monkeypatch):
    calls = []

    def _fake():
        calls.append(1)
        return "PY"

    monkeypatch.setattr("agent_dispatch.procutil.resolve_own_runtime_python", _fake)
    d = _daemon(FakeClient([]), FakeLauncher())
    assert d._own_python() == "PY"
    assert d._own_python() == "PY"
    assert len(calls) == 1


def test_companion_provider_active_starts_and_inactive_winds_down():
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    launcher = FakeLauncher()
    daemon = _daemon(
        client, launcher, companion_controller=controller
    )

    started = daemon.reconcile_once()
    assert started.started == ["companion"]
    assert launcher.launched == []

    controller.resolve_outcomes.append(None)
    stopped = daemon.reconcile_once()
    assert stopped.stopped == ["companion"]
    assert controller.stopped == ["companion"]
    assert controller.adopted_receipts[-1] == {"companion"}


def test_companion_provider_indeterminate_retains_same_authority():
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller
    )
    daemon.reconcile_once()
    first = controller.proc_for("companion")
    controller.resolve_outcomes.append(
        CompanionIndeterminate("provider unavailable")
    )

    summary = daemon.reconcile_once()

    assert summary.stopped == []
    assert controller.proc_for("companion") is first


def test_companion_indeterminate_new_authority_winds_down_old_process():
    client = FakeClient([_companion_reg(version="1")])
    controller = FakeCompanionController()
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller
    )
    daemon.reconcile_once()
    controller.resolve_outcomes.append(
        CompanionIndeterminate("new provider unavailable")
    )
    client.set_regs([_companion_reg(version="2")])

    summary = daemon.reconcile_once()

    assert summary.stopped == ["companion"]
    assert summary.running == []
    assert controller.stopped == ["companion"]
    assert controller.stopped_versions == ["1"]


def test_companion_provider_runtime_change_restarts_unit():
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    controller.resolve_outcomes.append(
        {"arguments": ["--first"], "environment": {}, "environment_digest": "a"}
    )
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller
    )
    daemon.reconcile_once()
    first = controller.proc_for("companion")
    controller.resolve_outcomes.append(
        {"arguments": ["--second"], "environment": {}, "environment_digest": "b"}
    )

    summary = daemon.reconcile_once()

    assert summary.restarted == ["companion"]
    assert controller.proc_for("companion") is not first


def test_companion_confirmed_unhealthy_restarts_but_indeterminate_health_keeps():
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller
    )
    daemon.reconcile_once()
    first = controller.proc_for("companion")
    controller.health_outcomes.append(
        CompanionIndeterminate("probe unavailable")
    )
    retained = daemon.reconcile_once()
    assert retained.restarted == []
    assert controller.proc_for("companion") is first

    controller.health_outcomes.append(False)
    restarted = daemon.reconcile_once()
    assert restarted.unhealthy == ["companion"]
    assert restarted.restarted == ["companion"]
    assert controller.proc_for("companion") is not first


def test_companion_recovery_is_reported_without_duplicate_start():
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    controller.recover_next = True
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller
    )

    summary = daemon.reconcile_once()

    assert summary.recovered == ["companion"]
    assert summary.started == []
    assert len(controller.launched) == 1


def test_companion_crash_tree_is_retired_before_restart():
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller
    )
    daemon.reconcile_once()
    controller.proc_for("companion").crash()

    summary = daemon.reconcile_once()

    assert controller.retired == ["companion"]
    assert summary.revived == ["companion"]


def test_crash_revival_not_suppressed_by_a_still_pending_health_probe():
    """If the process exits while its health probe is still off-thread and
    pending, the reconcile loop must not keep treating it as "healthy for
    this tick, re-check next time" forever -- once the process has actually
    exited, it must fall through to the normal crash-revival handling
    instead of a still-pending probe permanently suppressing it."""
    release = threading.Event()
    controller = FakeCompanionController()
    real_health = controller.health

    def blocking_health(resolution):
        release.wait(timeout=5)
        return real_health(resolution)

    controller.health = blocking_health  # type: ignore[method-assign]

    client = FakeClient([_companion_reg()])
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller, reconcile_call_budget=0.05
    )
    try:
        daemon.reconcile_once()  # starts the unit
        proc = controller.proc_for("companion")
        # A health probe is submitted and stays pending (never released).
        summary = daemon.reconcile_once()
        assert summary.unhealthy == []
        assert summary.revived == []
        # The process exits while that same probe is still stuck.
        proc.crash()
        summary = daemon.reconcile_once()
        assert summary.revived == ["companion"]
    finally:
        release.set()


def test_reconcile_stops_removed_registration():
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    client.set_regs([_reg("a")])  # b removed
    summary = d.reconcile_once()
    assert summary.stopped == ["b"]
    assert summary.running == ["a"]
    assert launcher.proc_for("b").terminated is True


def test_reconcile_stops_paused_registration():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    client.set_regs([_reg("a", status="paused")])
    summary = d.reconcile_once()
    assert summary.stopped == ["a"]
    assert summary.running == []


def test_reconcile_starts_and_withdraws_declared_registration():
    from agent_dispatch.registrar import load_declaration

    declaration = load_declaration(
        {"name": "plugin-profile", "owner": "producer@example-marketplace"}
    )
    current = [[declaration]]
    client = FakeClient([])
    launcher = FakeLauncher()
    daemon = _daemon(
        client,
        launcher,
        declared_source=lambda: current[0],
    )

    started = daemon.reconcile_once()
    registration_id = "declared:producer@example-marketplace:plugin-profile"
    assert started.started == [registration_id]

    current[0] = []
    stopped = daemon.reconcile_once()
    assert stopped.stopped == [registration_id]
    assert launcher.proc_for(registration_id).terminated is True


# -- operator overrides (kill-switch) ----------------------------------------


def test_reconcile_winds_down_overridden_unit():
    """Disabling a running unit via an override winds it down on the next reconcile
    -- the reconcile's stop-not-desired path, driven by the override subtraction."""
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    override_map: dict[str, dict] = {}
    d = _daemon(client, launcher, overrides_source=lambda: override_map)
    d.reconcile_once()  # both start
    override_map["b"] = {"disabled": True, "reason": "misbehaving"}
    summary = d.reconcile_once()
    assert summary.stopped == ["b"]
    assert launcher.proc_for("b").terminated is True
    # a is untouched
    assert "a" in d._units and "b" not in d._units


def test_override_outranks_declaration_and_survives_resync():
    """An override wins over the desired set every reconcile: a still-registered
    (or re-declared) unit stays wound down until the override is cleared -- a repo
    re-sync cannot quietly revive it."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    override_map = {"a": {"disabled": True, "reason": "stop"}}
    d = _daemon(client, launcher, overrides_source=lambda: override_map)
    summary = d.reconcile_once()
    assert summary.running == []  # never started -- overridden off from the start
    assert "a" not in d._units
    # a "re-sync" (still registered) does not revive it while the override stands.
    d.reconcile_once()
    assert "a" not in d._units
    # clearing the override returns it to its registered state.
    override_map.clear()
    summary = d.reconcile_once()
    assert summary.running == ["a"]
    assert "a" in d._units


def test_logical_override_winds_down_declared_unit_but_not_conflict():
    client = FakeClient(
        [
            _reg(
                "legacy",
                logical_id="review-workers",
                spec={"repo": TEST_REPO, "max_concurrent": 2, "max_attempts": 3},
            ),
        ]
    )
    launcher = FakeLauncher()
    declaration = load_declaration(
        {
            "name": "review-workers",
            "repos": TEST_REPO,
            "owner": "repo:example",
        }
    )
    overrides = {"logical:repo:example:review-workers": {"disabled": True}}
    daemon = _daemon(
        client,
        launcher,
        declared_source=lambda: [declaration],
        overrides_source=lambda: overrides,
    )

    summary = daemon.reconcile_once()

    assert summary.running == ["legacy"]
    assert set(daemon._units) == {"legacy"}


def test_logical_override_is_scoped_to_declaration_owner():
    client = FakeClient([])
    launcher = FakeLauncher()
    declared = [
        load_declaration(
            {
                "name": "review-workers",
                "repos": TEST_REPO,
                "owner": "repo:a",
            }
        ),
        load_declaration(
            {
                "name": "review-workers",
                "repos": TEST_REPO,
                "owner": "repo:b",
            }
        ),
    ]
    overrides = {"logical:repo:a:review-workers": {"disabled": True}}
    daemon = _daemon(
        client,
        launcher,
        declared_source=lambda: declared,
        overrides_source=lambda: overrides,
    )

    summary = daemon.reconcile_once()

    assert summary.running == ["declared:repo:b:review-workers"]


def test_override_disabled_false_is_inert():
    """A record left disabled=false is the same as no override -- the unit runs."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, overrides_source=lambda: {"a": {"disabled": False}})
    summary = d.reconcile_once()
    assert summary.running == ["a"]
    assert "a" in d._units


def test_override_source_error_fails_safe_to_none():
    """A raising overrides_source is treated as 'no overrides' -- a bad read must
    never wind down declared/registered units."""
    def boom():
        raise RuntimeError("cannot read overrides")

    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, overrides_source=boom)
    summary = d.reconcile_once()
    assert summary.running == ["a"]
    assert "a" in d._units


def test_reconcile_restarts_on_spec_change():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    first = launcher.proc_for("a")
    client.set_regs([_reg("a", spec={"repo": TEST_REPO, "max_concurrent": 9})])
    summary = d.reconcile_once()
    assert summary.restarted == ["a"]
    assert first.terminated is True
    assert launcher.proc_for("a") is not first


def test_reconcile_ignores_retired_reactive_spec_changes():
    client = FakeClient([_reg("a", spec={
        "repo": TEST_REPO,
        "max_concurrent": 1,
        "reactive": True,
        "reactive_interval": 0.1,
    })])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    d.reconcile_once()
    first = launcher.proc_for("a")

    client.set_regs([_reg("a", spec={
        "repo": TEST_REPO,
        "max_concurrent": 1,
        "reactive": False,
        "reactive_interval": 300,
    })])
    summary = d.reconcile_once()

    assert summary.restarted == []
    assert first.terminated is False
    assert launcher.proc_for("a") is first


def test_reconcile_revives_crashed_unit_with_backoff_and_cap():
    clock = Clock(0.0)
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, clock=clock, restart_backoff=10.0, max_restarts=2)
    d.reconcile_once()  # start proc0
    launcher.proc_for("a").crash()

    # t=0: revive (restart_after was 0)
    s1 = d.reconcile_once()
    assert s1.revived == ["a"]
    launcher.proc_for("a").crash()

    # t=5: still in backoff (restart_after == 10) -> no revive
    clock.t = 5.0
    s2 = d.reconcile_once()
    assert s2.revived == []
    assert s2.running == []  # crashed proc is not "running"
    assert "a" in s2.backing_off  # unit retained, awaiting backoff

    # t=15: backoff elapsed -> revive again (2nd restart, hits the cap)
    clock.t = 15.0
    s3 = d.reconcile_once()
    assert s3.revived == ["a"]
    launcher.proc_for("a").crash()

    # t=30: exceeded max_restarts (2) -> left stopped, not revived
    clock.t = 30.0
    s4 = d.reconcile_once()
    assert s4.revived == []
    assert "a" in s4.skipped
    assert s4.running == []


def test_reconcile_skips_unsupported_kind():
    client = FakeClient([_reg("a", kind="totally-unknown", spec={"x": 1})])
    launcher = FakeLauncher()
    d = _daemon(client, launcher)
    summary = d.reconcile_once()
    assert summary.skipped == ["a"]
    assert summary.started == []
    assert launcher.launched == []


# -- serve / single-instance -------------------------------------------------


def test_serve_stands_down_when_scope_already_held():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, lock=FakeLock(granted=False))
    rc = d.serve(once=True)
    assert rc == 3
    assert launcher.launched == []  # never ran


def test_serve_runs_once_and_winds_down():
    client = FakeClient([_reg("a"), _reg("b")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    seen = []
    d = _daemon(client, launcher, lock=lock)
    rc = d.serve(once=True, on_cycle=seen.append)
    assert rc == 0
    assert set(seen[0].started) == {"a", "b"}
    # shutdown terminated every unit and released the singleton lock
    assert launcher.proc_for("a").terminated is True
    assert launcher.proc_for("b").terminated is True
    assert lock.released is True


def test_serve_unguarded_skips_election():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, lock=FakeLock(granted=False))
    rc = d.serve(once=True, single_instance=False)
    assert rc == 0
    assert launcher.proc_for("a")  # ran despite a held lock


def test_serve_calls_on_cycle_start_before_reconcile_with_a_monotonic_id():
    """The heartbeat-start hook fires before the cycle body runs, carrying a
    per-process-lifetime monotonic cycle id -- what a caller persists so a
    hang shows up as a start with no matching finish."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    starts = []
    d = _daemon(client, launcher, lock=lock)
    d.serve(once=True, on_cycle_start=lambda cycle_id, started_at: starts.append(
        (cycle_id, started_at)
    ))
    assert len(starts) == 1
    cycle_id, started_at = starts[0]
    assert cycle_id == 1
    assert started_at == 0.0  # the fake Clock()'s fixed value


def test_serve_on_cycle_start_id_advances_across_cycles():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    d = _daemon(client, launcher, lock=lock)
    ids = []
    calls = {"n": 0}

    def _stop_after_two(_cycle_id, _started_at):
        ids.append(_cycle_id)
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt

    d.serve(once=False, on_cycle_start=_stop_after_two)
    assert ids == [1, 2]


def test_serve_logs_a_heartbeat_at_both_cycle_boundaries(caplog):
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    d = _daemon(client, launcher, lock=lock)
    with caplog.at_level("INFO", logger="agent-dispatch.supervisor-daemon"):
        d.serve(once=True)
    messages = [record.getMessage() for record in caplog.records]
    assert any("cycle 1 starting" in m for m in messages)
    assert any("cycle 1 finished" in m for m in messages)


def test_serve_on_cycle_start_failure_never_aborts_the_cycle():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    d = _daemon(client, launcher, lock=lock)

    def _boom(_cycle_id, _started_at):
        raise RuntimeError("blip")

    rc = d.serve(once=True, on_cycle_start=_boom)
    assert rc == 0
    assert launcher.proc_for("a")  # the cycle's own work still ran


# -- reconnect (coordinator restart / moved port, #3825) ---------------------


class ConnResetClient:
    """A client whose reads fail with a connection error until swapped out."""

    def __init__(self):
        self.calls = 0
        self.closed = False

    def list_registrations(self, **_kw):
        self.calls += 1
        raise ConnectionRefusedError("connection refused")

    def close(self) -> None:
        self.closed = True


def test_is_connection_error_classifies_transport_vs_http():
    from agent_dispatch.client import DispatchError
    from agent_dispatch.supervisor_daemon import _is_connection_error

    assert _is_connection_error(ConnectionRefusedError("refused")) is True
    assert _is_connection_error(TimeoutError("timed out")) is True
    assert _is_connection_error(OSError("winsock")) is True
    # A live coordinator returning an HTTP error must NOT trigger a reconnect.
    assert _is_connection_error(DispatchError(503, "unavailable")) is False
    assert _is_connection_error(ValueError("bad json")) is False


def test_serve_reconnects_on_connection_failure():
    """A connection failure rebuilds the client via the factory, and the next
    reconcile reaches the (moved) coordinator -- the daemon does not wedge."""
    dead = ConnResetClient()
    healthy = FakeClient([_reg("a")])
    rebuilt: list[object] = []

    def factory():
        rebuilt.append(healthy)
        return healthy

    launcher = FakeLauncher()
    d = _daemon(dead, launcher, lock=FakeLock(granted=True), client_factory=factory)
    # One cycle: reconcile_once() fails at the connection level -> _reconnect()
    # swaps in the healthy client.
    d.serve(once=True)
    assert rebuilt == [healthy]  # factory was called
    assert d.client is healthy  # client was swapped
    assert dead.closed is True  # old client closed on reconnect

    # The next reconcile now succeeds against the re-resolved coordinator.
    summary = d.reconcile_once()
    assert summary.started == ["a"]


def test_serve_does_not_reconnect_without_factory():
    """Back-compat: with no factory the daemon keeps its client (tests inject a
    fixed fake) and simply retries next tick."""
    dead = ConnResetClient()
    launcher = FakeLauncher()
    d = _daemon(dead, launcher, lock=FakeLock(granted=True))
    d.serve(once=True)
    assert d.client is dead  # unchanged
    assert dead.closed is False


# -- one stubborn companion cannot block the whole reconcile loop -----------
#
# reconcile_once()/_reconcile_managed() run every companion's health probe
# and (for a launch/relaunch decision) prepare_managed()+validate() -- and
# validate() can wait on the same interprocess managed-runtime lock a slow
# concurrent materialize() build holds. Before this fix all three ran
# synchronously on the single reconcile thread, so one companion stuck in
# any of them (a hung probe subprocess, a contended lock) blocked noticing
# or reviving every *other* unit for that entire duration. These prove the
# fix: each call is routed through the runtime pool and polled with a
# bounded wait, so a stuck check only ever delays its own rid's next
# decision, never any other unit's, past a small configurable budget.


class _BlockingCompanion:
    """A companion controller whose health()/prepare_managed() block on a
    controllable event, standing in for a hung probe subprocess."""

    def __init__(self):
        self.health_calls = 0
        self.prepare_calls = 0
        self.release = threading.Event()
        self.fail_with: BaseException | None = None

    def health(self, resolution):
        self.health_calls += 1
        self.release.wait(timeout=5)
        if self.fail_with is not None:
            raise self.fail_with
        return True

    def prepare_managed(self, snapshot):
        self.prepare_calls += 1
        self.release.wait(timeout=5)


class _BlockingMaterializer:
    """A managed-runtime materializer whose validate() blocks on a
    controllable event, standing in for validate() waiting on the same
    interprocess lock a slow concurrent materialize() build holds."""

    def __init__(self):
        self.validate_calls = 0
        self.release = threading.Event()

    def materialize(self, registration):
        raise NotImplementedError

    def validate(self, registration, runtimes):
        self.validate_calls += 1
        self.release.wait(timeout=5)


class _NonBlockingCompanion:
    """A companion controller whose prepare_managed() is instant, so a
    validate()-focused test isolates validate()'s own blocking behavior."""

    def __init__(self):
        self.prepare_calls = 0

    def prepare_managed(self, snapshot):
        self.prepare_calls += 1


class _FakeSnapshot:
    def __init__(self, fingerprint="fp-1", registration=None, runtimes=()):
        self.fingerprint = fingerprint
        self._registration = registration or {}
        self.runtimes = runtimes

    def resolution(self):
        return SimpleNamespace(registration=self._registration)


def _poll_until(predicate, poll, *, timeout=2.0):
    from time import monotonic, sleep as real_sleep

    deadline = monotonic() + timeout
    result = None
    while monotonic() < deadline:
        result = poll()
        if predicate(result):
            return result
        real_sleep(0.01)
    return result


def test_poll_managed_health_never_blocks_past_the_budget_then_resolves():
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    snapshot = _FakeSnapshot()
    try:
        started = monotonic()
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None
        assert monotonic() - started < 2.0  # bounded by budget, not the 5s probe
        # Still pending: must not resubmit a second concurrent probe.
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None
        assert companion.health_calls == 1
        companion.release.set()
        result = _poll_until(
            lambda r: r is not None,
            lambda: daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05),
        )
        assert result is True
    finally:
        companion.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_poll_managed_health_drops_a_stale_probe_for_a_superseded_snapshot():
    """A health probe started for one snapshot must never have its result
    applied to a different (superseded) snapshot for the same rid -- e.g. an
    old probe unblocking after a relaunch already happened for a new
    fingerprint must not silently mark the *new* unit unhealthy/healthy
    based on stale data."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    old_snapshot = _FakeSnapshot(fingerprint="fp-old")
    new_snapshot = _FakeSnapshot(fingerprint="fp-new")
    try:
        assert daemon._poll_managed_health("rid-1", old_snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 0, lambda: companion.health_calls) == 1
        # A newer snapshot for the same rid must start its OWN fresh probe,
        # not wait on (or ever consume the result of) the abandoned old one.
        assert daemon._poll_managed_health("rid-1", new_snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 1, lambda: companion.health_calls) == 2
    finally:
        companion.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_poll_unmanaged_health_never_blocks_past_the_budget_then_resolves():
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    try:
        started = monotonic()
        assert daemon._poll_unmanaged_health("rid-1", None, monotonic() + 0.05) is None
        assert monotonic() - started < 2.0
        assert daemon._poll_unmanaged_health("rid-1", None, monotonic() + 0.05) is None
        assert companion.health_calls == 1
        companion.release.set()
        result = _poll_until(
            lambda r: r is not None,
            lambda: daemon._poll_unmanaged_health("rid-1", None, monotonic() + 0.05),
        )
        assert result is True
    finally:
        companion.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_poll_managed_validate_never_blocks_past_the_budget_then_resolves():
    """The dangerous case: validate() waiting on a contended managed-runtime
    lock must not stall reconciling any other companion past the budget."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _NonBlockingCompanion()
    materializer = _BlockingMaterializer()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_materializer=materializer,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    snapshot = _FakeSnapshot()
    try:
        started = monotonic()
        assert daemon._poll_managed_validate("rid-1", snapshot, monotonic() + 0.05) is None
        assert monotonic() - started < 2.0
        # Still pending: must not resubmit a second concurrent check.
        assert daemon._poll_managed_validate("rid-1", snapshot, monotonic() + 0.05) is None
        result = _poll_until(
            lambda r: r != 0, lambda: materializer.validate_calls
        )
        assert result == 1
        assert companion.prepare_calls == 1
        materializer.release.set()
        result = _poll_until(
            lambda r: r is not None,
            lambda: daemon._poll_managed_validate("rid-1", snapshot, monotonic() + 0.05),
        )
        assert result is True
    finally:
        materializer.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_poll_managed_validate_drops_a_stale_check_for_a_superseded_snapshot():
    """A snapshot superseded mid-check (new fingerprint) must not be kept
    waiting on the old, abandoned check -- a fresh one starts instead."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _NonBlockingCompanion()
    materializer = _BlockingMaterializer()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_materializer=materializer,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    old_snapshot = _FakeSnapshot(fingerprint="fp-old")
    new_snapshot = _FakeSnapshot(fingerprint="fp-new")
    try:
        assert daemon._poll_managed_validate("rid-1", old_snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 0, lambda: materializer.validate_calls) == 1
        # A newer snapshot for the same rid must start its OWN fresh check,
        # not wait on the abandoned one for the old fingerprint.
        assert daemon._poll_managed_validate("rid-1", new_snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 1, lambda: materializer.validate_calls) == 2
    finally:
        materializer.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_reconcile_call_budget_is_shared_across_companions_in_one_pass():
    """Two stuck companions in the same _reconcile_managed() call must not
    each separately consume the full budget -- the total added delay across
    both is bounded by one shared budget, never 2x it."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=4),
        reconcile_call_budget=0.2,
    )
    snapshot = _FakeSnapshot()
    try:
        deadline = monotonic() + 0.2
        started = monotonic()
        assert daemon._poll_managed_health("rid-1", snapshot, deadline) is None
        assert daemon._poll_managed_health("rid-2", snapshot, deadline) is None
        assert daemon._poll_managed_health("rid-3", snapshot, deadline) is None
        elapsed = monotonic() - started
        # If each rid separately re-waited the full 0.2s budget this would be
        # >= 0.6s; sharing one deadline keeps the whole pass close to 0.2s.
        assert elapsed < 0.5
    finally:
        companion.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_launch_managed_skips_redundant_precondition_when_already_confirmed():
    """A caller that already ran prepare_managed()/validate() successfully
    this tick via _poll_managed_validate must not have _launch_managed()
    silently re-run them synchronously -- that would defeat the whole point
    of polling them off-thread first."""
    companion = FakeCompanionController()
    companion.prepare_calls = 0

    def _prepare_managed(snapshot):
        companion.prepare_calls += 1

    companion.prepare_managed = _prepare_managed
    materializer = _BlockingMaterializer()
    materializer.release.set()  # never actually blocks in this test
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_materializer=materializer,
    )
    snapshot = _FakeSnapshot(registration={"id": "rid-1"})
    from agent_dispatch.supervisor_daemon import ReconcileSummary

    summary = ReconcileSummary()

    daemon._launch_managed(snapshot, summary, bucket="started", precondition_confirmed=True)
    assert materializer.validate_calls == 0
    assert companion.prepare_calls == 0
    assert "rid-1" in daemon._units

    # The default (unconfirmed) behavior is unchanged -- still runs both.
    daemon._units.clear()
    daemon._launch_managed(
        _FakeSnapshot(fingerprint="fp-2", registration={"id": "rid-1"}), summary, bucket="started"
    )
    assert materializer.validate_calls == 1
    assert companion.prepare_calls == 1


def test_reconcile_once_shares_one_deadline_across_managed_and_unmanaged_phases():
    """A stuck managed-companion probe consuming (most of) the shared budget
    must not let the unmanaged-companion crash-revival phase in the same
    tick add a SECOND separate budget on top -- both phases must share the
    exact same deadline value, computed once at the top of reconcile_once()."""
    captured: dict[str, float] = {}
    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    daemon = _daemon(
        client, FakeLauncher(), companion_controller=controller, reconcile_call_budget=0.2
    )
    daemon.reconcile_once()  # starts the (unmanaged) companion unit

    real_reconcile_managed = daemon._reconcile_managed

    def spy_reconcile_managed(desired, summary, *, skip=None, deadline=None):
        captured["managed"] = deadline
        return real_reconcile_managed(desired, summary, skip=skip, deadline=deadline)

    daemon._reconcile_managed = spy_reconcile_managed

    real_poll_unmanaged_health = daemon._poll_unmanaged_health

    def spy_poll_unmanaged_health(rid, resolution, deadline):
        captured["unmanaged"] = deadline
        return real_poll_unmanaged_health(rid, resolution, deadline)

    daemon._poll_unmanaged_health = spy_poll_unmanaged_health

    daemon.reconcile_once()
    assert "managed" in captured and "unmanaged" in captured
    assert captured["managed"] == captured["unmanaged"]


def test_poll_managed_recovery_validate_never_blocks_past_the_budget_then_resolves():
    """The _managed_uncertain recovery path's own validate() precondition
    check must be bounded exactly like a launch/relaunch's validate() --
    a stuck lock wait here must not stall reconciling any other companion,
    and unlike _poll_managed_validate this path never calls
    prepare_managed()."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _NonBlockingCompanion()
    materializer = _BlockingMaterializer()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_materializer=materializer,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    snapshot = _FakeSnapshot()
    try:
        started = monotonic()
        assert daemon._poll_managed_recovery_validate("rid-1", snapshot, monotonic() + 0.05) is None
        assert monotonic() - started < 2.0
        # Still pending: must not resubmit a second concurrent check.
        assert daemon._poll_managed_recovery_validate("rid-1", snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 0, lambda: materializer.validate_calls) == 1
        # Recovery never runs a configuration provider.
        assert companion.prepare_calls == 0
        materializer.release.set()
        result = _poll_until(
            lambda r: r is not None,
            lambda: daemon._poll_managed_recovery_validate("rid-1", snapshot, monotonic() + 0.05),
        )
        assert result is True
    finally:
        materializer.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_poll_managed_recovery_validate_drops_a_stale_check_for_a_superseded_snapshot():
    """A recovery-validate check started for one previous-snapshot fingerprint
    must not be kept waiting on an abandoned check once a newer fingerprint
    for the same rid supersedes it."""
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _NonBlockingCompanion()
    materializer = _BlockingMaterializer()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_materializer=materializer,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    old_snapshot = _FakeSnapshot(fingerprint="fp-old")
    new_snapshot = _FakeSnapshot(fingerprint="fp-new")
    try:
        assert daemon._poll_managed_recovery_validate("rid-1", old_snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 0, lambda: materializer.validate_calls) == 1
        assert daemon._poll_managed_recovery_validate("rid-1", new_snapshot, monotonic() + 0.05) is None
        assert _poll_until(lambda r: r != 1, lambda: materializer.validate_calls) == 2
    finally:
        materializer.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_a_failed_health_probe_is_not_cached_forever_and_allows_retry():
    """An exception the outer reconcile handler catches (not just the
    narrow CompanionError/CompanionIndeterminate/OSError set) must still
    release this rid's tracking entry -- otherwise every later poll for the
    same fingerprint just re-raises the same already-completed future's
    exception forever, permanently blocking a retry."""
    from concurrent.futures import ThreadPoolExecutor

    companion = _BlockingCompanion()
    companion.fail_with = RuntimeError("boom")  # outside the narrow except set
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    snapshot = _FakeSnapshot()
    try:
        companion.release.set()  # resolve (raise) immediately
        with pytest.raises(RuntimeError):
            daemon._poll_managed_health("rid-1", snapshot, time.monotonic() + 0.05)
        assert "rid-1" not in daemon._managed_health_futures
        # A retry must start a genuinely fresh probe, not reuse/re-raise the
        # old (already-failed) one.
        companion.fail_with = None
        result = _poll_until(
            lambda r: r is not None,
            lambda: daemon._poll_managed_health("rid-1", snapshot, time.monotonic() + 0.05),
        )
        assert result is True
        assert companion.health_calls == 2
    finally:
        companion.release.set()
        daemon._runtime_executor.shutdown(wait=False, cancel_futures=True)


def test_abandoned_probes_are_still_drained_by_shutdown():
    """A probe abandoned mid-flight (its snapshot superseded, so it is
    dropped from the per-rid tracking dict) can't be cancelled -- it keeps
    occupying a pool worker regardless. shutdown() must still wait for it
    via the shared _probe_inflight set, not just whatever is still in the
    per-rid dicts at that moment."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.3
    try:
        old_snapshot = _FakeSnapshot(fingerprint="fp-old")
        new_snapshot = _FakeSnapshot(fingerprint="fp-new")
        assert daemon._poll_managed_health("rid-1", old_snapshot, monotonic() + 0.05) is None
        # Superseding the snapshot drops the old probe from the per-rid dict
        # entirely, but it is still running (blocked) -- must remain tracked.
        assert daemon._poll_managed_health("rid-1", new_snapshot, monotonic() + 0.05) is None
        assert len(daemon._probe_inflight) == 2

        def _unblock_soon():
            time.sleep(0.05)
            companion.release.set()

        threading.Thread(target=_unblock_soon, daemon=True).start()
        started = monotonic()
        daemon.shutdown()
        elapsed = monotonic() - started
        assert elapsed < 2.0  # bounded -- never hangs on the abandoned probe
        assert companion.health_calls == 2
    finally:
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace
        companion.release.set()


def test_shutdown_applies_the_grace_wait_once_not_per_future_category():
    """materialize/cleanup futures and probe futures pending at the same
    time must share ONE grace wait, not two sequential ones (which would let
    shutdown take up to 2x _SHUTDOWN_GRACE_SECONDS)."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from concurrent.futures import Future, ThreadPoolExecutor
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        runtime_executor=ThreadPoolExecutor(max_workers=2),
        reconcile_call_budget=0.05,
    )
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.3
    try:
        snapshot = _FakeSnapshot()
        # A never-resolving "materialize" future, standing in for one still
        # genuinely in flight (never released -- shutdown must not wait the
        # full grace for this AND then again the full grace for the probe).
        never_resolves: Future = Future()
        never_resolves.set_running_or_notify_cancel()
        daemon._managed_runtime_inflight.add(never_resolves)
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None

        started = monotonic()
        daemon.shutdown()
        elapsed = monotonic() - started
        # Two sequential 0.3s waits would take ~0.6s; one shared wait keeps
        # it close to 0.3s.
        assert elapsed < 0.5
    finally:
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace
        companion.release.set()


def test_shutdown_waits_briefly_for_a_pending_health_probe_before_exiting():
    """shutdown() must give an in-flight health/validate probe a bounded
    grace wait too -- not just materialize()/cleanup() -- since a probe left
    fully untracked would otherwise keep the pool's worker thread alive
    across interpreter exit (Python joins pool threads at exit) with no
    grace bound at all. A probe that finishes within the (shrunk, for this
    test) grace window must be waited for; shutdown must still return
    promptly afterward either way."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        reconcile_call_budget=0.05,
    )
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.3
    try:
        snapshot = _FakeSnapshot()
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None

        def _unblock_soon():
            time.sleep(0.05)
            companion.release.set()

        threading.Thread(target=_unblock_soon, daemon=True).start()
        started = monotonic()
        daemon.shutdown()
        elapsed = monotonic() - started
        assert elapsed < 2.0  # bounded -- never hangs on a stuck probe
        assert companion.health_calls == 1
    finally:
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace
        companion.release.set()


def test_shutdown_returns_true_when_everything_drains_within_grace():
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        reconcile_call_budget=0.05,
    )
    try:
        snapshot = _FakeSnapshot()
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None

        def _unblock_soon():
            time.sleep(0.05)
            companion.release.set()

        threading.Thread(target=_unblock_soon, daemon=True).start()
        assert daemon.shutdown() is True
    finally:
        companion.release.set()


def test_shutdown_returns_false_when_a_probe_is_still_stuck_past_the_grace():
    """A genuinely stuck probe means shutdown() cannot claim a clean drain --
    callers that can safely force-exit (see run()'s self-update-triggered
    path) need this signal to know a plain return would otherwise leave a
    live worker thread for Python's atexit join to wait on."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        reconcile_call_budget=0.05,
    )
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.1
    try:
        snapshot = _FakeSnapshot()
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None
        # Never released within the grace window -- stays genuinely stuck.
        assert daemon.shutdown() is False
    finally:
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace
        companion.release.set()


def test_shutdown_is_idempotent_for_the_same_executor_and_does_not_re_wait():
    """A second shutdown() call for the SAME (already grace-waited) owned
    executor -- e.g. serve()'s finally re-calling it after
    _maybe_self_update already did during the same handoff -- must reuse
    the cached drain result rather than sitting through a second full
    _SHUTDOWN_GRACE_SECONDS wait for a probe that is still stuck."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from time import monotonic

    companion = _BlockingCompanion()
    daemon = _daemon(
        FakeClient([]), FakeLauncher(),
        companion_controller=companion,
        reconcile_call_budget=0.05,
    )
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.2
    try:
        snapshot = _FakeSnapshot()
        assert daemon._poll_managed_health("rid-1", snapshot, monotonic() + 0.05) is None
        assert daemon.shutdown() is False  # first call: pays the one grace wait

        started = monotonic()
        assert daemon.shutdown() is False  # second call, same executor: cached
        elapsed = monotonic() - started
        assert elapsed < 0.05  # nowhere near a second 0.2s grace wait
    finally:
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace
        companion.release.set()


def test_crash_revival_clears_the_old_process_stale_health_future():
    """A health probe in flight for a crashed process must not have its
    (stale) result silently applied to the freshly-revived process on a
    later tick -- the crash-revival path must clear the per-rid future
    before starting the replacement, even though it bypasses _stop()."""
    from concurrent.futures import Future

    client = FakeClient([_companion_reg()])
    controller = FakeCompanionController()
    daemon = _daemon(client, FakeLauncher(), companion_controller=controller)
    daemon.reconcile_once()

    # Simulate a health probe left over from the still-live process, never
    # resolved by the time it crashes.
    stale = Future()
    daemon._unmanaged_health_futures["companion"] = stale

    controller.proc_for("companion").crash()
    summary = daemon.reconcile_once()

    assert summary.revived == ["companion"]
    # The crash-revival path must not have left the stale future keyed to
    # the new process -- either dropped entirely, or replaced by a fresh one
    # for the new process, but never the same abandoned object.
    assert daemon._unmanaged_health_futures.get("companion") is not stale


# -- live self-update (#2259) -------------------------------------------------


def test_self_update_disabled_by_default():
    """With no argv supplied, self-update stays off even if the caller passes
    enabled=True -- there is nothing safe to respawn with."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    d = _daemon(client, launcher, lock=FakeLock(granted=True), self_update_enabled=True)
    assert d.self_update_enabled is False


def test_self_update_not_triggered_when_not_stale():
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    spawned: list[tuple[object, list[str]]] = []
    d = _daemon(
        client, launcher, lock=FakeLock(granted=True),
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: None,
        self_update_spawn=lambda target, argv: spawned.append((target, argv)),
    )
    rc = d.serve(once=True)
    assert rc == 0
    assert spawned == []


def test_self_update_spawns_successor_and_hands_off():
    """A stale target triggers: units stop, the lease releases, the successor
    spawns with this daemon's own argv, and serve() returns the distinct code."""
    from agent_dispatch.supervisor_daemon import SELF_UPDATE_EXIT_CODE

    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    spawned: list[tuple[object, list[str]]] = []
    target = object()
    d = _daemon(
        client, launcher, lock=lock,
        self_update_enabled=True,
        self_update_argv=["supervise", "serve", "--machine", "anomalous-potato"],
        self_update_stale_target=lambda _root, _v: target,
        self_update_spawn=lambda t, argv: spawned.append((t, argv)),
    )
    rc = d.serve(once=False)  # the self-update break exits the loop itself
    assert rc == SELF_UPDATE_EXIT_CODE
    assert spawned == [(target, ["supervise", "serve", "--machine", "anomalous-potato"])]
    assert launcher.proc_for("a").terminated is True  # units wound down
    assert lock.released is True  # lease released before the spawn


def test_self_update_force_exits_rather_than_wait_on_a_stuck_probe():
    """If a probe/validate worker is still genuinely stuck past shutdown()'s
    grace window at the point serve() is about to return the self-update
    exit code, a plain return would let Python's atexit ThreadPoolExecutor
    join delay this process's actual exit well past the point the successor
    is already live. serve() must force-exit instead -- verified here by
    monkeypatching os._exit so the test process itself survives."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from agent_dispatch.supervisor_daemon import SELF_UPDATE_EXIT_CODE

    forced: list[int] = []
    real_exit = supervisor_daemon_module.os._exit
    supervisor_daemon_module.os._exit = forced.append  # type: ignore[assignment]

    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    target = object()
    d = _daemon(
        client, launcher, lock=lock,
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: target,
        self_update_spawn=lambda _t, _argv: None,
    )
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.05
    release = threading.Event()
    try:
        # A probe left permanently stuck (never released before the grace
        # window elapses) -- standing in for a health/validate call still
        # waiting on a contended lock right as self-update triggers.
        future = d._runtime_pool().submit(release.wait, 5)
        d._probe_inflight.add(future)

        rc = d.serve(once=False)
        assert rc == SELF_UPDATE_EXIT_CODE
        assert forced == [SELF_UPDATE_EXIT_CODE]
    finally:
        release.set()
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace
        supervisor_daemon_module.os._exit = real_exit


def test_self_update_ordering_releases_lease_before_spawn():
    """The lease must be released *before* the successor spawns, so the
    successor's own acquire never races this daemon's still-held lock."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    order: list[str] = []
    real_release = lock.release

    def tracked_release():
        order.append("release")
        real_release()

    lock.release = tracked_release  # type: ignore[method-assign]

    def spawn(_t, _argv):
        order.append("spawn")

    d = _daemon(
        client, launcher, lock=lock,
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: object(),
        self_update_spawn=spawn,
    )
    d.serve(once=False)
    assert order == ["release", "spawn", "release"]  # finally's redundant release is harmless


def test_self_update_defers_while_a_managed_runtime_build_is_in_flight():
    """Self-update must not spawn a successor while a materialize()/cleanup()
    task is still running: the successor's legacy-lock handshake only
    samples the lock once, so it cannot by itself force a wait for an
    old-process worker that has not yet reached its own lock acquisition --
    deferring avoids that overlapping old/new build race entirely."""
    from concurrent.futures import Future

    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    spawned: list[object] = []
    clock = Clock(0.0)
    d = _daemon(
        client, launcher, lock=lock, clock=clock,
        self_update_enabled=True,
        self_update_poll_interval=1.0,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: object(),
        self_update_spawn=lambda t, _argv: spawned.append(t),
    )
    in_flight: Future = Future()
    d._managed_runtime_futures["a"] = ("plugin-a", in_flight)
    d._managed_runtime_inflight.add(in_flight)

    result = d._maybe_self_update()

    assert result is False
    assert spawned == []  # deferred -- no successor spawned
    assert lock.released is False  # lease untouched, still running this version

    in_flight.set_result(())
    clock.t += 10.0  # past the poll interval, well under the cooldown default
    result = d._maybe_self_update()
    assert result is True
    assert spawned  # now completes once the build finished


def test_self_update_still_waits_on_a_cancelled_but_running_materialize_future():
    """cancel() is a no-op once a materialize() task is already running (not
    merely queued); a withdrawn/changed registration's future is still
    popped from _managed_runtime_futures in that case. The handoff barrier
    must keep waiting on it anyway (via _managed_runtime_inflight), not lose
    track of a build that is still actually in progress.
    """
    from concurrent.futures import Future

    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    spawned: list[object] = []
    clock = Clock(0.0)
    d = _daemon(
        client, launcher, lock=lock, clock=clock,
        self_update_enabled=True,
        self_update_poll_interval=1.0,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: object(),
        self_update_spawn=lambda t, _argv: spawned.append(t),
    )
    in_flight: Future = Future()
    # Simulate: submitted, tracked in both places, then the registration is
    # withdrawn/changed and cancel() is attempted on an already-running task
    # (a no-op -- it stays "not done"), which drops it from
    # _managed_runtime_futures but must not drop it from the inflight set.
    d._managed_runtime_futures["a"] = ("plugin-a", in_flight)
    d._managed_runtime_inflight.add(in_flight)
    in_flight.set_running_or_notify_cancel()  # simulate: a worker already claimed it
    assert in_flight.cancel() is False  # no-op: already running, not merely queued
    d._managed_runtime_futures.pop("a", None)

    result = d._maybe_self_update()

    assert result is False
    assert spawned == []  # still deferred -- the inflight future is not done
    assert lock.released is False

    in_flight.set_result(())
    clock.t += 10.0
    result = d._maybe_self_update()
    assert result is True
    assert spawned


def test_self_update_reclaims_lease_when_spawn_fails():
    """A spawn failure must not leave the daemon believing it still owns the
    lease it already released -- it reclaims the lease and keeps running."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)
    acquire_calls = []
    real_acquire = lock.acquire

    def counted_acquire():
        acquire_calls.append(1)
        return real_acquire()

    lock.acquire = counted_acquire  # type: ignore[method-assign]

    def failing_spawn(_t, _argv):
        raise OSError("no such file or directory")

    d = _daemon(
        client, launcher, lock=lock,
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: object(),
        self_update_spawn=failing_spawn,
    )
    rc = d.serve(once=True)
    assert rc == 0  # did not hand off -- stayed on this version
    # once for serve()'s initial election, once more for the post-failure reclaim
    assert len(acquire_calls) == 2


def test_self_update_retires_rather_than_discards_an_undrained_executor_on_spawn_failure():
    """If shutdown() (called before the spawn attempt) could not fully drain
    a stuck probe, and the spawn itself then fails, the old executor must
    not be silently discarded -- it stays tracked in
    _retired_runtime_executors rather than orphaned, so a repeated-failure
    loop never accumulates fully untracked live pools."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module

    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    lock = FakeLock(granted=True)

    def failing_spawn(_t, _argv):
        raise OSError("no such file or directory")

    d = _daemon(
        client, launcher, lock=lock,
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _v: object(),
        self_update_spawn=failing_spawn,
    )
    original_executor = d._runtime_pool()
    release = threading.Event()
    stuck = d._runtime_pool().submit(release.wait, 5)
    d._probe_inflight.add(stuck)
    # Force shutdown() to report an undrained probe deterministically rather
    # than race a real 5-second wait: patch _wait_futures to always report
    # something still pending.
    real_wait_futures = supervisor_daemon_module._wait_futures

    def fake_wait_futures(pending, *, timeout):
        return set(), set(pending)

    supervisor_daemon_module._wait_futures = fake_wait_futures
    try:
        result = d._maybe_self_update()
    finally:
        supervisor_daemon_module._wait_futures = real_wait_futures
        release.set()

    assert result is False
    assert d._retired_runtime_executors == [original_executor]


def test_shutdown_drains_a_retired_executors_probe_even_with_no_current_executor():
    """A future submitted to an executor that was later retired (see
    _maybe_self_update's spawn-failure branch, which sets
    self._runtime_executor to None) must still be waited on by a later
    shutdown() call -- gating the whole drain on a *current* executor
    existing would silently skip a genuinely still-running retired worker
    and falsely report a clean drain."""
    import agent_dispatch.supervisor_daemon as supervisor_daemon_module
    from time import monotonic

    d = _daemon(FakeClient([]), FakeLauncher())
    old_grace = supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS
    supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = 0.1
    release = threading.Event()
    try:
        retired = d._runtime_pool()
        stuck = retired.submit(release.wait, 5)
        d._probe_inflight.add(stuck)
        # Simulate the post-spawn-failure state: the executor is retired,
        # and the daemon no longer has a *current* one referenced.
        d._retired_runtime_executors.append(retired)
        d._runtime_executor = None

        started = monotonic()
        result = d.shutdown()
        elapsed = monotonic() - started

        assert result is False  # the retired probe is still genuinely stuck
        assert elapsed >= 0.08  # it was actually waited on, not skipped
        assert d._retired_runtime_executors == []  # swept
    finally:
        release.set()
        supervisor_daemon_module._SHUTDOWN_GRACE_SECONDS = old_grace


def test_self_update_respects_poll_interval():
    """The staleness check itself is throttled to the poll cadence, not run
    every tick."""
    client = FakeClient([_reg("a")])
    launcher = FakeLauncher()
    checks: list[float] = []

    def stale_target(_root, _v):
        checks.append(1)
        return None

    clock = Clock(0.0)
    d = _daemon(
        client, launcher, lock=FakeLock(granted=True), clock=clock,
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_poll_interval=100.0,
        self_update_stale_target=stale_target,
    )
    d._maybe_self_update()
    assert len(checks) == 1
    clock.t = 10.0
    d._maybe_self_update()
    assert len(checks) == 1  # still within the poll window -- not re-checked
    clock.t = 200.0
    d._maybe_self_update()
    assert len(checks) == 2


def test_self_update_settings_default_on(monkeypatch):
    from agent_dispatch.supervisor_daemon import _self_update_settings

    monkeypatch.delenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE", raising=False)
    enabled, poll, cooldown = _self_update_settings()
    assert enabled is True
    assert poll == 60.0
    assert cooldown == 900.0


def test_self_update_settings_falsy_values_disable(monkeypatch):
    from agent_dispatch.supervisor_daemon import _self_update_settings

    for value in ("0", "false", "FALSE", "no", "off"):
        monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE", value)
        enabled, *_ = _self_update_settings()
        assert enabled is False, value


def test_self_update_settings_overrides(monkeypatch):
    from agent_dispatch.supervisor_daemon import _self_update_settings

    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S", "5")
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_COOLDOWN_S", "10")
    enabled, poll, cooldown = _self_update_settings()
    assert enabled is True
    assert poll == 5.0
    assert cooldown == 10.0


def test_self_update_settings_invalid_values_fall_back(monkeypatch):
    from agent_dispatch.supervisor_daemon import _self_update_settings

    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_POLL_S", "nan")
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE_COOLDOWN_S", "not-a-number")
    _enabled, poll, cooldown = _self_update_settings()
    assert poll == 60.0
    assert cooldown == 900.0
