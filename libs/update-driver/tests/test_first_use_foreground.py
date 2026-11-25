"""Opt-in receiving operations against inert private fixture products only."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Any, Callable

import pytest
from _pytest.capture import CaptureManager

import agent_procutil
from agent_procutil import no_window_kwargs
from update_driver import (
    InterruptedError, RequestSpec, StateError, TrustedRunner, change_authority, core, drain, recovery_status,
    run_explicit, status, wait,
)
from update_driver import first_use
from test_process import eventually
from test_settled_failure_process import _product_exit


observe: Callable[[Callable[[], bool]], object] = eventually
product_exit: Callable[[int], AbstractContextManager[Callable[[], bool]]] = _product_exit
CONTRACT = "copilot-extensions.first-use-execution/1/foreground-finite-v1"
pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="the existing fixed provision receiver supports Windows and Linux only",
)


def command(payload: Path, installation: Path, project: Path, root: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    return core.runtime_command("update_driver.first_use", [
        "foreground", "--execution-contract", CONTRACT,
        "--payload", str(payload), "--runtime-root", str(installation),
        "--project", str(project), "--root", str(root),
    ])


@pytest.mark.parametrize("exit_code", [0, 7])
def test_foreground_receiver_actual_cli_receipt_history_and_result(
    owning_payload: tuple[Path, Path, Path], area: Path, exit_code: int,
) -> None:
    payload, installation, project = owning_payload
    for extension, message in (("ps1", "Write-Output 'finite-product-output'"), ("sh", "echo finite-product-output")):
        script = payload / "scripts" / f"install.{extension}"
        script.write_text(script.read_text(encoding="utf-8").replace(
            "exit 7\n", f"{message}\nexit {exit_code}\n",
        ), encoding="utf-8")
    root = area / "queue"
    argv, imports = command(payload, installation, project, root)
    result = subprocess.run(
        argv, env={**os.environ, **imports}, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "finite-product-output" in result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["state"] == "running"
    request = status(receipt["request_id"], root=root)
    assert request.state == "completed" and request.exit_code == exit_code
    assert request.started_at is not None and request.created_at <= request.started_at
    assert request.spec.backend == "owning-plugin-provision"
    assert request.spec.argv == ("--execution-contract", CONTRACT)
    assert json.loads(request.spec.source)["plugin"] == "agent-vault"
    assert [row.id for row in status(root=root)] == [request.id]
    assert (installation / "executed").read_text() == "provision"
    assert Path((installation / "project-path").read_text()) == project
    assert not list(root.glob(".worker-start*"))
    assert first_use.execution_contract()["scope"] == "python-provision-only"


@pytest.mark.parametrize("cell", [False, True])
def test_foreground_receiver_preserves_project_writer_authority(
    owning_payload: tuple[Path, Path, Path], area: Path,
    cell: bool,
) -> None:
    payload, installation, project = owning_payload
    root = area / "queue"
    context = ""
    prepared = (
        first_use._prepare(payload, installation, project, root=root, foreground=True)
        if cell else None
    )
    if cell:
        receipt = area / "marketplaces" / "example--0123456789abcdef" / "plugins" / "agent-vault" / "install.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}", encoding="utf-8")
        context = str(receipt)
    change_authority(
        "project", project="example", context=context, root=root, timeout=1,
        verify_capabilities=lambda _deadline: True,
    )
    with pytest.raises(StateError, match="authority"):
        if cell:
            assert prepared is not None
            spec, runner = prepared
            core.run_explicit(
                replace(spec, context=(*spec.context, ("COPILOT_EXTENSIONS_CONTEXT", context))),
                runner=runner, root=root,
            )
        else:
            first_use.foreground(
                payload, installation, project, root=root, execution_contract=CONTRACT,
            )
    assert status(root=root) == ()
    assert not installation.exists()


@pytest.mark.parametrize("client_only", [False, True])
def test_foreground_receiver_retains_native_installer_authorization(
    owning_payload: tuple[Path, Path, Path], area: Path,
    monkeypatch: pytest.MonkeyPatch, client_only: bool,
) -> None:
    payload, installation, project = owning_payload
    root = area / "queue"
    spec, runner = first_use._prepare(
        payload, installation, project, root=root, foreground=True, client_only=client_only,
    )
    entry = Path(__file__).resolve().parents[2] / "installer-engine" / "installer_driver.py"
    module_spec = importlib.util.spec_from_file_location("_foreground_native_authorization", entry)
    assert module_spec is not None and module_spec.loader is not None
    driver = importlib.util.module_from_spec(module_spec)
    monkeypatch.syspath_prepend(str(entry.parent))
    module_spec.loader.exec_module(driver)
    args = argparse.Namespace(
        payload=payload, root=installation, shell=Path(sys.executable), plugin="agent-vault",
        action="provision", options=["NoService"] if client_only else [],
        check_native=True, remove_broker=False, cell_context=None,
    )

    def authorize(transaction: core.Transaction) -> int:
        for key, value in transaction.participant_env().items():
            monkeypatch.setenv(key, value)
        assert driver.execute(args) == 0
        with monkeypatch.context() as context:
            context.setattr(driver, "native_arguments", lambda _args: pytest.fail("lost custody executed"))
            context.delenv("UPDATE_DRIVER_WITNESS", raising=False)
            with pytest.raises(RuntimeError, match="live driver custody"):
                driver.execute(args)
        return 0

    request = run_explicit(spec, runner=runner, root=root, executor=authorize)
    assert request.state == "completed" and request.exit_code == 0
    assert request.spec.argv == (
        *(("--client-only",) if client_only else ()), "--execution-contract", CONTRACT,
    )
    assert [row.id for row in status(root=root)] == [request.id]
    assert not installation.exists()


@pytest.mark.parametrize("required", [None, "copilot-extensions.first-use-execution/2/foreground-finite-v1", "invalid"])
def test_foreground_receiver_required_contract_refuses_before_effects(
    area: Path, required: str | None,
) -> None:
    root = area / "queue"
    arguments = [
        "foreground", "--payload", str(area / "absent-payload"),
        "--runtime-root", str(area / "absent-installation"),
        "--project", str(area), "--root", str(root),
    ]
    if required is not None:
        arguments += ["--execution-contract", required]
    argv, imports = core.runtime_command("update_driver.first_use", arguments)
    result = subprocess.run(
        argv, env={**os.environ, **imports}, capture_output=True, text=True,
        timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "execution-contract" in result.stderr
    assert not root.exists() and not (area / "absent-installation").exists()


def test_foreground_receiver_legacy_parser_does_not_ignore_required_semantics(area: Path) -> None:
    argv, imports = core.runtime_command("update_driver.first_use", [
        "submit", "--payload", str(area / "absent"),
        "--runtime-root", str(area / "installation"), "--project", str(area),
        "--execution-contract", CONTRACT,
    ])
    result = subprocess.run(
        argv, env={**os.environ, **imports}, capture_output=True, text=True,
        timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr
    assert not (area / "installation").exists()


def test_foreground_receiver_busy_does_not_join_or_execute_background(
    owning_payload: tuple[Path, Path, Path], area: Path, request: pytest.FixtureRequest,
) -> None:
    payload, installation, project = owning_payload
    root = area / "queue"
    began = time.monotonic()
    capture = request.config.pluginmanager.getplugin("capturemanager")
    assert isinstance(capture, CaptureManager)

    def phase(name: str) -> None:
        with capture.global_and_fixture_disabled():
            print(f"[foreground-busy] {name} {time.monotonic() - began:.3f}s", flush=True)

    phase("enter")
    peer = first_use.enqueue(payload, installation, project, root=root)
    phase("peer-admitted")
    before = status(root=root)
    with pytest.raises(StateError, match="nothing replayed"):
        first_use.foreground(
            payload, installation, project, root=root, execution_contract=CONTRACT,
        )
    assert status(root=root) == before and not installation.exists()
    phase("busy-refused-without-effects")
    peer_effect = area / "controlled-peer-effect"

    def finish_peer(transaction: core.Transaction) -> int:
        # Queue isolation does not require two native provision flows in one item.
        # The selected legacy CLI controls separately execute the real peer adapter.
        transaction.assert_owned()
        assert transaction.request.id == peer.id
        assert transaction.request.spec == peer.spec and transaction.request.runner == peer.runner
        assert not installation.exists()
        peer_effect.write_text(transaction.request.id, encoding="utf-8")
        return 7

    drain(root=root, executor=finish_peer)
    assert status(peer.id, root=root).exit_code == 7
    assert peer_effect.read_text(encoding="utf-8") == peer.id
    assert not installation.exists()
    phase("peer-completed")
    result = first_use.foreground(
        payload, installation, project, root=root, execution_contract=CONTRACT,
        on_admitted=lambda _request: phase("continuation-admitted"),
    )
    phase("continuation-completed")
    assert result.id != peer.id and result.exit_code == 7
    assert result.spec.backend == peer.spec.backend
    assert result.spec.argv != peer.spec.argv
    assert status(peer.id, root=root).exit_code == 7
    assert [row.id for row in status(root=root)] == [peer.id, result.id]
    assert peer_effect.read_text(encoding="utf-8") == peer.id
    assert (installation / "executed").read_text() == "provision"
    frozen_scripts = Path((installation / "source-path").read_text().rstrip("\r\n"))
    assert frozen_scripts.parent == root / "provision-inputs" / result.id
    extension = "ps1" if os.name == "nt" else "sh"
    assert (frozen_scripts / f"install.{extension}").read_bytes() == (
        payload / "scripts" / f"install.{extension}"
    ).read_bytes()


def test_foreground_receiver_killed_after_commit_never_replays(
    owning_payload: tuple[Path, Path, Path], area: Path,
) -> None:
    payload, installation, project = owning_payload
    root = area / "queue"
    marker = area / "admitted"
    argv, imports = command(payload, installation, project, root)
    script = Path(__file__).with_name("fixture_first_use_foreground.py")
    controlled, controlled_imports = core.runtime_script_command(
        script, [str(marker), *argv[argv.index("foreground"):]],
    )
    caller = subprocess.Popen(
        controlled, env={**os.environ, **imports, **controlled_imports}, cwd=project,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", **no_window_kwargs(),
    )
    try:
        observe(marker.exists)
        admitted = status(root=root)
        assert len(admitted) == 1 and admitted[0].state == "running"
        caller.kill()
        out, err = caller.communicate(timeout=5)
        assert caller.returncode != 0, out + err
        receipt = json.loads(out)
        assert receipt["request_id"] == admitted[0].id
        with pytest.raises(InterruptedError):
            drain(root=root, executor=lambda tx: pytest.fail("abandoned foreground replay"))
        original = status(admitted[0].id, root=root)
        assert original.state == "interrupted" and original.exit_code is None
        assert original.unresolved and original.settled_at is None
        assessment = recovery_status(root=root)
        assert not assessment.eligible_ids
        assert [(item.request_id, item.reason) for item in assessment.refused] == [
            (original.id, "same_kernel_boot"),
        ]
        assert not installation.exists()
    finally:
        if caller.poll() is None:
            caller.kill()
        caller.communicate(timeout=5)


def test_foreground_receiver_caller_death_during_inspection_stops_before_admission(
    owning_payload: tuple[Path, Path, Path], area: Path,
) -> None:
    payload, installation, project = owning_payload
    marker = area / "inspection-pid"
    helper = payload / "scripts" / "installation-context" / "installation_context.py"
    body = helper.read_text(encoding="utf-8")
    insertion = "from __future__ import annotations\n"
    assert body.count(insertion) == 1
    helper.write_text(body.replace(
        insertion,
        insertion + "import os as _fixture_os, time as _fixture_time\n"
        "from pathlib import Path as _FixturePath\n"
        f"_fixture_marker = _FixturePath({str(marker)!r})\n"
        "_fixture_pending = _fixture_marker.with_suffix('.pending')\n"
        "_fixture_pending.write_text(str(_fixture_os.getpid()))\n"
        "_fixture_pending.replace(_fixture_marker)\n"
        "_fixture_time.sleep(20)\n",
        1,
    ), encoding="utf-8")
    root = area / "queue"
    argv, imports = command(payload, installation, project, root)
    caller = subprocess.Popen(
        argv, env={**os.environ, **imports}, cwd=project, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        **no_window_kwargs(),
    )
    try:
        observe(marker.exists)
        with product_exit(int(marker.read_text())) as exited:
            caller.kill()
            out, err = caller.communicate(timeout=5)
            assert exited(), "the exact inspecting child survived its foreground caller"
        assert not out, err
        assert not root.exists() and not installation.exists()
    finally:
        if caller.poll() is None:
            caller.kill()
        caller.communicate(timeout=5)


def test_foreground_receiver_caller_death_stops_only_its_finite_tree(
    owning_payload: tuple[Path, Path, Path], area: Path,
) -> None:
    payload, installation, project = owning_payload
    scripts = payload / "scripts"
    if os.name == "nt":
        path = scripts / "install.ps1"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "exit 7\n",
            "[IO.File]::WriteAllText((Join-Path $InstallDir 'pid'), [string]$PID)\n"
            "Start-Sleep -Seconds 20\n"
            "[IO.File]::WriteAllText((Join-Path $InstallDir 'unexpected-completion'), 'bad')\n"
            "exit 7\n",
        ), encoding="utf-8")
    else:
        path = scripts / "install.sh"
        path.write_text(path.read_text(encoding="utf-8").replace(
            "exit 7\n",
            'printf "%s" "$$" >"$3/pid"\n'
            'sleep 20\nprintf "bad" >"$3/unexpected-completion"\nexit 7\n',
        ), encoding="utf-8")
    root = area / "queue"
    argv, imports = command(payload, installation, project, root)
    caller = subprocess.Popen(
        argv, env={**os.environ, **imports}, cwd=project, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        **no_window_kwargs(),
    )
    unrelated: subprocess.Popen[bytes] | None = None
    try:
        marker = installation / "pid"

        def started() -> bool:
            if marker.is_file() and marker.read_text():
                return True
            if caller.poll() is not None:
                out, err = caller.communicate(timeout=5)
                logs = {
                    path.name: path.read_text(encoding="utf-8", errors="replace")
                    for path in root.glob("*.log")
                }
                pytest.fail(f"controlled receiver exited before product entry: {out} {err} {logs}")
            return False

        try:
            observe(lambda: any(root.glob("*.log")))
            observe(started)
        except AssertionError:
            rows = status(root=root) if (root / "queue.sqlite3").exists() else ()
            logs = {
                path.name: path.read_text(encoding="utf-8", errors="replace")
                for path in root.glob("*.log")
            }
            pytest.fail(f"controlled entry not observed: exit={caller.poll()} rows={rows} logs={logs}")
        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"], cwd=area,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        with product_exit(int(marker.read_text())) as exited:
            caller.kill()
            out, err = caller.communicate(timeout=5)
            assert exited(), "the exact inert finite product survived its foreground caller"
        receipt = json.loads(out)
        original = wait(receipt["request_id"], root=root, timeout=5)
        assert original.state == "interrupted" and original.exit_code is None, err
        assert original.unresolved and original.settled_at is None
        assert not (installation / "unexpected-completion").exists()
        assert unrelated.poll() is None
    finally:
        for process in (caller, unrelated):
            if process is None:
                continue
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("receipt_failure", [False, True])
def test_foreground_receiver_admission_failure_cannot_be_replayed(
    area: Path, monkeypatch: pytest.MonkeyPatch, receipt_failure: bool,
) -> None:
    from update_driver import core

    root = area / "state"
    interruption = KeyboardInterrupt("admitted owner interrupted before executor entry")

    def interrupt_before_start(**kwargs: Any) -> int:
        assert len(status(root=root)) == 1
        raise interruption

    def interrupt_receipt(request: core.Request) -> None:
        interrupt_before_start()

    with monkeypatch.context() as context:
        if not receipt_failure:
            context.setattr(core, "drain", interrupt_before_start)
        with pytest.raises(KeyboardInterrupt) as failure:
            run_explicit(
                RequestSpec("source-recovery", source="original-selection"),
                runner=TrustedRunner((sys.executable, "-c", "pass"), str(area)), root=root,
                on_admitted=interrupt_receipt if receipt_failure else None,
            )
    assert failure.value is interruption
    request = status(getattr(failure.value, "request_id"), root=root)
    assert request.state == "interrupted" and request.exit_code is None
    assert request.unresolved and request.settled_at is None
    with pytest.raises(InterruptedError):
        drain(root=root, executor=lambda tx: pytest.fail("abandoned foreground replay"))
    assert status(request.id, root=root) == request


@pytest.mark.parametrize("linked", ["completion", "bootstrap-directory"])
def test_foreground_receiver_retained_invocation_rejects_linked_entries(
    area: Path, monkeypatch: pytest.MonkeyPatch, linked: str,
) -> None:
    monkeypatch.setattr(sys, "prefix", str(area))
    monkeypatch.setattr(agent_procutil, "direct_python", lambda executable: (executable, {}))
    directory = linked == "bootstrap-directory"
    target = area / "target"
    if directory:
        target.mkdir()
    else:
        target.write_text("fixture", encoding="utf-8")
    link = area / (".runtime-bootstrap" if directory else ".install-complete.json")
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"controlled symlink creation unavailable: {error}")
    with pytest.raises(StateError, match="linked or unsafe|missing its retention bootstrap"):
        core.runtime_command("update_driver.first_use")
