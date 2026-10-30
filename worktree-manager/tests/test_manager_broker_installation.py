"""Manager adoption precedes selector callbacks under the existing driver."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from agent_procutil import no_window_kwargs
import pytest

from update_driver import core
from update_driver.participant import ENVIRONMENT_KEY, ParticipantError, borrowed_custody, participant
from worktree_manager import self_install


@pytest.fixture
def installation(tmp_path, monkeypatch):
    adapter = self_install._runtime_installer()
    root = tmp_path / "manager"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    execution_root = tmp_path / "driver"
    monkeypatch.setattr(core, "default_root", lambda: execution_root)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    return SimpleNamespace(adapter=adapter, root=root, slot=slot, execution_root=execution_root)


def test_publication_and_transactional_callback_need_no_task_adoption(installation, monkeypatch):
    from contextlib import contextmanager

    state = installation
    events = []
    monkeypatch.setattr(self_install, "payload_version", lambda payload: "1.2.3")
    monkeypatch.setattr(self_install, "clean_legacy_artifacts", lambda root: [])
    monkeypatch.setattr(self_install, "_deploy_binstubs", lambda: ())
    monkeypatch.setattr(self_install, "_write_control_plane_provider_manifest", lambda *a, **k: None)
    adopt = Mock(side_effect=AssertionError("normal construction must not adopt a Task"))
    monkeypatch.setattr(state.adapter, "adopt_broker", adopt)
    publication = state.adapter.runtime._runtime_component("versioned_runtime_publication")

    @contextmanager
    def targets(*args):
        events.append("target-leases")
        try:
            yield
        finally:
            events.append("target-leases-released")

    monkeypatch.setattr(publication, "target_leases", targets)

    def publish(root, version, *, expected, before_publish, after_publish):
        assert events == ["validate"]
        events.append("publication-lease")
        before_publish()
        after_publish()

    def callback(slot):
        assert events == [
            "validate", "publication-lease", "target-leases",
            "validate", "target-leases-released",
        ]
        assert slot == state.slot
        events.append("cutover")

    monkeypatch.setattr(state.adapter, "activate", publish)
    result = self_install._complete_candidate(
        SimpleNamespace(slot=state.slot, reused=False), state.root, state.root,
        previous=None, was_current=False, before_publish=lambda: events.append("validate"),
        after_activate=callback, transactional_activation=True,
    )
    assert result.action == "installed"
    assert events[-1] == "cutover"
    adopt.assert_not_called()


def test_source_refusal_precedes_broker_mutation(installation, monkeypatch):
    state = installation
    monkeypatch.setattr(self_install, "payload_version", lambda payload: "1.2.3")
    adopt, activate = Mock(), Mock()
    monkeypatch.setattr(state.adapter, "adopt_broker", adopt)
    monkeypatch.setattr(state.adapter, "activate", activate)
    with pytest.raises(RuntimeError, match="source changed"):
        self_install._complete_candidate(
            SimpleNamespace(slot=state.slot, reused=False), state.root, state.root,
            previous=None, was_current=False,
            before_publish=Mock(side_effect=RuntimeError("source changed")),
        )
    adopt.assert_not_called()
    activate.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows installation integration")
def test_adoption_without_custody_does_not_start_a_child(installation, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    spawn = Mock()
    monkeypatch.setattr(installation.adapter.subprocess, "Popen", spawn)
    with pytest.raises(RuntimeError, match="live installation custody"):
        installation.adapter.adopt_broker(installation.root, installation.slot)
    spawn.assert_not_called()
    assert not installation.execution_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows installation integration")
def test_direct_apply_without_custody_refuses_before_construction(installation, monkeypatch):
    state = installation
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(self_install, "payload_version", lambda payload: "1.2.3")
    monkeypatch.setattr(self_install, "needs_install", lambda *args: True)
    constructor = Mock(side_effect=AssertionError("construction outside driver custody"))
    monkeypatch.setattr(state.adapter, "candidate", constructor)
    result = self_install.self_install(state.root, root=state.root, dry_run=False)
    assert result.action == "error" and "live installation custody" in result.reason
    constructor.assert_not_called()
    assert not state.execution_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows installation integration")
@pytest.mark.parametrize("scoped", [False, True])
def test_adoption_hands_live_custody_to_the_fixed_installed_entry(
    installation, monkeypatch, scoped,
):
    state = installation
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    interpreter = state.slot / "Scripts" / "python.exe"
    monkeypatch.setattr(state.adapter.runtime, "slot_python", lambda *args: interpreter)
    calls = []
    request = core.enqueue(
        core.RequestSpec("manager-adoption-fixture"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(state.root)),
    )

    def launch(command, **options):
        assert command == [
            str(interpreter), "-I", "-m", "update_driver.windows_broker",
            "--runtime-root", str(state.root), "--slot", str(state.slot),
            "--execution-root", str(state.execution_root),
        ]
        assert options["creationflags"] == state.adapter.subprocess.CREATE_NO_WINDOW
        assert ENVIRONMENT_KEY not in os.environ
        with participant(
            root=state.execution_root, witness=options["env"][ENVIRONMENT_KEY],
        ) as child, child.execution_scope():
            child.assert_owned()
        calls.append(command)
        return SimpleNamespace(returncode=0, communicate=lambda: ('{"adopted":true}\n', ""))

    monkeypatch.setattr(state.adapter.subprocess, "Popen", launch)

    def executor(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        if scoped:
            with borrowed_custody(root=state.execution_root):
                state.adapter.adopt_broker(state.root, state.slot)
        else:
            state.adapter.adopt_broker(state.root, state.slot)
        return 0

    assert core.drain(executor=executor) == 0
    assert core.status(request.id).state == "completed"
    assert len(calls) == 1


def test_contained_and_posix_installation_do_not_adopt_a_host_task(installation, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    spawn = Mock()
    monkeypatch.setattr(installation.adapter.subprocess, "Popen", spawn)
    installation.adapter.adopt_broker(installation.root, installation.slot)
    spawn.assert_not_called()
    assert not installation.execution_root.exists()


@pytest.mark.parametrize("changed", [None, "root", "source", "slot", "version", "oversized"])
@pytest.mark.parametrize("skip_reconcile", [False, True])
def test_foreground_bootstrap_queues_only_its_fixed_installation_not_application_arguments(
    installation, monkeypatch, changed, skip_reconcile,
):
    state = installation
    monkeypatch.setattr(state.adapter, "_source_digest", lambda payload: "a" * 64)
    state.adapter.runtime.mark_complete(state.root, state.slot.name, payload_hash="b" * 64)
    log = state.root / "installation.log"
    receipt = {
        "schema": "copilot-extensions.manager-bootstrap", "version": 1,
        "root": str(state.root), "source": "a" * 64, "slot": str(state.slot),
    }
    if changed is not None:
        receipt[changed] = True if changed == "version" else "foreign"
    log.write_text(
        ("x" * 65537 if changed == "oversized" else json.dumps(receipt)) + "\n", encoding="utf-8",
    )
    # Another drainer may already have selected a later generation. Dispatch
    # must retain this request's actual result, never choose that newer marker.
    state.adapter.runtime.mark_complete(state.root, "9.9.9", payload_hash="c" * 64)
    (state.root / "current-version").write_text("9.9.9\n", encoding="utf-8")
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0, log_path=log)),
    )
    args = SimpleNamespace(
        payload=state.root, root=state.root, repair_update=False, recover_adopted=None,
        skip_instance_reconcile=skip_reconcile,
        command=["create", "--prompt", "application data that must not be journaled"],
    )
    if changed is None:
        assert state.adapter._foreground_install(args, driver) == state.slot
    else:
        with pytest.raises((RuntimeError, ValueError)):
            state.adapter._foreground_install(args, driver)
    spec, runner = queued[0]
    assert spec.backend == "manager-bootstrap"
    assert spec.argv == (("install", "skip-instance-reconcile") if skip_reconcile else ("install",))
    assert dict(spec.context)["WORKTREE_MANAGER_ROOT"] == str(state.root)
    assert "WORKTREE_MANAGER_ROOT" in runner.context_keys
    assert "AGENT_WORKTREES_PAYLOAD_ROOT" in runner.context_keys
    assert runner.argv == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
        str(state.adapter.SCRIPTS / "runtime_install.py"),
        "--root", str(state.root), "--payload", str(state.root),
        "--install-only", "--source-digest", "a" * 64,
        *(("--skip-instance-reconcile",) if skip_reconcile else ()),
    )
    driver.drain.assert_called_once_with()
    driver.wait.assert_called_once_with("request", output=sys.stdout)


@pytest.mark.parametrize(("command", "repair"), [
    (["--", "update", "--force"], True),
    (["update", "--force-runtime", "agent-worktrees"], True),
    (["update", "--recreate-venv"], True),
    (["update"], True),
    (["update", "--auto"], False),
    (["update", "--auto", "--force"], False),
    (["create", "--prompt", "--force"], False),
])
@pytest.mark.parametrize("held", [True, False])
def test_bootstrap_retains_update_repair_authority_without_executing_inhibited_work(
    installation, monkeypatch, command, repair, held,
):
    state = installation
    monkeypatch.setattr(state.adapter, "_source_digest", lambda payload: "a" * 64)
    state.adapter.runtime.mark_complete(state.root, state.slot.name, payload_hash="b" * 64)
    switch = state.root / "killswitch.json"
    switch.write_text(json.dumps({"active": held}), encoding="utf-8")
    caller = state.root / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", "../killswitch.json")
    automatic = core.enqueue(
        core.RequestSpec("automatic-fixture", cwd=str(caller)),
        runner=core.TrustedRunner((sys.executable, "-c", "raise SystemExit(99)"), str(state.root)),
    )
    executed = []

    def install(transaction):
        if transaction.request.id == automatic.id:
            executed.append(automatic.id)
            return 0
        assert transaction.request.spec.backend == "manager-bootstrap"
        executed.append(transaction.request.id)
        transaction.log_path.write_text(json.dumps({
            "schema": "copilot-extensions.manager-bootstrap", "version": 1,
            "root": str(state.root), "source": "a" * 64, "slot": str(state.slot),
        }) + "\n", encoding="utf-8")
        return 0

    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=core.enqueue, drain=lambda: core.drain(executor=install), wait=core.wait,
    )
    normalized = command[1:] if command[:1] == ["--"] else command
    args = SimpleNamespace(
        payload=state.root, root=state.root, command=command,
        repair_update=normalized[:1] == ["update"], recover_adopted=None,
        skip_instance_reconcile=False,
    )
    result = state.adapter._foreground_install(args, driver)
    admitted = repair or not held
    assert result == (state.slot if admitted else 3)
    skipped = core.status(automatic.id)
    assert skipped.state == "completed" and skipped.exit_code == (3 if held else 0)
    if held:
        assert skipped.started_at is None
    bootstrap = next(request for request in core.status() if request.id != automatic.id)
    assert bootstrap.spec.repair is repair
    assert Path(bootstrap.spec.killswitch_path) == switch
    assert bootstrap.state == "completed" and bootstrap.exit_code == (0 if admitted else 3)
    assert executed == ([] if held else [automatic.id]) + ([bootstrap.id] if admitted else [])
    assert bootstrap.spec.argv == (
        (*(("install", "repair-update") if normalized[:1] == ["update"] else ("install",)),
         *(("--auto",) if "--auto" in normalized else ()))
    )
    assert "--force" not in bootstrap.runner.argv
    assert json.loads(switch.read_text()) == {"active": held}


@pytest.mark.parametrize(("automatic", "held"), [(False, True), (True, True), (True, False)])
def test_native_bootstrap_hold_precedes_installer(tmp_path, automatic, held):
    root = tmp_path / "manager"
    marker = tmp_path / "installer-ran"
    hold = tmp_path / "hold.json"
    hold.write_text(json.dumps({"active": held}))
    command = [sys.executable, "-c", """
