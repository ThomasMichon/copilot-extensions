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
from concurrent.futures import Future
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

    def launch(self, reg: dict, cmd: list[str]) -> FakeProc:
        proc = FakeProc()
        self.launched.append((reg["id"], proc))
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


class FakeRuntimeMaterializer:
    def __init__(self):
        self.registrations: list[dict] = []

    def materialize(self, registration):
        self.registrations.append(registration)
        return (SimpleNamespace(python="prepared-python"),)


class BlockingRuntimeMaterializer:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def materialize(self, registration):
        self.started.set()
        assert self.release.wait(timeout=5)
        return (SimpleNamespace(python="prepared-python"),)


class ImmediateExecutor:
    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


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


def _managed_companion_reg(rid="companion"):
    registration = _companion_reg(rid)
    managed = {
        "schema_version": 1,
        "runtimes": [
            {
                "name": "service",
                "version": "1",
                "profile": "host",
                "python_env": "EXAMPLE_MANAGED_PYTHON",
                "projects": [{"path": "."}],
                "imports": ["example_service"],
            }
        ],
    }
    registration["source"] = "declared"
    registration["owner"] = "plugin@example"
    registration["spec"]["managed_runtime"] = managed
    registration["runtime_revision"] = {
        "plugin_root": registration["plugin"]["root"],
        "plugin_owner": "plugin@example",
        "plugin_source_path": registration["plugin"]["source_path"],
        "plugin_version": registration["plugin"]["version"],
        "activation_scopes": registration["plugin"]["activation_scopes"],
        "managed_runtime": managed,
    }
    return registration


def _daemon(client, launcher, **kw):
    return SupervisorDaemon(
        client, "anomalous-potato", "default",
        launcher=launcher, sleep=lambda _s: None, **kw,
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


def test_managed_runtime_materialization_prepares_without_changing_launch():
    registration = _managed_companion_reg()
    controller = FakeCompanionController()
    materializer = FakeRuntimeMaterializer()
    daemon = _daemon(
        FakeClient([registration]),
        FakeLauncher(),
        companion_controller=controller,
        runtime_materializer=materializer,
        runtime_executor=ImmediateExecutor(),
    )

    first = daemon.reconcile_once()
    second = daemon.reconcile_once()

    assert first.started == ["companion"]
    assert second.restarted == []
    assert len(materializer.registrations) == 1
    launched = controller.launched[0][0]
    assert launched == "companion"
    assert controller.resolve_outcomes == []
    assert "EXAMPLE_MANAGED_PYTHON" not in registration.get(
        "companion_runtime", {}
    ).get("environment", {})


def test_managed_runtime_only_change_does_not_restart_live_companion():
    registration = _managed_companion_reg()
    controller = FakeCompanionController()
    materializer = FakeRuntimeMaterializer()
    client = FakeClient([registration])
    daemon = _daemon(
        client,
        FakeLauncher(),
        companion_controller=controller,
        runtime_materializer=materializer,
        runtime_executor=ImmediateExecutor(),
    )
    daemon.reconcile_once()
    changed = _managed_companion_reg()
    changed["spec"]["managed_runtime"]["runtimes"][0]["version"] = "2"
    changed["runtime_revision"]["managed_runtime"]["runtimes"][0]["version"] = "2"
    client.set_regs([changed])

    summary = daemon.reconcile_once()

    assert summary.restarted == []
    assert controller.stopped == []
    assert len(controller.launched) == 1
    assert len(materializer.registrations) == 2


def test_managed_runtime_build_does_not_block_companion_reconcile():
    registration = _managed_companion_reg()
    controller = FakeCompanionController()
    materializer = BlockingRuntimeMaterializer()
    daemon = _daemon(
        FakeClient([registration]),
        FakeLauncher(),
        companion_controller=controller,
        runtime_materializer=materializer,
    )

    started = time.monotonic()
    summary = daemon.reconcile_once()
    elapsed = time.monotonic() - started

    assert summary.started == ["companion"]
    assert elapsed < 1
    assert materializer.started.wait(timeout=2)
    materializer.release.set()
    daemon.shutdown()


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
