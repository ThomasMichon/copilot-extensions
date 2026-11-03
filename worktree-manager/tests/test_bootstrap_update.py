"""The bootstrap request completes only after its full update participant."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import pytest

from update_driver import core
from worktree_manager import self_install, update_driver_cli

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_update.py"
sys.path.insert(0, str(SCRIPT.parent))


@pytest.fixture(autouse=True)
def retained_inputs(tmp_path, monkeypatch):
    import bootstrap_inputs

    monkeypatch.setattr(bootstrap_inputs, "retain", lambda payload, root, **kwargs: (payload, True))


@pytest.mark.parametrize("exit_code", [0, 23])
@pytest.mark.parametrize("automatic", [False, True])
def test_bootstrap_update_journals_scope_and_waits_for_the_whole_result(
    tmp_path, monkeypatch, capsys, exit_code, automatic,
):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    payload, root = tmp_path / "source", tmp_path / "manager"
    payload.mkdir()
    seen = {}
    events = []
    command = [
        "--", "update", "--supervised-debug", "--project", "sample",
        *(["--auto"] if automatic else []),
    ]
    request = SimpleNamespace(id="fixture", log_path=tmp_path / "update.log")

    def enqueue(spec, *, runner, coalesce_running):
        assert coalesce_running is False
        seen.update(spec=spec, runner=runner)
        return request

    def drain():
        events.extend(["manager-built", "suite-finished"])

    def wait(identifier, *, output, worker):
        assert identifier == request.id
        assert worker is None
        assert events == ["manager-built", "suite-finished"]
        return SimpleNamespace(state="completed", exit_code=exit_code)

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=enqueue, drain=drain, wait=wait, default_root=lambda: tmp_path,
    )
    args = SimpleNamespace(
        payload=payload, root=root, command=command, repair_update=True, install_source=None,
    )
    assert execute(args, driver, source_digest="a" * 64) == exit_code
    spec, runner = seen["spec"], seen["runner"]
    assert spec.backend == "manager"
    assert spec.argv == tuple(command[2:])
    assert spec.source == "configured-source"
    assert spec.repair is not automatic
    assert dict(spec.context)["WORKTREE_MANAGER_ROOT"] == str(root)
    assert runner.argv[-len(command):] == tuple(command)
    assert "--update-participant" in runner.argv
    assert runner.argv[runner.argv.index("--source-digest") + 1] == "a" * 64
    assert "--install-only" not in runner.argv
    assert not capsys.readouterr().out


@pytest.mark.parametrize("state", ["interrupted", "blocked"])
def test_bootstrap_update_does_not_report_unfinished_work_as_success(tmp_path, monkeypatch, state):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        default_root=lambda: tmp_path,
        enqueue=lambda *a, **k: SimpleNamespace(id="fixture", log_path=tmp_path / "log"),
        drain=lambda: None,
        wait=lambda *a, **k: SimpleNamespace(state=state, exit_code=None),
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path, command=["update", "--supervised-debug"],
        repair_update=True, install_source=None,
    )
    with pytest.raises(RuntimeError, match=f"did not complete: {state}"):
        execute(args, driver, source_digest="a" * 64)


@pytest.mark.parametrize("detach", [False, True])
def test_default_update_observes_a_retained_worker_instead_of_running_installers(
    tmp_path, monkeypatch, detach,
):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    request = SimpleNamespace(
        id="fixture", log_path=tmp_path / "log", state="pending", exit_code=None, error=None,
    )
    seen = []

    def worker(command, **kwargs):
        assert Path(command[-1]) == tmp_path / "scripts" / "bootstrap_update.py"
        assert kwargs["cwd"] == tmp_path
        seen.append("worker")
        return "worker-handle"

    def wait(identifier, **kwargs):
        assert kwargs["worker"] == "worker-handle"
        seen.append("wait")
        return SimpleNamespace(state="completed", exit_code=23)

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        default_root=lambda: tmp_path,
        enqueue=lambda *a, **k: request, ensure_worker=worker, wait=wait,
        drain=lambda: pytest.fail("ordinary observer ran installation"),
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager",
        command=["update", *(["--detach"] if detach else [])],
        repair_update=True, install_source=None,
    )
    assert execute(args, driver, source_digest="a" * 64) == (0 if detach else 23)
    assert seen == (["worker"] if detach else ["worker", "wait"])


def test_prepared_old_source_is_data_not_the_bootstrap_controller(tmp_path, monkeypatch):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    import bootstrap_inputs

    source, controller = tmp_path / "old-source", tmp_path / "retained-controller"
    source.mkdir()
    selected = tmp_path / "prepared"
    seen = {}

    def retain(payload, root, *, include_sdk):
        assert payload == SCRIPT.parents[1] and root == tmp_path
        assert include_sdk is False
        return controller, False

    def enqueue(spec, *, runner, coalesce_running):
        assert coalesce_running is False
        seen["runner"] = runner
        return SimpleNamespace(id="fixture", log_path=tmp_path / "log")

    monkeypatch.setattr(bootstrap_inputs, "retain", retain)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=enqueue, default_root=lambda: tmp_path, drain=lambda: None,
        wait=lambda *a, **k: SimpleNamespace(state="completed", exit_code=23),
    )
    args = SimpleNamespace(
        payload=source, root=tmp_path / "manager", install_source=selected,
        command=["update", "--supervised-debug"], repair_update=False,
    )
    assert execute(args, driver, source_digest="a" * 64) == 23
    runner = seen["runner"]
    assert runner.cwd == str(controller)
    assert str(controller / "scripts" / "runtime_install.py") in runner.argv
    assert runner.argv[runner.argv.index("--payload") + 1] == str(source)
    assert runner.argv[runner.argv.index("--install-source") + 1] == str(selected)
    assert not list(source.iterdir())


@pytest.mark.parametrize("automatic", [False, True])
def test_default_update_uses_supported_manual_repair_without_replaying_failure(
    tmp_path, monkeypatch, automatic,
):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    root = tmp_path / "driver"
    monkeypatch.setattr(core, "default_root", lambda: root)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    old = core.enqueue(
        core.RequestSpec("old-attempt"),
        runner=core.TrustedRunner((sys.executable,), str(tmp_path)),
    )
    drain = core.drain

    def interrupt(transaction):
        raise RuntimeError("original interrupted attempt")

    with pytest.raises(RuntimeError, match="original interrupted attempt"):
        drain(executor=interrupt)
    before = core.status(old.id)
    assert before.unresolved and before.state == "interrupted"
    executed = []
    monkeypatch.setattr(
        core, "drain",
        lambda: drain(executor=lambda transaction: executed.append(transaction.request.id) or 23),
    )
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager",
        command=["update", "--supervised-debug", *(["--auto"] if automatic else [])],
        repair_update=True, install_source=None,
    )
    if automatic:
        with pytest.raises(core.InterruptedError, match="queue fenced"):
            execute(args, core, source_digest="a" * 64)
        assert core.status() == (before,)
        assert not executed
        return
    assert execute(args, core, source_digest="a" * 64) == 23
    old, new = core.status()
    assert old.state == before.state and old.error == before.error
    assert old.spec == before.spec and old.runner == before.runner
    assert old.finished_at == before.finished_at and old.exit_code is None
    assert old.recovery_authority == "operator_repair"
    assert old.settled_at is None and old.settlement_kind is None
    assert old.released_at is not None
    assert new.id != old.id and executed == [new.id]
    assert new.state == "completed" and new.exit_code == 23


def test_automatic_whole_update_rechecks_its_saved_hold_before_construction(tmp_path, monkeypatch):
    execute = runpy.run_path(str(SCRIPT))["execute"]
    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "driver")
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "configured-source")
    monkeypatch.setattr(update_driver_cli, "request_context", lambda: {})
    saved = tmp_path / "saved-hold.json"
    saved.write_text('{"active": false}', encoding="utf-8")
    other = tmp_path / "worker-hold.json"
    other.write_text('{"active": false}', encoding="utf-8")
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(saved))
    drain = core.drain

    def owner():
        saved.write_text('{"active": true}', encoding="utf-8")
        monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(other))
        drain(executor=lambda transaction: pytest.fail("held update started construction"))

    monkeypatch.setattr(core, "drain", owner)
    args = SimpleNamespace(
        payload=tmp_path, root=tmp_path / "manager", install_source=None,
        command=["update", "--supervised-debug", "--auto"], repair_update=False,
    )
    assert execute(args, core, source_digest="a" * 64) == 3
    request, = core.status()
    assert request.spec.killswitch_path == str(saved)
    assert request.state == "completed" and request.started_at is None
    assert not request.log_path.exists()


def test_outer_update_does_not_dispatch_a_second_suite_after_its_queued_result(tmp_path, monkeypatch):
    adapter = self_install._runtime_installer()
    import bootstrap_update

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setattr(adapter, "_execution_driver", lambda payload: object())
    monkeypatch.setattr(adapter, "_automatic_bootstrap_refusal", lambda args: None)
    monkeypatch.setattr(adapter, "_installation_custody", lambda: nullcontext(None))
    monkeypatch.setattr(adapter, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.setattr(bootstrap_update, "execute", lambda *a, **k: 23)
    monkeypatch.setattr(
        adapter, "_finish", lambda *a: pytest.fail("whole update dispatched twice"),
    )
    assert adapter.main(["--root", str(tmp_path), "--", "update"]) == 23


def test_update_participant_retains_custody_through_manager_and_suite(tmp_path, monkeypatch):
    adapter = self_install._runtime_installer()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    events = []
    saved = tmp_path / "saved-hold.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(tmp_path / "worker-hold.json"))

    class Custody:
        root = tmp_path
        request_id = "fixture"

        def __enter__(self):
            events.append("owned")
            return self

        def __exit__(self, *_args):
            events.append("released")

    spec = core.RequestSpec(
        "manager", ("--project", "sample"),
        context=(("WORKTREE_MANAGER_ROOT", str(tmp_path)),), killswitch_path=str(saved),
    )
    monkeypatch.setattr(adapter, "_execution_driver", lambda payload: SimpleNamespace(
        status=lambda identifier, **kwargs: SimpleNamespace(spec=spec),
    ))

    def policy(args):
        assert events == ["owned"]
        assert adapter.os.environ["BOOTSTRAP_KILLSWITCH_STATE_FILE"] == str(saved)

    monkeypatch.setattr(adapter, "_automatic_bootstrap_refusal", policy)
    monkeypatch.setattr(adapter, "_installation_custody", Custody)
    monkeypatch.setattr(adapter, "_install", lambda args: events.append("manager-built") or tmp_path)

    def finish(args, slot, command):
        assert events == ["owned", "manager-built"]
        assert command == ["update", "--project", "sample"]
        events.append("suite-finished")
        return 23

    monkeypatch.setattr(adapter, "_finish", finish)
    assert adapter.main([
        "--root", str(tmp_path), "--update-participant", "--source-digest", "a" * 64,
        "--", "update", "--project", "sample",
    ]) == 23
    assert events == ["owned", "manager-built", "suite-finished", "released"]
    assert adapter.os.environ["BOOTSTRAP_KILLSWITCH_STATE_FILE"] == str(tmp_path / "worker-hold.json")
