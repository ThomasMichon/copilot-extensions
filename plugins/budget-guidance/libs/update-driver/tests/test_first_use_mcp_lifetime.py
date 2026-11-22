"""Actual MCP bootstrap after destruction of its live native submitting owners."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances
from update_driver import RequestSpec, TrustedRunner, enqueue, foundation_status, status
from update_driver.core import OperationLock, OperationLockedError

from test_first_use_codespaces_bootstrap import hook_command
from test_first_use_genesis import observe
from test_first_use_mcp import REPOSITORY, owning_mcp as owning_mcp
from test_first_use_mcp_materialized import (
    assert_installed_mcp, materialized_mcp as materialized_mcp,
)
from test_first_use_vault import cold_environment
from test_first_use_vault_bootstrap import ordinary_receipt


pytestmark = pytest.mark.skipif(
    not os.environ.get("COPILOT_EXTENSIONS_MCP_LIFETIME_ROOT"),
    reason="explicit uncontained ephemeral hosted lifetime owner",
)


def test_actual_hook_finishes_after_all_native_callers_are_lost(
    materialized_mcp, area, record_property,
):
    sys.path.insert(0, str(REPOSITORY / "tools"))
    import mcp_bootstrap_lifetime as lane

    temporary = lane.admit(True)
    native_root = Path(os.environ[lane.ROOT]).resolve()
    assert native_root.is_relative_to(temporary)
    assert native_root.name.startswith("ce-broker-probe-mcp-")
    payload, installation, project = materialized_mcp
    root = installation.parent / ".copilot-extensions/update-driver"
    registry = native_root / "instances"
    outcomes, cycles = [], []
    sentinel = installation / "bridges/fixture-preserved.yaml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-server-not-started"]},
        "auth": {"kind": "none"},
    }), encoding="utf-8")
    preserved = sentinel.read_bytes()

    def diagnostic():
        text = "\n".join(
            f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in sorted({
                *area.rglob("*.log"), *native_root.glob("caller-*/*.log"),
                *native_root.glob("caller-*/hook.stderr"),
            })[:64] if path.is_file()
        )
        (native_root / "owner-diagnostics.log").write_text(text, encoding="utf-8")
        return text

    def owner_alive(owner):
        if os.name == "nt":
            return owner.poll() is None
        from agent_procutil._caller_owned import _pidfd_exited

        return not _pidfd_exited(owner)

    def completed(log, owner):
        progress = foundation_status(root=root)
        if progress is not None:
            assert progress.state not in {"failed", "interrupted"}, diagnostic()
        receipt = ordinary_receipt(log)
        if receipt is None:
            assert owner_alive(owner), "Acknowledged owner exited without an ordinary receipt\n" + diagnostic()
            return False
        result = status(receipt, root=root)
        assert result.state not in {"failed", "interrupted"}, diagnostic()
        if result.state != "completed":
            assert owner_alive(owner), "Acknowledged owner exited before ordinary completion\n" + diagnostic()
        return result.state == "completed"

    try:
        with ExitStack() as witnesses:
            if os.name == "nt":
                from windows_broker_desktop import DesktopWitness
                import agent_procutil as pu

                witness = DesktopWitness(native_root)
                record_property("desktop-positive-control", json.dumps(witness.qualify(pu)))
                witness.start()
                witnesses.callback(
                    lambda: record_property("desktop-observation", json.dumps(witness.finish())),
                )
            lifecycle = witnesses.enter_context(daemon_test_lifecycle(
                owner="mcp-native-caller-loss", registry_root=registry, cleanup_results=outcomes,
            ))
            environment = cold_environment(
                installation, project, area, python_prerequisite=True,
                lifecycle_environment=lifecycle,
            )
            environment.pop("AGENT_VAULT_HOME")
            if os.name == "nt":
                home = area / "distinct-home"
                home.mkdir()
                environment["HOME"] = str(home)
            environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(installation.parent / "killswitch.json")
            environment.update({
                key: os.environ[key]
                for key in ("RUNNER_TEMP", "GITHUB_ACTIONS", "RUNNER_ENVIRONMENT")
            })
            if sys.platform == "linux":
                from agent_procutil.linux_daemon_scope import caller_unit

                if caller_unit() is not None:
                    runner = shutil.which("systemd-run", path=environment["PATH"])
                    assert runner, "native caller cleanup unit requires the real systemd-run"
                for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                    if key in os.environ:
                        environment[key] = os.environ[key]
                    else:
                        environment.pop(key, None)
            environment = lane.consumer_environment(environment, lifecycle)
            if os.name == "nt":
                from windows_normal_process_lifetime import qualify

                record_property("native-owner-controls", json.dumps(qualify(native_root, environment)))

            for cycle, action in enumerate(("stamp", "provision")):
                print(f"MCP native qualification: {action} caller admission", flush=True)
                if cycle:
                    (installation / "deploy-manifest.json").write_text(json.dumps({
                        "source": {"version": "0.0.0", "path": str(payload)},
                    }), encoding="utf-8")
                root.mkdir(parents=True, exist_ok=True)
                lock = OperationLock(root / "execution.lock")
                lock.acquire()
                with ExitStack() as actors:
                    actors.callback(lock.release)
                    requests = []
                    if cycle:
                        ordinary = enqueue(
                            RequestSpec("mcp-native-ordinary"),
                            runner=TrustedRunner(
                                (sys.executable, "-c", "raise SystemExit(7)"), str(project), timeout=60,
                            ), root=root,
                        )
                    for follower in range(2 if not cycle else 1):
                        directory = native_root / f"caller-{cycle}-{follower}"
                        command = [sys.executable, str(REPOSITORY / "tools/mcp_bootstrap_lifetime.py"),
                                   "--caller", str(directory)]
                        directory.mkdir()
                        (directory / "hook-command.json").write_text(
                            json.dumps(hook_command(payload, project, installation.parent)),
                            encoding="utf-8",
                        )
                        if os.name == "nt":
                            from windows_normal_process_lifetime import start_caller_hierarchy

                            process, job = start_caller_hierarchy(command, directory, environment)
                            actors.callback(job.close)
                        else:
                            log = actors.enter_context((directory / "caller.log").open("wb"))
                            process = subprocess.Popen(
                                command, env=environment, cwd=project, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                **no_window_kwargs(),
                            )
                            job = None
                        actors.callback(_settle_caller, process, job)
                        observe(lambda: (directory / "outcome.json").is_file(), 30, diagnostic)
                        outcome = json.loads((directory / "outcome.json").read_text())
                        assert outcome["code"] == 0 and outcome["seconds"] <= 15, diagnostic()
                        assert (directory / "hook.stdout").read_bytes() == b"{}", diagnostic()
                        assert len(outcome["frames"]) == 1, diagnostic()
                        frame = outcome["frames"][0]
                        if os.name == "nt":
                            from agent_procutil import _job_scopes
                            from test_foundation_genesis_process import pinned_worker
                            from windows_normal_process_lifetime import (
                                _verified_process, caller_scope_names,
                            )

                            receipt = json.loads(frame)
                            owner = pinned_worker(receipt)
                            bounded = _verified_process(
                                outcome["ordinary"], outcome["ordinary_birth"], "bounded MCP control",
                            )
                            actors.callback(owner.close)
                            actors.callback(bounded.close)
                            scopes = {_job_scopes.ENVIRONMENT_KEY: outcome["scopes"]}
                            caller_scope_names(environment, job, outcome["scopes"])
                            with _job_scopes.query_scopes(scopes) as outside_callers:
                                outside_callers(owner._handle)
                            receipt_log = Path(receipt["log"])
                            leader = None
                        else:
                            from agent_procutil._linux_pidfd import open_pidfd
                            from agent_procutil._caller_owned import _pidfd_exited

                            owner_pid = int(frame.split()[0].partition(":")[2])
                            owner = open_pidfd(owner_pid)
                            bounded = open_pidfd(outcome["ordinary"])
                            leader = open_pidfd(process.pid)
                            for descriptor in (owner, bounded, leader):
                                actors.callback(os.close, descriptor)
                            assert os.getpgid(process.pid) == process.pid
                            assert os.getpgid(outcome["ordinary"]) == process.pid
                            assert not _pidfd_exited(leader), diagnostic()
                            receipt_log = Path(frame.partition(" log:")[2])
                            assert not _pidfd_exited(owner), diagnostic()
                            assert os.path.samefile(
                                Path("/proc") / str(owner_pid) / "fd/1", receipt_log,
                            ), diagnostic()
                            assert not _pidfd_exited(owner), diagnostic()
                            assert os.getpgid(owner_pid) != process.pid
                        requests.append((process, job, owner, bounded, leader, receipt_log, outcome))

                    for process, job, owner, bounded, leader, _, _ in requests:
                        assert process.poll() is None, diagnostic()
                        if os.name == "nt":
                            assert owner.poll() is None and bounded.poll() is None, diagnostic()
                            job.close()
                            bounded.wait(timeout=5)
                        else:
                            assert not _pidfd_exited(owner) and not _pidfd_exited(bounded), diagnostic()
                            assert not _pidfd_exited(leader), diagnostic()
                            assert os.getpgid(process.pid) == process.pid
                            os.killpg(process.pid, signal.SIGKILL)
                            observe(lambda: _pidfd_exited(bounded), 5, diagnostic)
                        process.wait(timeout=5)
                        for _ in range(2):
                            alive = owner.poll() is None if os.name == "nt" else not _pidfd_exited(owner)
                            assert alive, diagnostic()
                            time.sleep(.1)
                    lock.release()
                    print(f"MCP native qualification: {action} caller boundaries destroyed", flush=True)
                    diagnostic()
                    results = []
                    for _, _, owner, _, _, receipt_log, _ in requests:
                        observe(lambda: completed(receipt_log, owner), 900, diagnostic)
                        result = status(ordinary_receipt(receipt_log), root=root)
                        assert result.exit_code == 0, diagnostic()
                        admission = json.loads(result.spec.source)
                        assert admission["plugin"] == "agent-mcp"
                        assert admission.get("installer_action", "provision") == action
                        assert Path(admission["payload"]) == payload
                        assert Path(admission["runtime_root"]) == installation
                        assert Path(admission["project"]) == project
                        results.append(result)
                        if os.name == "nt":
                            assert owner.wait(timeout=900) == 0, diagnostic()
                        else:
                            observe(lambda: _pidfd_exited(owner), 900, diagnostic)
                    assert all(
                        item.spec == results[0].spec and item.runner == results[0].runner
                        for item in results
                    ), diagnostic()
                    ordered = sorted(
                        {item.id: item for item in results}.values(), key=lambda item: item.created_at,
                    )
                    for earlier, later in zip(ordered, ordered[1:]):
                        assert (
                            earlier.finished_at is not None and earlier.finished_at <= later.created_at
                        ), diagnostic()
                    if cycle:
                        assert status(ordinary.id, root=root).exit_code == 7, diagnostic()
                    cycles.append({
                        "action": action, "callers": len(requests), "same_request": len(ordered) == 1,
                        "coalescing_checked_against_acceptance_times": True,
                        "all_caller_boundaries_destroyed": True,
                        "same_acknowledged_owners_survived": True,
                        "bounded_controls_exited": True, "ordinary_completed": True,
                        "hook_seconds": [item[-1]["seconds"] for item in requests],
                    })
                    print(f"MCP native qualification: {action} ordinary work completed", flush=True)
            assert_installed_mcp(installation, environment, project)
            assert sentinel.read_bytes() == preserved
            observe(lambda: foundation_status(root=root).state != "running", 900, diagnostic)
            observe(lambda: _unlocked(root), 900, diagnostic)
    finally:
        diagnostic()
        record_property("mcp-native-caller-loss", json.dumps(cycles))
        record_property("mcp-native-cleanup", json.dumps([vars(item) for item in outcomes]))
    assert outcomes and all(item.status == "settled" for item in outcomes), outcomes
    assert inspect_instances(registry) == []


def _settle_caller(process, job):
    if job is not None and job.handle:
        job.terminate_and_wait(5)
    elif job is None and process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)


def _unlocked(root):
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError:
        return False
    lock.release()
    return True
