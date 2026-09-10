"""Guards for the wholesale production Picker transplant."""

from __future__ import annotations

import importlib.util
import json
import binascii
import zlib
from pathlib import Path

import pytest

from worktree_manager import __main__ as entrypoint
from worktree_manager.production_picker import runner
from worktree_manager.production_picker import _engine_runtime as engine_runtime


def test_transplanted_picker_sources_match_production_copy():
    root = Path(__file__).resolve().parents[2]
    source = root / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
    transplanted = (
        root / "worktree-manager" / "src" / "worktree_manager" / "production_picker"
    )
    relative_paths = [
        Path("picker.py"),
        *(
            path.relative_to(source)
            for path in sorted((source / "picker_tui").glob("*.py"))
            # These are Manager-owned process-boundary adapters, not source
            # copies. All other transplanted modules remain byte-identical.
            if path.name not in {
                "data_local.py",
                "data_ssh.py",
                "engine.py",
                "maintenance.py",
                "pivots.py",
            }
        ),
    ]

    assert relative_paths
    for relative in relative_paths:
        assert (transplanted / relative).read_bytes() == (source / relative).read_bytes()
    assert "WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE" in (
        transplanted / "picker_tui" / "pivots.py"
    ).read_text(encoding="utf-8")


def test_production_runner_activates_project_and_uses_transplanted_ui(monkeypatch):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    class Config:
        @staticmethod
        def set_active_project(project):
            calls.append(("active", project))

    class Cli:
        @staticmethod
        def _resolve_active_project(project):
            return project, None

        @staticmethod
        def _in_ssh_session():
            return False

        @staticmethod
        def _heal_stale_anchor_if_self_missing(config):
            calls.append(("heal", config))
            return config

        @staticmethod
        def reap_orphan_mux_sessions():
            calls.append(("reap",))

        @staticmethod
        def _sweep_managed_on_exit():
            calls.append(("managed",))

        @staticmethod
        def _sweep_launcher_shells_on_exit():
            calls.append(("shells",))

        @staticmethod
        def _sweep_finished_sessions_on_cadence():
            calls.append(("finished",))

        @staticmethod
        def _start_picker_monitor_root():
            return None

    Config.load_config = staticmethod(lambda: "config")

    monkeypatch.setattr(
        runner,
        "engine_module",
        lambda name: Config if name == "config" else Cli,
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live: calls.append(("picker", live)) or {"action": "new"},
    )
    monkeypatch.setattr(runner.threading, "Thread", ImmediateThread)

    assert runner.run("demo") == {"action": "new"}
    assert calls == [
        ("active", "demo"),
        ("heal", "config"),
        ("reap",),
        ("managed",),
        ("shells",),
        ("finished",),
        ("picker", True),
    ]


