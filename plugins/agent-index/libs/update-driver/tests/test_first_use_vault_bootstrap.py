"""Actual bootstrap publication and caller loss on disposable native runners."""

from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import agent_procutil
import pytest
from update_driver import RequestSpec, StateError, TrustedRunner, drain, enqueue, foundation_status, status
from update_driver import core

from test_first_use_genesis import observe
from test_first_use_native import native_command
from test_first_use_vault import (
    assert_stamp_only, cold_environment, materialized_vault as materialized_vault,
    native_genesis, vault_command,
)


ACKNOWLEDGEMENT = "[agent-vault] driver launch acknowledgement: "


def ordinary_receipt(log):
    if not log.is_file():
        return None
    for line in log.read_text(encoding="utf-8-sig").splitlines(keepends=True):
        if line.startswith('{"request_id"') and line.endswith("\n"):
            document = json.loads(line)
            assert set(document) == {"request_id", "state"}
            assert isinstance(document["request_id"], str)
            assert document["state"] in {"pending", "running"}
            return document["request_id"]
    return None


def receipt_completed_count(root, count, log):
    receipt = ordinary_receipt(log)
    if receipt is None:
        return False
    requests = status(root=root)
    assert any(item.id == receipt for item in requests), "durable receipt has no ordinary request"
    return len(requests) == count and all(item.state == "completed" for item in requests)


def test_bootstrap_observer_waits_for_committed_receipt_without_masking_bad_state(area, monkeypatch):
    root = area / "queue"
    log = area / "first-use.log"
    entered, release = threading.Event(), threading.Event()
    validate = core._validate_db

    def pause_initial_commit(db):
        if db.in_transaction:
            entered.set()
            assert release.wait(10), "fixture database release missing"
        validate(db)

    monkeypatch.setattr(core, "_validate_db", pause_initial_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            enqueue, RequestSpec("bootstrap-receipt-control"), root=root,
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)),
        )
        try:
            assert entered.wait(10), "fixture did not enter initialization"
            with closing(sqlite3.connect(
                f"{(root / 'queue.sqlite3').as_uri()}?mode=ro", uri=True,
            )) as db:
                assert db.execute("PRAGMA user_version").fetchone()[0] == 0
            with pytest.raises(StateError, match="unsupported update-driver schema"):
                status(root=root)
            log.write_text('{"launched":true}\n', encoding="utf-8")
            assert not receipt_completed_count(root, 1, log)
            log.write_text('{"request_id":"partial"', encoding="utf-8")
            assert not receipt_completed_count(root, 1, log)
        finally:
            release.set()
        request = future.result(timeout=15)
    log.write_text(json.dumps({"request_id": request.id, "state": "pending"}) + "\n", encoding="utf-8")
    assert not receipt_completed_count(root, 1, log)
    drain(root=root, executor=lambda _transaction: 0)
    assert receipt_completed_count(root, 1, log)
    with closing(sqlite3.connect(root / "queue.sqlite3")) as db, db:
        db.execute("PRAGMA user_version=999")
    before = (root / "queue.sqlite3").read_bytes()
    with pytest.raises(StateError, match="unsupported update-driver schema"):
        receipt_completed_count(root, 1, log)
    assert (root / "queue.sqlite3").read_bytes() == before


@contextmanager
def owned_hook(
    command, *, env, project, directory, timeout, diagnostic,
    acknowledgement_prefix=ACKNOWLEDGEMENT,
):
    directory.mkdir()
    output = directory / "stdout"
    errors = directory / "stderr"
    caller = job = owner = descriptor = None

    def close_caller():
        nonlocal job
        if job is not None:
            job.close()
            job = None
        elif caller is not None and caller.poll() is None:
            if os.name == "nt":
                caller.kill()
            else:
                os.killpg(caller.pid, signal.SIGKILL)
            caller.wait(timeout=10)

    def exited():
        if owner is not None:
            return owner.poll() is not None
        from agent_procutil._caller_owned import _pidfd_exited
        return _pidfd_exited(descriptor)

    with output.open("wb") as stdout, errors.open("wb") as stderr:
        try:
            started = time.monotonic()
            if os.name == "nt":
                from test_foundation_genesis_process import pinned_worker
                caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
                    command, env=env, cwd=project, require_job=True, allow_breakaway=False,
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    **agent_procutil.no_window_kwargs(),
                )
                assert caller.wait(timeout=90) == 0, diagnostic()
            else:
                from agent_procutil._linux_pidfd import open_pidfd
                caller = subprocess.Popen(
                    [str(Path(env["PATH"]) / "bash"), "-c",
                     '"$@"; code=$?; printf "hook-status:%s\\n" "$code" >&2; '
                     '[[ "$code" == 0 ]] || exit "$code"; sleep 900',
                     "bootstrap-caller", *command],
                    env=env, cwd=project, start_new_session=True, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr,
                )
                observe(
                    lambda: "hook-status:" in errors.read_text(errors="replace")
                    or caller.poll() is not None,
                    90, diagnostic,
                )
                assert "hook-status:0" in errors.read_text(errors="replace"), diagnostic()
            elapsed = time.monotonic() - started
            text = errors.read_text(encoding="utf-8", errors="replace")
            frame = next(
                (line.partition(acknowledgement_prefix)[2] for line in text.splitlines()
                 if line.startswith(acknowledgement_prefix)), "",
            )
            assert frame, diagnostic()
            if os.name == "nt":
                acknowledgement = json.loads(frame)
                owner = pinned_worker(acknowledgement)
                receipt_log = Path(acknowledgement["log"])
            else:
                assert frame.startswith("launched:"), diagnostic()
                descriptor = open_pidfd(int(frame.split()[0].partition(":")[2]))
                receipt_log = Path(frame.partition(" log:")[2])
            assert json.loads(output.read_text(encoding="utf-8-sig")) == {}, diagnostic()
            # Pin the launched owner before asserting the budget, so a failure still settles it.
            assert elapsed <= timeout, f"bootstrap took {elapsed:.2f}s; hook deadline is {timeout}s\n{diagnostic()}"
            yield SimpleNamespace(close_caller=close_caller, exited=exited, receipt_log=receipt_log)
        finally:
            close_caller()
            if owner is not None:
                try:
                    owner.wait(timeout=900)
                finally:
                    owner.close()
            if descriptor is not None:
                try:
                    observe(exited, 900, diagnostic)
                finally:
                    os.close(descriptor)


