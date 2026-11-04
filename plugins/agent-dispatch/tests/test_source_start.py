from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != "nt", reason="equivalent native Windows path spellings")
@pytest.mark.parametrize("extended_prefix", [False, True])
@pytest.mark.parametrize("extended_module", [False, True])
def test_source_start_accepts_same_physical_windows_namespace(
    tmp_path, monkeypatch, extended_prefix, extended_module,
):
    from agent_dispatch import source_start

    version = f"1.2.3+source.{'a' * 40}.{'b' * 16}.profile.{'c' * 16}"
    root = tmp_path / "app"
    slot = root / "versions" / version
    prefix = slot / ".venv"
    prefix.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "versioned_runtime.py").write_text(
        "def is_complete(root, version):\n    return True\n", encoding="utf-8",
    )
    (root / "current-version").write_text(version, encoding="utf-8")
    module = prefix / "source_start.py"
    module.write_text("", encoding="utf-8")
    monkeypatch.setattr(source_start, "__version__", version)
    monkeypatch.setattr(source_start.sys, "prefix", "\\\\?\\" + str(prefix) if extended_prefix else str(prefix))
    monkeypatch.setattr(source_start, "__file__", "\\\\?\\" + str(module) if extended_module else str(module))
    monkeypatch.setattr(source_start, "install_dir", lambda: root)
    assert source_start.require_selected_source().samefile(root)
    foreign = tmp_path / "foreign-source.py"
    foreign.write_text("", encoding="utf-8")
    monkeypatch.setattr(source_start, "__file__", "\\\\?\\" + str(foreign) if extended_module else str(foreign))
    with pytest.raises(ValueError, match="selection"):
        source_start.require_selected_source()


def test_source_start_parser_is_strict():
    from agent_dispatch.__main__ import build_parser
    from agent_dispatch.source_start import command

    parser = build_parser()
    assert parser.parse_args(["__source-start"]).func is command
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["__source-start", "--force"])
    assert exc.value.code == 2


def test_source_start_refuses_checkout_before_any_service_effect(monkeypatch):
    from agent_dispatch import source_start

    monkeypatch.setattr(source_start, "__version__", "1.2.3")
    with pytest.raises(ValueError, match="physical selected Source"):
        source_start.require_selected_source()


@pytest.mark.parametrize("selected", [False, True])
def test_source_start_requires_completed_selected_physical_generation(tmp_path, monkeypatch, selected):
    from agent_dispatch import source_start
    from agent_procutil import _daemon_instance_identity

    version = f"1.2.3+source.{'a' * 40}.{'b' * 16}.profile.{'c' * 16}"
    root = tmp_path / "app"
    slot = root / "versions" / version
    prefix = slot / ".venv"
    prefix.mkdir(parents=True)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "versioned_runtime.py").write_text(
        "def is_complete(root, version):\n    return False\n", encoding="utf-8",
    )
    (root / "current-version").write_text(version if selected else "another", encoding="utf-8")
    monkeypatch.setattr(source_start, "__version__", version)
    monkeypatch.setattr(source_start.sys, "prefix", str(prefix))
    monkeypatch.setattr(source_start, "__file__", str(prefix / "source_start.py"))
    monkeypatch.setattr(source_start, "install_dir", lambda: root)
    monkeypatch.setattr(_daemon_instance_identity, "runtime_slot_paths", lambda _: (
        version, str(slot), str(root),
    ))
    with pytest.raises(ValueError, match="complete|selection"):
        source_start.require_selected_source()


def test_source_start_callback_never_deploys_or_succeeds_after_failed_coordinator(monkeypatch):
    from agent_dispatch import coordinator_cli, source_start

    calls = []
    monkeypatch.setattr(source_start, "require_selected_source", lambda: Path("app"))
    monkeypatch.setattr(source_start, "supervisor_plan", lambda root: None)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda args, **kwargs: (
        calls.append(kwargs) or 1
    ))
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda root: pytest.fail("failed coordinator"))
    assert source_start.command(argparse.Namespace(json=True)) == 1
    assert len(calls) == 1
    assert calls[0]["source_replacement"] is True
    assert calls[0]["source_environment"]["AGENT_DISPATCH_INSTALL_DIR"] == "app"


def test_source_start_callback_reports_supervisor_failure_as_failure(monkeypatch):
    from agent_dispatch import coordinator_cli, source_start

    monkeypatch.setattr(source_start, "require_selected_source", lambda: Path("app"))
    monkeypatch.setattr(source_start, "supervisor_plan", lambda root: None)
    monkeypatch.setattr(coordinator_cli, "_cmd_cutover", lambda args, **kwargs: 0)
    monkeypatch.setattr(source_start, "ensure_supervisor", lambda root, **kwargs: (
        (_ for _ in ()).throw(RuntimeError("native supervisor readiness unconfirmed"))
    ))
    with pytest.raises(RuntimeError, match="readiness unconfirmed"):
        source_start.command(argparse.Namespace(json=True))