def test_engine_runtime_prefers_explicit_context_over_checkout(monkeypatch, tmp_path):
    root = (
        tmp_path
        / "durable"
        / "marketplaces"
        / "cell-a"
        / "plugins"
        / "agent-worktrees"
    )
    slot = root / "versions" / "1.2.3"
    source = (
        slot / "Lib" / "site-packages"
        if engine_runtime.os.name == "nt"
        else slot / "lib" / "python3.10" / "site-packages"
    )
    package = source / "agent_worktrees"
    package.mkdir(parents=True)
    (root / "current-version").write_text("1.2.3", encoding="utf-8")
    (root / "install.json").write_text(
        json.dumps({"pluginId": "agent-worktrees"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(root / "install.json"))
    monkeypatch.delenv(engine_runtime.ENGINE_SOURCE_ENV, raising=False)
    monkeypatch.setattr(engine_runtime, "_checkout_source", lambda: tmp_path / "checkout")

    assert engine_runtime._active_runtime_source() == source


def test_engine_runtime_rejects_foreign_explicit_context(monkeypatch, tmp_path):
    install = tmp_path / "install.json"
    install.write_text(json.dumps({"pluginId": "agent-bridge"}), encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(install))

    with pytest.raises(engine_runtime.EngineRuntimeError, match="does not own agent-worktrees"):
        engine_runtime.ensure_engine_runtime()


def test_production_runner_mock_skips_mutating_startup(monkeypatch):
    calls = []

    class Config:
        @staticmethod
        def set_active_project(project):
            calls.append(("active", project))

        @staticmethod
        def load_config():
            return "config"

    class Cli:
        @staticmethod
        def _resolve_active_project(project):
            return project, None

        @staticmethod
        def _in_ssh_session():
            return False

        @staticmethod
        def _heal_stale_anchor_if_self_missing(config):
            calls.append(("heal", config))

        @staticmethod
        def _start_picker_monitor_root():
            calls.append(("monitor",))
            return None

    monkeypatch.setattr(
        runner,
        "engine_module",
        lambda name: Config if name == "config" else Cli,
    )
    monkeypatch.setattr(
        runner,
        "_start_housekeeping",
        lambda cli: calls.append(("housekeeping",)),
    )
    monkeypatch.setattr(
        runner,
        "run_tui_picker",
        lambda *, live, mock_mode: calls.append(("picker", live, mock_mode))
        or None,
    )

    assert runner.run("demo", mock_mode=True, local=True) is None
    assert calls == [
        ("active", "demo"),
        ("picker", False, True),
    ]


def test_production_capture_uses_read_only_prepare(monkeypatch):
    from worktree_manager.production_picker.picker_tui import capture as picker_capture

    calls = []
    monkeypatch.setattr(
        runner,
        "_prepare",
        lambda project, *, heal: calls.append((project, heal)) or (object(), False),
    )
    monkeypatch.setattr(
        picker_capture,
        "capture",
        lambda source, **kwargs: {
            "text": "GRID\n",
            "ansi": "ANSI\n",
            "svg": "<svg />",
        },
    )

    assert runner.capture("demo")["text"] == "GRID\n"
    assert calls == [("demo", False)]


def test_manager_acts_on_production_picker_new_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"no_mux": False},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 23,
    )

    assert entrypoint._run_production_picker("demo") == 23
    assert requests[0].project == "demo"
    assert requests[0].mode == "new"
    assert requests[0].worktree_id is None
    assert requests[0].no_mux is False


def test_manager_acts_on_production_picker_resume_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume me",
            "is_local": True,
            "options": {"bare_resume": True, "no_mux": True, "ahp": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "bare-resume"
    assert requests[0].no_mux is True


def test_manager_restores_local_session_then_uses_common_launch_gate(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "title": "Restore me",
            "is_local": True,
        },
    )
    from worktree_manager import engine_client

    calls = []
    monkeypatch.setattr(
        engine_client, "project_binstub_command", lambda project: Path("demo.cmd")
    )
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda project, args, **kwargs: (
            calls.append(("remux", project, args, kwargs))
            or {"ok": True, "action": "reclaimed", "requires_resume": True}
        ),
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert calls[0][0:3] == (
        "remux",
        "demo",
        ["remux", "--worktree-id", "demo-1234", "--yes", "--json"],
    )
    assert requests[0].project == "demo"
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].mode == "resume"


def test_manager_does_not_launch_when_restore_prep_fails(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "is_local": True,
        },
    )
    from worktree_manager import engine_client

    monkeypatch.setattr(
        engine_client, "project_binstub_command", lambda project: Path("demo.cmd")
    )
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda *args, **kwargs: {"ok": False, "reason": "ambiguous owner"},
    )
    monkeypatch.setattr(
        engine_client,
        "run_project_passthrough",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("launch must not run")
        ),
    )

    assert entrypoint._run_production_picker("demo") == 1


def test_manager_does_not_launch_unverified_live_adoption(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "is_local": True,
        },
    )
    from worktree_manager import engine_client

    monkeypatch.setattr(
        engine_client, "project_binstub_command", lambda project: Path("demo.cmd")
    )
    monkeypatch.setattr(
        engine_client,
        "run_json",
        lambda *args, **kwargs: {
            "ok": True,
            "action": "adopted",
            "verified": False,
        },
    )
    monkeypatch.setattr(
        engine_client,
        "run_project_passthrough",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("launch must not run")
        ),
    )

    assert entrypoint._run_production_picker("demo") == 1


def test_manager_restores_remote_session_then_resumes(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "title": "Restore remotely",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
        },
    )
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    monkeypatch.setattr(
        data_ssh,
        "remote_op_argv",
        lambda *args, **kwargs: ["ssh", "example", "demo remux"],
    )
    monkeypatch.setattr(
        maintenance,
        "_ssh_json",
        lambda argv: {"ok": True, "verified": True},
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].worktree_id == "demo-1234"
    assert requests[0].machine == "Example"
    assert requests[0].environment == "WSL"