@native_genesis
@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true",
    reason="actual default service integration is limited to disposable hosted qualification",
)
def test_actual_bootstrap_keeps_deadline_caller_independence_and_default_service(
    materialized_vault, area, native_window_observation,
):
    payload, installation, project = materialized_vault
    profile = installation.parent
    root = profile / ".copilot-extensions" / "update-driver"
    env = cold_environment(installation, project, area)
    env["AGENT_VAULT_INSTALLATION_ID"] = "fixture-foreign-installation"
    env["AGENT_VAULT_PIPE"] = "fixture-foreign-pipe"
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(profile / "bootstrap-killswitch.json")
    hooks = json.loads((payload / "hooks.json").read_text())
    deadline = hooks["hooks"]["sessionStart"][0]["timeoutSec"]
    if os.name == "nt":
        shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        hook = payload / "scripts" / "bootstrap-check.ps1"
        command = [
            shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            f"Set-Location -LiteralPath {quote(project)}; "
            f"[IO.Directory]::SetCurrentDirectory({quote(profile)}); "
            f"& {quote(hook)}; exit $LASTEXITCODE",
        ]
        cleanup = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                   str(payload / "scripts" / "install.ps1"), "uninstall", "-InstallDir", str(installation)]
    else:
        tools = Path(env["PATH"])
        # Only the existing hook's metadata/killswitch reader uses this interpreter.
        (tools / "python3").symlink_to(sys._base_executable)
        systemctl = shutil.which("systemctl")
        assert systemctl, "hosted qualification requires the real platform service manager client"
        (tools / "systemctl").symlink_to(systemctl)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        link = area / "project-link"
        link.symlink_to(project, target_is_directory=True)
        command = [
            str(tools / "bash"), "-c", 'cd -- "$1"; shift; exec "$@"', "bootstrap",
            str(link), str(tools / "bash"), str(payload / "scripts" / "bootstrap-check.sh"),
        ]
        cleanup = [str(tools / "bash"), str(payload / "scripts" / "install.sh"),
                   "uninstall", "--install-dir", str(installation)]

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(area)}:\n{path.read_text(errors='replace')[-16000:]}"
            for directory in (root, area / "cold-hook", area / "drift-hook")
            for path in directory.rglob("*")
            if path.is_file() and (path.suffix == ".log" or path.name in {"stdout", "stderr"})
        )

    def completed_count(count):
        requests = status(root=root)
        return len(requests) == count and all(item.state == "completed" for item in requests)

    def assert_request(request, action):
        assert request.exit_code == 0, diagnostic()
        admission = json.loads(request.spec.source)
        assert admission.get("installer_action", "provision") == action
        assert Path(admission["payload"]) == payload
        assert Path(admission["runtime_root"]) == installation
        assert Path(admission["project"]) == project
        assert "AGENT_VAULT_INSTALLATION_ID" not in dict(request.spec.context)
        selected = (root / "foundation" / "current-version").read_text().strip()
        launcher = (root / "foundation" / "versions" / selected / ".runtime-bootstrap" / "runtime_launcher.py").resolve()
        assert any(Path(arg).resolve() == launcher for arg in request.runner.argv if arg.endswith("runtime_launcher.py"))

    def healthy_hook(count, completion):
        before = completion.read_bytes()
        operation = foundation_status(root=root).operation_id
        result = subprocess.run(
            command, env=env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=deadline,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0 and json.loads(result.stdout) == {}, result.stdout + result.stderr
        assert not result.stderr and len(status(root=root)) == count
        assert foundation_status(root=root).operation_id == operation
        assert completion.read_bytes() == before

    try:
        with owned_hook(
            command, env=env, project=project, directory=area / "cold-hook",
            timeout=deadline, diagnostic=diagnostic,
        ) as cold:
            observe(lambda: foundation_status(root=root) is not None, 90, diagnostic)
            operation = foundation_status(root=root)
            assert operation.state == "running", diagnostic()
            cold.close_caller()
            assert not cold.exited(), diagnostic()
            observe(lambda: receipt_completed_count(root, 1, cold.receipt_log) or cold.exited(), 850, diagnostic)
            assert ordinary_receipt(cold.receipt_log), diagnostic()
            assert completed_count(1), diagnostic()
            stamp = status(root=root)[0]
            assert_request(stamp, "stamp")
            assert_stamp_only(installation)
            assert foundation_status(root=root).operation_id == operation.operation_id
            observe(cold.exited, 60, diagnostic)
        provision = subprocess.run(
            native_command(
                root, payload, installation, project,
                source=payload / "libs" / "update-driver", client_only=True,
            ) + ["-Wait" if os.name == "nt" else "--wait"],
            env={key: value for key, value in env.items()
                 if key not in {"AGENT_VAULT_INSTALLATION_ID", "AGENT_VAULT_PIPE"}},
            cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=850, **agent_procutil.no_window_kwargs(),
        )
        assert provision.returncode == 0 and completed_count(2), provision.stdout + provision.stderr + diagnostic()
        client = next(item for item in status(root=root) if item.id != stamp.id)
        assert client.spec.argv == ("--client-only",)
        selector = installation / "current-version"
        prior = installation / "versions" / selector.read_text().strip() / ".install-complete.json"
        healthy_hook(2, prior)
        project_file = payload / "pyproject.toml"
        text = project_file.read_text()
        current = json.loads((payload / "plugin.json").read_text())
        assert f'version = "{current["version"]}"' in text
        text = text.replace(f'version = "{current["version"]}"', 'version = "99.0.0"', 1)
        project_file.write_text(text, encoding="utf-8")
        current["version"] = "99.0.0"
        (payload / "plugin.json").write_text(json.dumps(current), encoding="utf-8")
        with owned_hook(
            command, env=env, project=project, directory=area / "drift-hook",
            timeout=deadline, diagnostic=diagnostic,
        ) as drift:
            drift.close_caller()
            observe(lambda: receipt_completed_count(root, 3, drift.receipt_log) or drift.exited(), 850, diagnostic)
            assert ordinary_receipt(drift.receipt_log), diagnostic()
            assert completed_count(3), diagnostic()
            request = next(item for item in status(root=root) if item.id not in {stamp.id, client.id})
            assert_request(request, "provision")
            assert request.spec.argv == () and "service skipped" not in request.log_path.read_text(errors="replace")
            observe(drift.exited, 60, diagnostic)
        assert selector.read_text().strip() == "99.0.0", diagnostic()
        completion = installation / "versions" / "99.0.0" / ".install-complete.json"
        assert json.loads(completion.read_text())["version"] == "99.0.0"
        if os.name == "nt":
            assert (installation / "service.ps1").is_file()
            assert "fixture-foreign" not in (installation / "service.ps1").read_text()
            task = subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                 "$ErrorActionPreference='Stop'; $task=Get-ScheduledTask -TaskName AgentVault; "
                 f"if ($task.Actions.Arguments -notlike '*{installation}*') {{ exit 91 }}"],
                env=env, cwd=project, capture_output=True, text=True, timeout=30,
                **agent_procutil.no_window_kwargs(),
            )
            assert task.returncode == 0, task.stdout + task.stderr
        else:
            units = list((profile / ".config" / "systemd" / "user").glob("agent-vault*.service"))
            assert len(units) == 1 and str(installation) in units[0].read_text(), diagnostic()
            assert "fixture-foreign" not in units[0].read_text()
        healthy_hook(3, completion)
        completed = completion.read_bytes()
        healthy = subprocess.run(
            vault_command(payload), env={**env, "AGENT_VAULT_NO_SELFPROVISION": "1"},
            cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding="utf-8", timeout=30, **agent_procutil.no_window_kwargs(),
        )
        assert healthy.returncode == 0 and "usage:" in healthy.stdout, healthy.stdout + healthy.stderr
        assert len(status(root=root)) == 3 and completion.read_bytes() == completed
        assert not (installation / "reconcile.lock").exists()
    finally:
        clean_env = {key: value for key, value in env.items()
                     if key not in {"AGENT_VAULT_INSTALLATION_ID", "AGENT_VAULT_PIPE"}}
        removed = subprocess.run(
            cleanup, env=clean_env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90, **agent_procutil.no_window_kwargs(),
        )
        assert removed.returncode == 0, removed.stdout + removed.stderr