import sys
from contextlib import nullcontext
from pathlib import Path
from worktree_manager import self_install
adapter = self_install._runtime_installer()
def install(args):
    Path(sys.argv[2]).write_text('ordinary construction')
    return 17
adapter._install = install
adapter._foreground_install = lambda args, driver: install(args)
adapter._installation_custody = lambda: nullcontext(None)
raise SystemExit(adapter.main(['--root', sys.argv[1], '--', 'update', *sys.argv[3:]]))
""", str(root), str(marker), *(["--auto"] if automatic else [])]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        env={**os.environ, "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(hold),
             "COPILOT_EXTENSIONS_CONTEXT": ""},
    )
    skipped = automatic and held
    assert result.returncode == (3 if skipped else 17), result.stdout + result.stderr
    assert marker.exists() is not skipped
    assert not root.exists()
    assert json.loads(hold.read_text()) == {"active": held}
    if skipped:
        assert "automatic-update KillSwitch active; nothing ran" in result.stderr


def test_native_bootstrap_hold_preserves_interrupted_fence(installation, monkeypatch):
    state = installation
    hold = state.root / "hold.json"
    hold.write_text(json.dumps({"active": True}))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(hold))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    runner = core.TrustedRunner((sys.executable, "-c", "pass"), str(state.root))
    request = core.enqueue(core.RequestSpec("interrupted-fixture", repair=True), runner=runner)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        core.drain(executor=Mock(side_effect=RuntimeError("fixture interruption")))
    before = core.status(request.id)
    assert before.state == "interrupted" and before.settled_at is None
    monkeypatch.setattr(state.adapter, "_execution_driver", lambda payload: core)
    monkeypatch.setattr(state.adapter, "_source_digest", lambda payload: "a" * 64)

    assert state.adapter.main(["--root", str(state.root), "--", "update", "--auto"]) == 3

    assert core.status() == (before,)
    with pytest.raises(core.InterruptedError, match="queue fenced"):
        core.enqueue(core.RequestSpec("still-fenced", repair=True), runner=runner)
    assert json.loads(hold.read_text()) == {"active": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows removal execution ownership")
@pytest.mark.parametrize("as_json", [False, True])
def test_apply_removal_enters_the_driver_before_any_removal_custody(
    installation, monkeypatch, as_json,
):
    import runtime_uninstall

    state = installation
    monkeypatch.setattr(state.adapter, "_source_digest", lambda payload: "a" * 64)
    monkeypatch.setattr(state.adapter, "_execution_driver", lambda payload: core)
    calls = []

    def remove(root, *, apply, as_json):
        assert root == state.root and apply
        from update_driver import participant_environment

        assert participant_environment()
        calls.append(as_json)
        return 0

    monkeypatch.setattr(runtime_uninstall, "uninstall", remove)
    request = core.enqueue(
        core.RequestSpec("manager-removal-fixture"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(state.root)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return state.adapter.main([
            "--root", str(state.root), "--uninstall", "--apply",
            *(["--json"] if as_json else []),
        ])

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert calls == [as_json]


@pytest.mark.skipif(os.name != "nt", reason="Windows removal execution ownership")
def test_unowned_apply_removal_queues_only_the_fixed_uninstall_command(
    installation, monkeypatch,
):
    import runtime_uninstall

    state = installation
    direct = Mock(side_effect=AssertionError("removal before installation execution ownership"))
    monkeypatch.setattr(runtime_uninstall, "uninstall", direct)
    monkeypatch.setattr(state.adapter, "_source_digest", lambda payload: "a" * 64)
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=7)),
    )
    monkeypatch.setattr(state.adapter, "_execution_driver", lambda payload: driver)
    assert state.adapter.main([
        "--root", str(state.root), "--uninstall", "--apply", "--json",
    ]) == 7
    direct.assert_not_called()
    spec, runner = queued[0]
    assert spec.backend == "manager-uninstall"
    assert spec.argv == ("--apply", "--json")
    assert runner.argv == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
        str(state.adapter.SCRIPTS / "runtime_install.py"),
        "--root", str(state.root), "--uninstall", "--apply", "--json",
    )
    assert dict(spec.context)["WORKTREE_MANAGER_ROOT"] == str(state.root)
    driver.drain.assert_called_once_with()
    driver.wait.assert_called_once_with("request", output=sys.stdout)


@pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap admission")
def test_internal_installer_requires_a_live_owner_before_construction(installation):
    state = installation
    with pytest.raises(RuntimeError, match="live driver custody"):
        state.adapter.main([
            "--root", str(state.root), "--install-only", "--source-digest", "0" * 64,
        ])
    assert not state.execution_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows owned execution")
def test_real_bootstrap_participant_rejects_changed_source_before_installation(
    installation, monkeypatch,
):
    state = installation
    driver = state.adapter._execution_driver(state.adapter.SCRIPTS.parent)
    monkeypatch.setattr(core, "default_root", lambda: Path.home() / ".copilot-extensions" / "update-driver")
    request = driver.enqueue(
        core.RequestSpec("manager-bootstrap-admission-fixture"),
        runner=core.TrustedRunner((
            sys.executable, "-I", "-S", "-B", "-X", "utf8",
            str(state.adapter.SCRIPTS / "runtime_install.py"),
            "--root", str(state.root), "--install-only", "--source-digest", "0" * 64,
        ), str(state.root)),
    )
    with pytest.raises(ParticipantError, match="unclosed participants"):
        driver.drain()
    outcome = driver.status(request.id)
    assert outcome.state == "interrupted" and outcome.unresolved
    assert "source changed after driver admission" in outcome.log_path.read_text(encoding="utf-8")
    assert not (state.root / "current-version").exists()
    assert not (state.root / "windows-launch-broker.json").exists()


@pytest.mark.parametrize("scoped", [False, True])
def test_self_install_apply_joins_the_owner_without_another_request(
    installation, monkeypatch, scoped,
):
    from contextlib import nullcontext
    from worktree_manager import update_driver_cli
    from single_instance_lease.operation import OperationLock, OperationLockedError

    state = installation
    request = core.enqueue(
        core.RequestSpec("manager-self-install-fixture"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(state.root)),
    )
    calls = []

    def operation():
        with pytest.raises(OperationLockedError):
            OperationLock(state.execution_root / "execution.lock").acquire()
        calls.append("apply")
        return 17

    def executor(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        with borrowed_custody(root=state.execution_root) if scoped else nullcontext():
            assert update_driver_cli.self_install_request(operation) == 17
        return 0

    assert core.drain(executor=executor) == 0
    assert core.status(request.id).state == "completed"
    assert len(core.status()) == 1
    assert calls == ["apply"]


def test_self_install_apply_uses_native_driver_not_ensure_worker(installation, monkeypatch):
    import update_driver
    from worktree_manager import update_driver_cli

    state = installation
    invocation = (sys.executable, "-m", "worktree_manager", "self-install", "--apply")
    queued = []
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args: (invocation, {}))
    monkeypatch.setattr(
        update_driver, "enqueue",
        lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
    )
    drain = Mock()
    monkeypatch.setattr(update_driver, "drain", drain)
    monkeypatch.setattr(
        update_driver, "wait", lambda identifier, **kwargs: SimpleNamespace(state="completed", exit_code=13),
    )
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "source")
    monkeypatch.setattr(self_install, "default_root", lambda: state.root)
    monkeypatch.setattr(self_install, "running_payload_dir", lambda: state.root)
    monkeypatch.setattr(self_install, "_runtime_installer", lambda: state.adapter)
    operation = Mock(side_effect=AssertionError("installation outside custody"))
    assert update_driver_cli.self_install_request(operation) == 13
    assert queued[0][0].backend == "manager-self-install"
    assert queued[0][1].argv == invocation
    assert dict(queued[0][0].context)["WORKTREE_MANAGER_ROOT"] == str(state.root)
    assert "WORKTREE_MANAGER_ROOT" in queued[0][1].context_keys
    assert "AGENT_WORKTREES_PAYLOAD_ROOT" in queued[0][1].context_keys
    drain.assert_called_once_with()
    operation.assert_not_called()