def test_manager_reports_remote_restore_transport_failure(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "restore",
            "worktree_id": "demo-1234",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
        },
    )
    from worktree_manager.production_picker.picker_tui import data_ssh, maintenance

    monkeypatch.setattr(
        data_ssh,
        "remote_op_argv",
        lambda *args, **kwargs: ["ssh", "example", "demo remux"],
    )
    monkeypatch.setattr(
        maintenance,
        "_ssh_json",
        lambda argv: (_ for _ in ()).throw(FileNotFoundError("ssh missing")),
    )

    assert entrypoint._run_production_picker("demo") == 1


def test_manager_rejects_crafted_remote_ahp_picker_decision(monkeypatch, capsys):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "resume",
            "worktree_id": "demo-1234",
            "title": "Resume remotely",
            "is_local": False,
            "machine": "Example",
            "env": "WSL",
            "options": {"bare_resume": True, "no_mux": True, "ahp": True},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 1
    assert requests == []
    assert "same-machine" in capsys.readouterr().out


def test_manager_acts_on_base_repo_production_picker_decision(monkeypatch):
    monkeypatch.setattr(
        runner,
        "run",
        lambda project: {
            "action": "new",
            "is_local": True,
            "options": {"anchor": True, "no_mux": False},
        },
    )
    requests = []
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: requests.append(request) or 0,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert requests[0].mode == "base"
    assert requests[0].worktree_id is None


def test_run_launch_executes_remote_plan(monkeypatch):
    plan = type("Plan", (), {"action": "remote", "exit_code": 0})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    calls = []
    from worktree_manager import launcher

    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 17,
    )
    request = type("Request", (), {"no_mux": True})()

    assert entrypoint._run_launch(request) == 17
    assert calls == [(plan, False)]


def test_run_launch_rejects_crafted_remote_ahp_before_launch(
    monkeypatch,
):
    from worktree_manager import engine_client, launcher

    plan = type("Plan", (), {"action": "remote", "exit_code": 0})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("remote launch must not inspect local execution-leg state")
        ),
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": "Example",
            "environment": "WSL",
            "no_mux": False,
            "ahp": True,
        },
    )()

    assert entrypoint._run_launch(request) == 1
    assert calls == []


def test_run_launch_selects_ahp_after_plan_resolution(monkeypatch, tmp_path):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
            "work_dir": str(tmp_path),
        },
    )()
    hosted = object()
    attachment = object()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {"execution_leg": None},
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda project, worktree_id, work_dir: (
            attachment
            if (project, worktree_id, work_dir)
            == ("demo", "demo-1234", str(tmp_path))
            else None
        ),
    )
    monkeypatch.setattr(ahp_provider, "attach_plan", lambda value, found: hosted)
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": True,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(hosted, True)]


def test_run_launch_default_never_calls_ahp(monkeypatch):
    from worktree_manager import ahp_provider, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda request: (plan, 0))
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(AssertionError("AHP is opt-in")),
    )
    monkeypatch.setattr(launcher, "launch", lambda *_args, **_kwargs: 0)
    request = type("Request", (), {"no_mux": False, "ahp": False})()

    assert entrypoint._run_launch(request) == 0


@pytest.mark.parametrize("state", ["active", "unknown"])
def test_run_launch_forces_persisted_active_or_unknown_ahp_binding(
    monkeypatch,
    tmp_path,
    state,
):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
            "work_dir": str(tmp_path),
        },
    )()
    attachment = object()
    hosted = object()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "ahp",
                "state": state,
                "binding_revision": 3,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args, **_kwargs: attachment,
    )
    monkeypatch.setattr(
        ahp_provider,
        "attach_plan",
        lambda value, found: hosted
        if (value, found) == (plan, attachment)
        else None,
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(hosted, True)]


def test_run_launch_allows_direct_after_disposed_binding(monkeypatch):
    from worktree_manager import ahp_provider, engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "ahp",
                "state": "disposed",
                "binding_revision": 4,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        ahp_provider,
        "ensure_session",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("disposed binding does not force AHP")
        ),
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(plan, True)]