@pytest.mark.parametrize("changed", ["version", "birth", "config", "pid"])
def test_source_readiness_binds_generation_native_birth_and_configuration(monkeypatch, changed):
    from agent_dispatch import source_start
    from zdd import diagnostics

    payload = {
        "version": "source-a", "status": "ok", "runtime_config_identity": "config-a",
        "slot": {"pid": 41, "process_start_time": "birth-a", "role": "passive"},
    }
    if changed == "version":
        payload["version"] = "source-b"
    elif changed == "birth":
        payload["slot"]["process_start_time"] = "birth-b"
    elif changed == "config":
        payload["runtime_config_identity"] = "config-b"
    else:
        payload["slot"]["pid"] = 42
    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "birth-a")
    handle = SimpleNamespace(pid=41, poll=lambda: None)
    assert not source_start.accept_readiness(
        payload, handle=handle, version="source-a", config_identity="config-a",
    )


def test_source_readiness_accepts_only_live_pinned_selected_generation(monkeypatch):
    from agent_dispatch import source_start
    from zdd import diagnostics

    monkeypatch.setattr(diagnostics, "process_start_time", lambda _: "birth-a")
    handle = SimpleNamespace(pid=41, poll=lambda: None)
    payload = {
        "version": "source-a", "status": "ok", "runtime_config_identity": "config-a",
        "slot": {"pid": 41, "process_start_time": "birth-a", "role": "active"},
    }
    assert source_start.accept_readiness(
        payload, handle=handle, version="source-a", config_identity="config-a", active=True,
    )
    handle.poll = lambda: 0
    assert not source_start.accept_readiness(
        payload, handle=handle, version="source-a", config_identity="config-a", active=True,
    )


@pytest.mark.parametrize("configured", [False, True])
def test_source_supervisor_never_treats_unknown_declarations_as_disabled(tmp_path, monkeypatch, configured):
    from agent_dispatch import pool_source_snapshot, source_start

    monkeypatch.setattr(pool_source_snapshot, "read_supervisor_declarations", lambda _: (
        pool_source_snapshot.DeclarationRead((), configured, "unconfirmed" if not configured else "")
    ))
    if configured:
        assert source_start.ensure_supervisor(tmp_path) == {"status": "unconfigured"}
    else:
        with pytest.raises(RuntimeError, match="declaration source is unconfirmed"):
            source_start.ensure_supervisor(tmp_path)


@pytest.mark.parametrize("ready,stale", [(False, False), (True, False), (True, True)])
def test_source_coordinator_uses_actual_guarded_publication_and_reuses_owner(
    tmp_path, monkeypatch, ready, stale,
):
    import os
    from agent_dispatch import client, source_start
    from agent_dispatch.config import Config
    from zdd import diagnostics, routing

    cfg = Config(host="127.0.0.1", db_path=str(tmp_path / "tasks.db"))
    identity = source_start.config_identity(cfg)
    birth = diagnostics.process_start_time(os.getpid())
    assert birth
    monkeypatch.setattr(source_start, "require_selected_source", lambda: tmp_path)
    spawned = []

    class Handle:
        pid = os.getpid()
        exited = False

        def poll(self):
            return 0 if self.exited else None

        def terminate(self):
            self.exited = True

    handle = Handle()
    if stale:
        routing.publish_active(
            tmp_path, bind="127.0.0.1", port=43101, pid=handle.pid,
            version=source_start.__version__, process_start_time=birth,
        )
        monkeypatch.setattr(source_start, "_route_owner_state", lambda owner: (
            "dead" if owner.port == 43101 else "matching"
        ))
        monkeypatch.setattr(routing, "_listening", lambda *_args, **_kwargs: False)

    def spawn(port):
        spawned.append(port)
        return handle

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def health(self):
            active = (routing.read_table(tmp_path) or {}).get("active")
            return {
                "status": "ok", "version": source_start.__version__,
                "runtime_config_identity": identity if ready else "wrong-config",
                "slot": {
                    "pid": handle.pid, "process_start_time": birth,
                    "role": "active" if active else "passive",
                },
            }

    monkeypatch.setattr(client, "DispatchClient", Client)
    result = source_start.run_coordinator(
        cfg, tmp_path, token=None, spawn_passive=spawn, pick_free_port=lambda: 43123, timeout=0,
    )
    assert result["ok"] is ready
    if not ready:
        assert handle.exited
        assert (routing.read_table(tmp_path) or {}).get("active") is None
    else:
        active = routing.read_table(tmp_path)["active"]
        assert active["pid"] == os.getpid()
        assert active["process_start_time"] == birth
        repeated = source_start.run_coordinator(
            cfg, tmp_path, token=None, spawn_passive=spawn, pick_free_port=lambda: 43124, timeout=0,
        )
        assert repeated["status"] == "reused"
        assert spawned == [43123]
