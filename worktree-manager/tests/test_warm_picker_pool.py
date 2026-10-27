"""Single-use UI ownership, scope refusal, and graceful resident drain."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from worktree_manager import engine_client, warm_picker_pool as warm


@pytest.fixture
def worker_ui_imports(monkeypatch):
    import sys
    from types import ModuleType

    from worktree_manager.production_picker import picker_tui

    # Worker custody tests replace runner.run; UI construction has its own suite.
    for name in ("engine", "data_local", "data_ssh"):
        module = ModuleType(f"{picker_tui.__name__}.{name}")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        monkeypatch.setattr(picker_tui, name, module, raising=False)


@pytest.fixture
def pool(tmp_path, monkeypatch):
    from worktree_manager.warm_picker_contract import worker_environment

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    clean = worker_environment(dict(os.environ))
    for key in set(os.environ) - clean.keys():
        monkeypatch.delenv(key)
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["qualified-engine"])
    which = warm.shutil.which
    monkeypatch.setattr(
        warm.shutil, "which",
        lambda name: "qualified-mux" if name in {"psmux", "tmux"} else which(name),
    )
    value = warm.WarmPickerPool(tmp_path)
    value.owner_start = "fixture-birth"
    monkeypatch.setattr(value, "_alive", lambda _: True)
    monkeypatch.setattr(value, "_worker_ready", lambda slot: (slot.directory / "ready.json").exists())
    monkeypatch.setattr(value, "_incarnation", lambda slot: "$1:123")
    calls = []
    monkeypatch.setattr(
        value, "_mux",
        lambda *args: calls.append(args) or subprocess.CompletedProcess(args, 0),
    )
    return value, calls


def spare(pool):
    value, _ = pool
    directory = value.directory / "spare"
    directory.mkdir(parents=True)
    (directory / "ready.json").write_text('{"pid":1}')
    slot = warm.Slot(directory, "owned-ui", time.monotonic(), incarnation="$1:123", context=value.context)
    value._slots.append(slot)
    return slot


def request():
    return {
        "manager_runtime": warm.runtime_identity(), "engine": ["qualified-engine"],
        "context": warm.invocation_context(),
        "cwd": os.getcwd(),
        "handoff": {"bootstrap": {"project": "sample", "headless": False}},
    }


def test_unused_pool_never_discovers_the_optional_mux(pool, tmp_path, monkeypatch):
    monkeypatch.setattr(
        warm.shutil, "which", lambda _name: pytest.fail("unused pool performed blocking discovery"),
    )
    value = warm.WarmPickerPool(tmp_path)
    value.pump()
    value.pump()
    value.close()
    assert value.mux is None
    assert not value.directory.exists()


def test_admitted_demand_resolves_once_then_warms_and_attaches(pool, monkeypatch):
    value, _ = pool
    lookups = []
    monkeypatch.setattr(
        warm.shutil, "which", lambda name: lookups.append(name) or "demand-selected-mux",
    )
    monkeypatch.setattr(value, "_spawn", lambda: spare(pool))
    payload = request()
    assert value.acquire(payload) == {"available": False, "reason": "warming"}
    assert lookups == ["psmux" if os.name == "nt" else "tmux"]
    value.pump()
    result = value.acquire(payload)
    assert result["available"]
    assert result["attach_command"] == [
        "demand-selected-mux", *value.mux_args, "attach-session", "-t", "owned-ui",
    ]
    assert len(lookups) == 1
    value.close()


@pytest.mark.parametrize("refusal", ["scope-mismatch", "draining"])
def test_refused_demand_never_discovers_the_optional_mux(pool, monkeypatch, refusal):
    value, _ = pool
    monkeypatch.setattr(
        warm.shutil, "which", lambda _name: pytest.fail("refused demand performed discovery"),
    )
    payload = request()
    if refusal == "draining":
        value.pause()
    else:
        payload["engine"] = ["foreign-engine"]
    assert value.acquire(payload) == {"available": False, "reason": refusal}
    value.pump()
    assert value.mux is None


def test_missing_mux_is_cached_without_background_discovery(pool, monkeypatch):
    value, _ = pool
    lookups = []
    monkeypatch.setattr(warm.shutil, "which", lambda name: lookups.append(name))
    monkeypatch.setattr(value, "_spawn", lambda: pytest.fail("missing mux was spawned"))
    assert value.acquire(request()) == {"available": False, "reason": "warming"}
    value.pump()
    assert value.acquire(request()) == {"available": False, "reason": "warming"}
    assert len(lookups) == 1


def test_spare_is_consumed_once_with_a_complete_atomic_handoff(pool):
    value, _ = pool
    slot = spare(pool)
    payload = request()
    assert value.acquire(payload)["session"] == "owned-ui"
    assert json.loads((slot.directory / "request.json").read_text()) == payload
    assert not (slot.directory / "request.tmp").exists()
    assert value.busy
    assert value.acquire(payload) == {"available": False, "reason": "warming"}


@pytest.mark.parametrize("field,new", [
    ("engine", ["foreign-engine"]), ("manager_runtime", "old-version"),
    ("context", {"AGENT_HOME": "foreign-root"}),
    ("handoff", {"bootstrap": {"project": "other", "headless": True}}),
])
def test_foreign_scope_never_consumes_a_spare(pool, field, new):
    value, _ = pool
    slot = spare(pool)
    payload = request()
    payload[field] = new
    assert value.acquire(payload)["reason"] == "scope-mismatch"
    assert slot.claimed is None
    assert not (slot.directory / "request.json").exists()


def test_drain_retires_only_idle_ui_and_preserves_an_active_user(pool):
    value, calls = pool
    active = spare(pool)
    value.acquire(request())
    directory = value.directory / "idle"
    directory.mkdir()
    idle = warm.Slot(directory, "idle-ui", time.monotonic(), incarnation="$1:123")
    value._slots.append(idle)
    value.pause()
    value.pump()
    assert calls == [("kill-session", "-t", "idle-ui")]
    assert value.busy and active.directory.exists()
    assert value.acquire(request())["reason"] == "draining"
    value.resume()
    assert value.acquire(request())["reason"] == "warming"


@pytest.mark.parametrize("attached", [False, True])
def test_final_shutdown_retires_claimed_ui_instead_of_abandoning_it(pool, attached):
    value, calls = pool
    slot = spare(pool)
    value.acquire(request())
    if attached:
        (slot.directory / "attached").touch()
    value.close()
    assert calls == [("kill-session", "-t", "owned-ui")]
    assert not slot.directory.exists() and not value._slots
    assert not value.busy


def test_final_shutdown_refuses_replaced_session_identity(pool, monkeypatch):
    value, calls = pool
    slot = spare(pool)
    value.acquire(request())
    monkeypatch.setattr(value, "_incarnation", lambda _: "$2:456")
    value.close()
    assert not calls and slot.directory.exists()


@pytest.mark.parametrize("end", ["consumed", "expired"])
def test_completed_decision_blocks_graceful_retirement_until_consumed_or_expired(pool, monkeypatch, end):
    from types import SimpleNamespace
    from worktree_manager.mux_daemon import MuxDaemonRuntime

    value, _ = pool
    slot = spare(pool)
    value.acquire(request())
    value.pause()
    runtime = SimpleNamespace(
        server=SimpleNamespace(active_handler_count=lambda: 0),
        handler_tracker=SimpleNamespace(wait_for_drain=lambda _timeout: True),
        _loop_mutation_active=False,
        restart_notifications=SimpleNamespace(drained=True),
        picker_sessions=value,
    )
    assert not MuxDaemonRuntime._is_drained(runtime)
    (slot.directory / "result.json").write_text('{"ok":true,"decision":{"action":"new"}}')
    value.pump()
    assert not MuxDaemonRuntime._is_drained(runtime)
    assert slot.directory.exists()
    if end == "consumed":
        (slot.directory / "consumed").touch()
    else:
        monkeypatch.setattr(warm.time, "monotonic", lambda: slot.completed + warm.RESULT_TIMEOUT + 1)
    value.pump()
    assert MuxDaemonRuntime._is_drained(runtime)
    assert not slot.directory.exists()


def test_replaced_mux_incarnation_cannot_be_admitted(pool, monkeypatch):
    value, calls = pool
    slot = spare(pool)
    monkeypatch.setattr(value, "_incarnation", lambda _: "$2:456")
    assert value.acquire(request())["available"] is False
    assert slot.claimed is None and not (slot.directory / "request.json").exists()
    assert not calls


def test_unavailable_process_birth_identity_disables_prewarming(pool, tmp_path, monkeypatch):
    monkeypatch.setattr(warm, "process_start_time", lambda _pid: None)
    value = warm.WarmPickerPool(tmp_path)
    monkeypatch.setattr(value, "_spawn", lambda: pytest.fail("unverifiable UI was spawned"))
    value.pump()
    value.pump()
    assert value.acquire(request()) == {"available": False, "reason": "unsupported-process-identity"}
    assert not value._slots and not value.directory.exists()


def test_completed_result_survives_until_consumed_then_cleans_up(pool):
    value, calls = pool
    slot = spare(pool)
    value.acquire(request())
    value.pause()
    (slot.directory / "result.json").write_text('{"ok":true,"decision":null}')
    value.pump()
    assert value.busy
    assert slot.directory.exists() and not calls
    (slot.directory / "consumed").touch()
    value.pump()
    assert calls == [("kill-session", "-t", "owned-ui")]
    assert not slot.directory.exists()


def test_cancelled_attachment_reaps_only_its_owned_ui(pool):
    value, calls = pool
    slot = spare(pool)
    value.acquire(request())
    value.pause()
    (slot.directory / "consumed").touch()
    value.pump()
    assert not value.busy
    assert calls == [("kill-session", "-t", "owned-ui")]


def test_unattached_claim_has_a_finite_lifetime(pool, monkeypatch):
    value, calls = pool
    slot = spare(pool)
    value.acquire(request())
    value.pause()
    monkeypatch.setattr(warm.time, "monotonic", lambda: slot.claimed + warm.CLAIM_TIMEOUT + 1)
    value.pump()
    assert not value.busy
    assert calls == [("kill-session", "-t", "owned-ui")]


def test_spawn_failure_is_bounded_and_does_not_break_resident_maintenance(pool, monkeypatch):
    value, _ = pool
    monkeypatch.setattr(value, "_spawn", lambda: (_ for _ in ()).throw(OSError("unavailable mux")))
    assert value.acquire(request())["reason"] == "warming"
    value.pump()
    assert not value.busy
    assert value.acquire(request())["reason"] == "warming"


def test_thin_client_uses_the_selected_provider_generation_and_retained_completion_command(pool, monkeypatch):
    from worktree_manager import runtime_identity

    value, _ = pool
    spare(pool)
    monkeypatch.setattr(runtime_identity, "route_version", lambda: "selected-generation")
    payload = request()
    payload.pop("manager_runtime")
    payload["provider_generation"] = "foreign-generation"
    assert value.acquire(payload)["reason"] == "scope-mismatch"
    payload["provider_generation"] = "selected-generation"
    result = value.acquire(payload)
    assert result["available"]
    assert result["attach_command"][1:] == ["attach-session", "-t", "owned-ui"]
    assert "-I" in result["complete_command"]
    assert result["complete_command"][-9:-6] == ["-m", "worktree_manager", "--resident-result"]


def test_session_replacement_is_never_killed_by_stale_ui_cleanup(pool, monkeypatch):
    value, calls = pool
    slot = spare(pool)
    monkeypatch.setattr(value, "_incarnation", lambda _: "$2:456")
    assert not value.acquire(request())["available"]
    value.pause()
    value.pump()
    assert not calls and slot.directory.exists()
    assert slot in value._slots


def test_unavailable_optional_engine_cannot_break_the_existing_resident(tmp_path, monkeypatch):
    def unavailable():
        raise engine_client.EngineError("selected engine is unavailable")

    monkeypatch.setattr(engine_client, "engine_base_command", unavailable)
    value = warm.WarmPickerPool(tmp_path)
    value.pump()
    assert value.engine is None
    assert not value.busy
    assert value.acquire(request())["available"] is False


@pytest.mark.parametrize("key", [
    "HOME", "USERPROFILE", "PATH", "AGENT_WORKTREES_PICKER_MOCK",
    "AGENT_WORKTREES_PICKER_STREAM", "AGENT_WORKTREES_PICKER_REAL_OPS",
    "AGENT_WORKTREES_PICKER_CACHE_ONLY", "AGENT_WORKTREES_PIVOTS_DIR",
    "AGENT_WORKTREES_SOURCE_DIR", "GIT_CONFIG_GLOBAL",
])
def test_changed_caller_environment_falls_back_without_consuming_ui(pool, monkeypatch, key):
    value, _ = pool
    slot = spare(pool)
    monkeypatch.setenv(key, "different-from-resident")
    assert value.acquire(request())["reason"] == "scope-mismatch"
    assert slot.claimed is None


@pytest.mark.parametrize("cwd", [None, "", "relative-path", 19])
def test_invalid_invocation_cwd_cannot_claim_a_ui(pool, cwd):
    value, _ = pool
    slot = spare(pool)
    payload = {**request(), "cwd": cwd}
    assert value.acquire(payload)["reason"] == "scope-mismatch"
    assert slot.claimed is None


@pytest.mark.parametrize("stale_worker_environment", [False, True])
def test_single_use_worker_enters_caller_cwd_before_binding(
    pool, tmp_path, monkeypatch, stale_worker_environment, worker_ui_imports,
):
    from worktree_manager import resident_picker, warm_picker_worker as worker
    from worktree_manager.production_picker import runner

    resident = tmp_path / "resident"
    caller = tmp_path / "caller"
    resident.mkdir()
    caller.mkdir()
    monkeypatch.chdir(resident)
    directory = tmp_path / "session"
    directory.mkdir()
    payload = {**request(), "cwd": str(caller)}
    payload.update(launch_id="sample-fresh-launch", binstub_started="fixture-start")
    (directory / "request.json").write_text(json.dumps(payload))
    (directory / "attached").touch()
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "stale-mux-session")
    monkeypatch.setenv("GH_TOKEN", "stale-mux-grant")
    if stale_worker_environment:
        monkeypatch.setenv("AGENT_WORKTREES_PICKER_REAL_OPS", "different")
    seen = []
    def consume(args):
        assert os.environ["AGENT_WORKTREES_LAUNCH_ID"] == "sample-fresh-launch"
        assert os.environ["AGENT_WORKTREES_BINSTUB_STARTED"] == "fixture-start"
        return seen.append(os.getcwd()) or ("sample", {})

    monkeypatch.setattr(resident_picker, "consume", consume)
    def run(project, bootstrap_supplier, lifetime_cancelled):
        assert not lifetime_cancelled()
        bootstrap_supplier()
        seen.append(os.getcwd())

    monkeypatch.setattr(runner, "run", run)
    assert worker.main(directory, os.getpid(), str(warm.process_start_time(os.getpid()))) == (
        1 if stale_worker_environment else 0
    )
    assert seen == ([] if stale_worker_environment else [str(caller), str(caller)])
    assert json.loads((directory / "result.json").read_text())["ok"] is not stale_worker_environment
    assert "COPILOT_AGENT_SESSION_ID" not in os.environ
    assert "GH_TOKEN" not in os.environ


@pytest.mark.parametrize("loss", ["dead", "replaced", "cancelled"])
@pytest.mark.parametrize("retained_evidence", [False, True])
def test_owner_loss_after_binding_never_publishes_a_decision(
    pool, monkeypatch, tmp_path, loss, retained_evidence, worker_ui_imports,
):
    from zdd import diagnostics
    from worktree_manager import resident_picker, warm_picker_worker as worker
    from worktree_manager.production_picker import runner

    directory = tmp_path / "session"
    directory.mkdir()
    evidence = directory / "evidence.keep"
    if retained_evidence:
        evidence.write_text("retain unknown files")
    (directory / "request.json").write_text(json.dumps(request()))
    (directory / "attached").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(resident_picker, "consume", lambda _args: ("sample", {}))
    state = {"alive": True, "birth": "first"}
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda _pid: state["alive"])
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _pid: state["birth"])

    def run(project, bootstrap_supplier, lifetime_cancelled):
        bootstrap_supplier()
        assert not lifetime_cancelled()
        if loss == "dead":
            state["alive"] = False
        elif loss == "replaced":
            state["birth"] = "replaced"
        else:
            (directory / "cancel").touch()
        assert lifetime_cancelled()
        return {"action": "new"}

    monkeypatch.setattr(runner, "run", run)
    assert worker.main(directory, os.getpid(), "first") == 130
    assert not (directory / "result.json").exists()
    assert directory.exists() is (retained_evidence or loss == "cancelled")
    if retained_evidence:
        assert evidence.read_text() == "retain unknown files"


def test_private_resident_state_is_not_inherited_by_a_spare(pool):
    from worktree_manager.warm_picker_contract import worker_environment

    clean = worker_environment({
        "HOME": "same-home", "COPILOT_AGENT_SESSION_ID": "old-session",
        "AGENT_WORKTREES_BIND_WORKTREE": "old-worktree", "GH_TOKEN": "old-token",
        "AGENT_WORKTREES_AHP_AUTH_TOKEN": "old-grant", "CONTEXT_HANDOFF_ID": "old-transfer",
        "CE_UPDATE_AUTH": "old-auth",
    })
    assert clean == {"HOME": "same-home"}


def test_engine_and_manager_launch_context_contracts_match(monkeypatch):
    import importlib.util
    from pathlib import Path
    from worktree_manager.warm_picker_contract import invocation_context

    path = Path(__file__).resolve().parents[2] / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "picker_session_entry.py"
    spec = importlib.util.spec_from_file_location("thin_context_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    environment = {"HOME": "caller", "PATH": "caller-bin", "PSMUX_SESSION": "caller-mux"}
    assert module.invocation_context(environment) == invocation_context(environment)
    assert invocation_context(environment) == invocation_context({**environment, "PSMUX_SESSION": "worker-mux"})
    pane_environment = {
        **environment, "TERM": "tmux-256color", "TERM_PROGRAM": "tmux",
        "TERM_PROGRAM_VERSION": "fixture-version", "TMUX": "fixture-transport",
        "TMUX_PANE": "%1",
    }
    assert module.invocation_context(pane_environment) == module.invocation_context(environment)
    assert invocation_context(pane_environment) == invocation_context(environment)
    assert invocation_context({**environment, "GH_TOKEN": "private"}) is None
    assert module.invocation_context({**environment, "GH_TOKEN": "private"}) is None
    assert invocation_context({**environment, "WORKTREE_MANAGER_ROOT": "\0"}) is None
    assert module.invocation_context({**environment, "WORKTREE_MANAGER_ROOT": "\0"}) is None


def test_claim_uses_the_workers_actual_scope_instead_of_the_residents_environment(pool, monkeypatch):
    from zdd.diagnostics import process_start_time

    value, _ = pool
    slot = spare(pool)
    birth = process_start_time(os.getpid())
    if birth is None:
        pytest.skip("the platform has no supported real process-birth identity")
    ready = {
        "pid": os.getpid(), "start": birth,
        "context": value.context,
    }
    (slot.directory / "ready.json").write_text(json.dumps(ready))
    assert warm.WarmPickerPool._worker_ready(value, slot)
    parent_context = dict(value.context)
    monkeypatch.setenv("HOME", "different-home")
    ready["context"] = warm.invocation_context()
    (slot.directory / "ready.json").write_text(json.dumps(ready))
    assert warm.WarmPickerPool._worker_ready(value, slot)
    assert slot.context != parent_context
    assert value.acquire({**request(), "context": parent_context})["reason"] == "scope-mismatch"
    assert value.acquire(request())["available"] is True


def test_absent_launcher_root_uses_selected_provider_default_but_explicit_overrides_stay_foreign(pool, monkeypatch):
    value, _ = pool
    slot = spare(pool)
    root = value.directory.parent.parent
    monkeypatch.delenv("WORKTREE_MANAGER_ROOT")
    payload = {**request(), "context": warm.invocation_context(manager_root=root)}
    assert value.acquire(payload)["available"] is True
    assert slot.claimed is not None
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root / "foreign"))
    assert warm.invocation_context(manager_root=root) != value.context


def test_actual_manager_launcher_root_export_matches_an_unset_project_caller(pool, tmp_path):
    from pathlib import Path
    from agent_procutil import no_window_kwargs
    from _manager_process_fixture import synthetic_runtime
    from worktree_manager import runtime_launchers

    value, _ = pool
    spare(pool)
    version = "0.0.0-fixture"
    synthetic_runtime(
        tmp_path, version,
        program='import os\nprint(os.environ["WORKTREE_MANAGER_ROOT"])\n',
    )
    (tmp_path / "current-version").write_text(version)
    if os.name == "nt":
        import shutil

        shell = shutil.which("pwsh")
        if shell is None:
            pytest.skip("PowerShell is unavailable")
        launcher = tmp_path / "manager.ps1"
        launcher.write_text(runtime_launchers._ps1_binstub(), encoding="utf-8")
        command = [shell, "-NoLogo", "-NoProfile", "-File", str(launcher)]
    else:
        launcher = tmp_path / "manager"
        launcher.write_text(runtime_launchers._sh_binstub(), encoding="utf-8")
        command = ["bash", str(launcher)]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    exported_root = Path(result.stdout.strip())
    caller = dict(os.environ)
    caller.pop("WORKTREE_MANAGER_ROOT", None)
    payload = {**request(), "context": warm.invocation_context(caller, manager_root=exported_root)}
    assert value.acquire(payload)["available"]


def test_one_retirement_refusal_does_not_block_another_completed_sessions_cleanup(pool, monkeypatch):
    value, calls = pool
    refused = spare(pool)
    value.acquire(request())
    (refused.directory / "consumed").touch()
    directory = value.directory / "later"
    directory.mkdir()
    later = warm.Slot(
        directory, "later-ui", time.monotonic(), claimed=time.monotonic(),
        completed=time.monotonic() - warm.RESULT_TIMEOUT - 1, incarnation="$1:123",
    )
    value._slots.append(later)
    (directory / "result.json").write_text('{"ok":true,"decision":null}')
    monkeypatch.setattr(
        value, "_incarnation", lambda slot: "$2:456" if slot is refused else "$1:123",
    )
    value.pause()
    value.pump()
    assert refused in value._slots and refused.directory.exists()
    assert later not in value._slots and not directory.exists()
    assert calls == [("kill-session", "-t", "later-ui")]
    assert not value.busy


def test_selected_nondefault_mux_socket_is_used_for_spawn_probe_cleanup_and_attachment(pool, monkeypatch):
    from worktree_manager.mux_attached_clients import mux_attached_clients
    from worktree_manager.warm_picker_contract import mux_arguments

    value, _ = pool
    socket = "/fixture/tmux-selected.sock"
    value.mux_args = mux_arguments({"TMUX": socket + ",123,0"}, windows=False)
    assert value.mux_args == ["-S", socket]
    assert mux_arguments({}, windows=False) == ["-L", "default"]
    assert mux_arguments({"TMUX": socket + ",123,0"}, windows=True) == []
    commands = []
    monkeypatch.setattr(
        warm.subprocess, "run",
        lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0, stdout=""),
    )
    monkeypatch.setattr(value, "_mux", lambda *args: warm.WarmPickerPool._mux(value, *args))
    assert value.acquire(request())["reason"] == "warming"
    spare(pool)
    value._spawn()
    result = value.acquire(request())
    assert result["attach_command"] == [
        value.mux, "-S", socket, "attach-session", "-t", "owned-ui",
    ]
    value._mux("has-session", "-t", "owned-ui")
    assert mux_attached_clients(value.mux, "owned-ui", mux_args=value.mux_args) == 0
    value.close()
    assert commands and all(command[:3] == [value.mux, "-S", socket] for command in commands)


@pytest.mark.parametrize("owner_lost", [False, True])
def test_legacy_warm_client_uses_the_admitted_socket_command(pool, tmp_path, monkeypatch, owner_lost):
    import sys
    from types import SimpleNamespace
    from work_coalescing_singleton import client
    from zdd import routing
    from worktree_manager import warm_picker_client

    value, _ = pool
    slot = spare(pool)
    value.mux_args = ["-S", "/fixture/tmux-selected.sock"]
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(engine_client, "inherited_engine_command", lambda: ["qualified-engine"])
    monkeypatch.setattr(
        routing, "read_active_endpoint",
        lambda *_a, **_k: SimpleNamespace(client_host="127.0.0.1", port=12345),
    )
    (tmp_path / "mux-daemon.token").write_text("fixture-token")

    def admit(*_args, payload, **_kwargs):
        result = value.acquire(payload)
        (slot.directory / "result.json").write_text('{"ok":true,"decision":null}')
        return result

    commands = []
    released = []
    monkeypatch.setattr(client, "request", admit)
    monkeypatch.setattr(client, "release", lambda *_a, **_k: released.append(True))

    def attach(command, **kwargs):
        commands.append(command)
        if owner_lost:
            warm.shutil.rmtree(slot.directory)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(warm.subprocess, "run", attach)
    handoff = {"bootstrap": {"project": "sample", "headless": False}}
    if owner_lost:
        with pytest.raises(RuntimeError, match="detached without a completed decision"):
            warm_picker_client.run(handoff)
    else:
        assert warm_picker_client.run(handoff) == (True, None)
    assert released == [True]
    assert commands == [[value.mux, "-S", "/fixture/tmux-selected.sock", "attach-session", "-t", slot.session]]


def test_generated_launcher_trace_identity_does_not_disqualify_a_spare(pool, monkeypatch):
    value, _ = pool
    slot = spare(pool)
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_ID", "sample-fresh-launch")
    monkeypatch.setenv("AGENT_WORKTREES_BINSTUB_STARTED", "fixture-start")
    payload = {**request(), "launch_id": "sample-fresh-launch", "binstub_started": "fixture-start"}
    assert value.acquire(payload)["available"]
    assert json.loads((slot.directory / "request.json").read_text())["launch_id"] == "sample-fresh-launch"


@pytest.mark.parametrize("retained", [False, True])
def test_runtime_commands_ignore_conflicting_python_paths(tmp_path, monkeypatch, retained):
    import sys
    from agent_procutil import no_window_kwargs
    from worktree_manager import warm_picker_contract as contract

    foreign = tmp_path / "foreign"
    package = foreign / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('foreign Manager imported')")
    environment = {
        **os.environ, "PYTHONPATH": str(foreign), "PYTHONHOME": str(tmp_path / "missing-python"),
    }
    if retained:
        prefix = tmp_path / "versions" / "retained"
        bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
        bootstrap.parent.mkdir(parents=True)
        bootstrap.write_text(
            "import json, sys\n"
            "assert sys.flags.isolated and sys.flags.no_site\n"
            "print(json.dumps(sys.argv[1:]))\n"
        )
        (prefix / ".install-complete.json").write_text("{}")
        monkeypatch.setattr(sys, "prefix", str(prefix))
    command = contract.runtime_command("worktree_manager", "--version")
    completed = subprocess.run(
        command, cwd=foreign, env=environment, capture_output=True, text=True,
        timeout=20, **no_window_kwargs(),
    )
    assert completed.returncode == 0, completed.stderr
    if retained:
        assert json.loads(completed.stdout) == ["-m", "worktree_manager", "--version"]
    else:
        assert completed.stdout.startswith("worktree-manager ")


def test_spare_launch_uses_isolation_before_importing_worker_module(pool, monkeypatch):
    import base64

    value, _ = pool
    commands = []
    monkeypatch.setattr(
        warm.subprocess, "run",
        lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0),
    )
    assert value.acquire(request())["reason"] == "warming"
    value._spawn()
    command = commands[0]
    script = (
        base64.b64decode(command[-1]).decode("utf-16-le") if os.name == "nt" else command[-1]
    )
    assert "-I" in script and "worktree_manager.warm_picker_worker" in script
    assert script.index("-I") < script.index("worktree_manager.warm_picker_worker")
    value.close()


def test_unconstructed_retained_slot_cannot_spawn_or_consume_a_spare(pool, tmp_path, monkeypatch):
    import sys

    value, _ = pool
    slot = spare(pool)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "versions" / "unconstructed"))
    with pytest.raises(RuntimeError, match="constructed Manager runtime"):
        value.acquire(request())
    assert slot.claimed is None
    assert not (slot.directory / "request.json").exists()
    with pytest.raises(RuntimeError, match="constructed Manager runtime"):
        value._spawn()
    assert value._slots == [slot]