@pytest.mark.parametrize("mode", ["resume", "bare-resume"])
def test_run_launch_preserves_direct_resume_when_execution_leg_is_unsupported(
    monkeypatch,
    mode,
):
    from worktree_manager import engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    calls = []
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda resolved, *, want_mux: calls.append((resolved, want_mux)) or 0,
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": mode,
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 0
    assert calls == [(plan, True)]


def test_run_launch_fails_closed_for_unknown_unavailable_provider(
    monkeypatch,
    capsys,
):
    from worktree_manager import engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "future-host",
                "state": "unknown",
                "binding_revision": 9,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not launch direct")
        ),
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 1
    assert "requires unsupported execution-leg provider future-host" in capsys.readouterr().out


def test_run_launch_rejects_bare_resume_for_active_ahp_binding(
    monkeypatch,
    capsys,
):
    from worktree_manager import engine_client, launcher

    plan = type(
        "Plan",
        (),
        {
            "action": "exec",
            "exit_code": 0,
            "worktree_id": "demo-1234",
        },
    )()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    monkeypatch.setattr(
        engine_client,
        "execution_leg_get",
        lambda *_args, **_kwargs: {
            "execution_leg": {
                "provider": "ahp",
                "state": "active",
                "binding_revision": 3,
                "blob": {},
            }
        },
    )
    monkeypatch.setattr(
        launcher,
        "launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bare resume must fail closed")
        ),
    )
    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "bare-resume",
            "machine": None,
            "no_mux": False,
            "ahp": False,
        },
    )()

    assert entrypoint._run_launch(request) == 1
    assert "bare resume is incompatible" in capsys.readouterr().out


def test_production_picker_disposal_decision_calls_manager_owned_provider(
    monkeypatch,
    tmp_path,
):
    from worktree_manager import ahp_provider

    monkeypatch.setattr(
        runner,
        "run",
        lambda _project: {
            "action": "dispose-hosted-session",
            "worktree_id": "demo-1234",
            "is_local": True,
        },
    )
    plan = type("Plan", (), {"work_dir": str(tmp_path)})()
    monkeypatch.setattr(entrypoint, "_resolve_for", lambda _request: (plan, 0))
    calls = []
    monkeypatch.setattr(
        ahp_provider,
        "dispose_worktree_session",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert entrypoint._run_production_picker("demo") == 0
    assert calls[0][0] == ("demo", "demo-1234", str(tmp_path))


def test_resolve_for_uses_remote_compatibility_on_older_engine(monkeypatch):
    from worktree_manager import engine_client

    request = type(
        "Request",
        (),
        {
            "project": "demo",
            "worktree_id": "demo-1234",
            "mode": "bare-resume",
            "machine": "Example",
            "environment": "WSL",
            "no_mux": True,
        },
    )()
    monkeypatch.setattr(
        engine_client,
        "resolve_launch_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            engine_client.EngineFeatureUnavailable("older engine")
        ),
    )
    monkeypatch.setattr(
        runner,
        "compatibility_remote_plan",
        lambda *args, **kwargs: {
            "action": "remote",
            "ssh_alias": "example-wsl",
            "remote_command": "demo --worktree-id demo-1234 --bare-resume --no-mux",
        },
    )

    plan, code = entrypoint._resolve_for(request)
    assert code == 0
    assert plan.action == "remote"
    assert plan.raw["ssh_alias"] == "example-wsl"


def test_remote_compatibility_rejects_shell_metacharacters():
    with pytest.raises(RuntimeError, match="unsafe remote launch token"):
        runner._remote_command(["demo;Remove-Item", "--new"])


def test_normal_picker_command_uses_production_transplant(monkeypatch):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(entrypoint, "build_projects", lambda: [])
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run_production_picker",
        lambda project: calls.append(project) or 19,
    )

    assert entrypoint._cmd_picker(["demo"]) == 19
    assert calls == ["demo"]


def test_picker_mock_uses_production_transplant_without_acting(monkeypatch, capsys):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "run",
        lambda project, **kwargs: calls.append((project, kwargs))
        or {"action": "new"},
    )
    monkeypatch.setattr(
        entrypoint,
        "_run_launch",
        lambda request: (_ for _ in ()).throw(
            AssertionError("mock mode must not launch")
        ),
    )

    assert entrypoint._cmd_picker(["mock", "demo", "--local", "--json"]) == 0
    assert calls == [("demo", {"mock_mode": True, "local": True})]
    assert json.loads(capsys.readouterr().out) == {
        "mock": True,
        "decision": {"action": "new"},
    }


def test_picker_screenshot_uses_production_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "capture",
        lambda project, **kwargs: calls.append((project, kwargs))
        or {"text": "GRID\n", "ansi": "ANSI\n", "svg": "<svg />"},
    )
    out = tmp_path / "picker.txt"

    assert entrypoint._cmd_picker([
        "screenshot",
        "demo",
        "--format",
        "text",
        "--pivot",
        "Tasks",
        "--wait",
        "1.5",
        "--out",
        str(out),
    ]) == 0
    assert calls == [(
        "demo",
        {"live": False, "pivot": "Tasks", "wait_pivot": 1.5},
    )]
    assert out.read_text(encoding="utf-8") == "GRID\n"


def test_picker_screenshot_keeps_relative_output_at_caller_cwd(monkeypatch, tmp_path):
    caller = tmp_path / "caller"
    project = tmp_path / "project"
    caller.mkdir()
    project.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(entrypoint, "engine_available", lambda: False)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )

    def capture(*args, **kwargs):
        monkeypatch.chdir(project)
        return {"text": "GRID\n", "ansi": "ANSI\n", "svg": "<svg />"}

    monkeypatch.setattr(runner, "capture", capture)

    assert entrypoint._cmd_picker([
        "screenshot",
        "demo",
        "--format",
        "text",
        "--out",
        "picker.txt",
    ]) == 0
    assert (caller / "picker.txt").read_text(encoding="utf-8") == "GRID\n"
    assert not (project / "picker.txt").exists()


def test_legacy_screenshot_flag_uses_production_capture(monkeypatch, tmp_path):
    monkeypatch.setattr(entrypoint, "engine_available", lambda: True)
    monkeypatch.setattr(
        entrypoint,
        "build_projects",
        lambda: [type("Project", (), {"name": "demo"})()],
    )
    monkeypatch.setattr(
        runner,
        "capture",
        lambda project, **kwargs: {
            "text": "GRID\n",
            "ansi": "ANSI\n",
            "svg": "<svg>production</svg>",
        },
    )
    out = tmp_path / "picker.svg"

    assert entrypoint._cmd_picker(["demo", "--screenshot", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "<svg>production</svg>"


def test_picker_validation_assets_are_manager_owned():
    root = Path(__file__).resolve().parents[2]
    manager = root / "worktree-manager"
    corpus = manager / "tests" / "production_picker"
    snapshots = manager / "scripts" / "picker-snapshot"

    assert (corpus / "test_picker_tui.py").is_file()
    assert (corpus / "goldens" / "picker" / "worktrees_list.txt").is_file()
    assert (manager / "scripts" / "preview-picker.ps1").is_file()
    assert (manager / "scripts" / "preview-picker.sh").is_file()
    assert (manager / "scripts" / "picker-shot.py").is_file()
    assert (snapshots / "render.py").is_file()
    assert (snapshots / "svg2png.mjs").is_file()
    assert "worktree_manager.production_picker" in (
        snapshots / "render.py"
    ).read_text(encoding="utf-8")


def _picker_shot_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "picker-shot.py"
    spec = importlib.util.spec_from_file_location("manager_picker_shot", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        len(data).to_bytes(4, "big")
        + chunk_type
        + data
        + crc.to_bytes(4, "big")
    )


def _test_png(width: int, height: int) -> bytes:
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, 6, 0, 0, 0])
    )
    pixels = b"".join(b"\x00" + bytes(width * 4) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(pixels))
        + _png_chunk(b"IEND", b"")
    )


def test_png_validator_accepts_expected_signature_and_dimensions(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2))

    module._validate_png(str(png), expected_size=(4, 2))


def test_png_validator_rejects_wrong_dimensions(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2))

    with pytest.raises(RuntimeError, match="expected 8x4"):
        module._validate_png(str(png), expected_size=(8, 4))


def test_png_validator_rejects_truncated_png(tmp_path):
    module = _picker_shot_module()
    png = tmp_path / "picker.png"
    png.write_bytes(_test_png(4, 2)[:24])

    with pytest.raises(RuntimeError, match="invalid PNG"):
        module._validate_png(str(png), expected_size=(4, 2))
