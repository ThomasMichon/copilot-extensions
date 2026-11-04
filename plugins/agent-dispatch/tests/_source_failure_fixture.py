"""Finite installed-code owner controlling real attributed passive candidates."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time


def main():
    from agent_procutil import DaemonSpawnError
    from agent_procutil.daemon_instances import admit_daemon_instance, cleanup_instance, inspect_instances
    from agent_procutil._daemon_instance_identity import birth_identity, stop_identity
    from agent_dispatch import __version__, supervisor_source as source
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.pool_source_snapshot import DeclarationRead
    from agent_dispatch.remote_dispatch import local_machine
    from agent_dispatch.single_instance import is_locked, lock_path_for, read_holder_pid
    from agent_dispatch.source_start import _path_name
    from agent_dispatch.supervisor_daemon import SupervisorDaemon
    from agent_dispatch.supervisor_registration import supervisor_lease_scope
    from zdd import routing

    request = json.loads(sys.argv[1])
    root = Path(request["root"])
    prefix = _path_name(Path(sys.prefix))
    assert _path_name(Path(source.__file__).resolve()).is_relative_to(prefix.resolve())
    assert __version__ == request["original_version"] == prefix.parent.name
    version = request["version"]
    python = root / "versions" / version / ".venv" / "Scripts" / "python.exe"
    spec = importlib.util.spec_from_file_location(
        "native_fault_runtime", prefix.parent / ".runtime-bootstrap" / "versioned_runtime.py",
    )
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    spec.loader.exec_module(runtime)
    endpoint = routing.Endpoint.from_dict(routing.read_table(root / "routes")["active"])
    client = DispatchClient(endpoint.base_url, token=os.environ["AGENT_DISPATCH_TOKEN"],
                            control_token=os.environ["AGENT_DISPATCH_CONTROL_TOKEN"])
    result = {
        "original_version": __version__, "candidate_version": version,
        "prefix": str(prefix), "module": source.__file__,
        "controller": {"pid": os.getpid(), "birth": birth_identity(os.getpid())},
        "cases": [], "attempts": [], "cleanup": [],
    }

    def owner(name, *, target=None, cooldown=0):
        argv = ["supervise", "serve", "--machine", local_machine(), "--env", name, "--interval", "5"]
        options = {} if target is None else {"self_update_stale_target": lambda *_: target[0]}
        return SupervisorDaemon(
            client, local_machine(), name, poll_interval=0.5,
            declared_source=lambda: DeclarationRead((), True),
            self_update_enabled=True, self_update_poll_interval=0,
            self_update_cooldown=cooldown, self_update_argv=argv,
            self_update_install_dir=lambda: root, **options,
        )

    def held(daemon):
        path = lock_path_for(root / "control", supervisor_lease_scope(daemon.machine, daemon.env))
        assert is_locked(path) and read_holder_pid(path) == os.getpid()

    def prepared(daemon):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert daemon._maybe_self_update() is False
            state = daemon._source_handoff
            if state is not None and state.get("prepared") is not None:
                held(daemon)
                return state
            time.sleep(0.05)
        raise AssertionError("real candidate did not prepare before original ownership")

    def settlement(child):
        receipt = child.instance_receipt
        verdict = cleanup_instance(receipt, terminate=False)
        assert verdict.status in {"settled", "missing"}
        result["cleanup"].append({**asdict(verdict), "registry_root": str(receipt.registry_root)})

    with admit_daemon_instance(
        "dispatch-installed-failure-controller", runtime_root=root, source=__file__,
        purpose="unattended-test",
    ) as guard:
        try:
            for case in ("selection-flap", "candidate-exit"):
                if request.get("remaining_only"):
                    continue
                daemon = owner("native-" + case)
                pool = ThreadPoolExecutor(max_workers=1)
                start, release = threading.Event(), threading.Event()
                future = pool.submit(start.wait)
                future.add_done_callback(lambda _future, release=release: release.wait(90))
                daemon._runtime_executor = pool
                daemon._probe_inflight.add(future)
                assert daemon.acquire_singleton()
                original_cancel = source._cancel_owned_candidate
                try:
                    start.set()
                    state = prepared(daemon)
                    assert state["phase"] == "withdrawing" and daemon._pool_metadata_closed.is_set()
                    child = state["child"]
                    ready = state["prepared"]
                    assert child.poll() is None
                    native = next(row for row in inspect_instances(child.instance_receipt.registry_root)
                                  if row["instance_id"] == child.instance_receipt.instance_id)
                    assert native["state"] == "running"
                    item = {
                        "case": case, "prepared": ready, "native_before": native,
                        "returned_child_pid": child.pid, "returned_child_type": type(child).__name__,
                    }
                    result["attempts"].append(item)
                    if case == "selection-flap":
                        entered, proceed = threading.Event(), threading.Event()

                        def observe_cancel(*args):
                            if not item.get("selected_again_while_candidate_live"):
                                entered.set()
                                assert proceed.wait(10)
                                assert runtime.current_version(root) == version
                                assert child.poll() is None
                                item["selected_again_while_candidate_live"] = True
                            before = cleanup_instance(child.instance_receipt, terminate=False)
                            observed = original_cancel(*args)
                            item.setdefault("cancel_observations", []).append({
                                "sdk_before": asdict(before), "cancel_result": observed,
                                "child_exit_after": child.poll(),
                            })
                            return observed

                        source._cancel_owned_candidate = observe_cancel
                        runtime.activate(root, request["original_version"], link_free=True)
                        with ThreadPoolExecutor(max_workers=1) as control:
                            attempt = control.submit(daemon._maybe_self_update)
                            assert entered.wait(10)
                            runtime.activate(root, version, link_free=True)
                            release.set()
                            proceed.set()
                            assert attempt.result(timeout=30) is False
                    else:
                        assert stop_identity(child.pid, ready["birth"])
                        assert child.poll() is not None
                        release.set()
                        assert daemon._maybe_self_update() is False
                    deadline = time.monotonic() + 60
                    while daemon._source_handoff is not None:
                        assert time.monotonic() < deadline
                        assert daemon._maybe_self_update() is False
                        time.sleep(0.05)
                    assert child.poll() is not None
                    assert daemon._source_handoff is None and not daemon._pool_metadata_closed.is_set()
                    held(daemon)
                    settlement(child)
                    result["cases"].append({
                        **item, "native_exit": child.returncode,
                        "admission_restored_with_original_singleton": True,
                    })
                finally:
                    source._cancel_owned_candidate = original_cancel
                    release.set()
                    pool.shutdown(wait=True, cancel_futures=True)
                    runtime.activate(root, version, link_free=True)
                    state = daemon._source_handoff
                    if state and state.get("child") is not None:
                        assert original_cancel(state["child"], root)
                    daemon.shutdown()
                    daemon.release_singleton()

            original_spawn = source.spawn_candidate
            refusal = [True]
            native_attempts = []
            daemon = owner("native-nonstart", cooldown=1)
            assert daemon.acquire_singleton()
            try:
                def native_refusal(*args, **kwargs):
                    if not refusal[0]:
                        return original_spawn(*args, **kwargs)
                    from agent_procutil.daemon_instances import DaemonInstanceSpec, spawn_instance

                    missing = root / "profile" / "fixture-missing-python.exe"
                    assert not missing.exists()

                    def denied(command, **options):
                        native_attempts.append(str(missing))
                        return subprocess.Popen([str(missing), *command[1:]], **options)

                    try:
                        spawn_instance(
                            [str(python), "-I", "-B", "-c", "pass"],
                            instance=DaemonInstanceSpec(
                                "dispatch-native-create-refusal", root, __file__,
                                purpose="unattended-test", lifecycle="finite-worker",
                            ),
                            popen=denied, cwd=root, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    except FileNotFoundError as error:
                        # spawn_instance cancelled its reservation before returning the OS refusal.
                        raise DaemonSpawnError(
                            "confirmed fixture native creation refusal",
                            process_created=False, cleanup_confirmed=True, admission_sent=False,
                        ) from error
                    raise AssertionError("missing executable unexpectedly created a process")

                source.spawn_candidate = native_refusal
                assert daemon._maybe_self_update() is False
                assert daemon._source_handoff is None
                stamp = daemon._self_update_last_spawn
                assert stamp is not None
                result["inventory_after_nonstart"] = [
                    {"instance_id": row["instance_id"], "state": row["state"]}
                    for row in inspect_instances(guard.receipt.registry_root)
                ]
                refusal[0] = False
                assert daemon._maybe_self_update() is False and daemon._source_handoff is None
                assert len(native_attempts) == 1
                time.sleep(max(0, stamp + 1.1 - time.time()))
                pending = threading.Event()
                pool = ThreadPoolExecutor(max_workers=1)
                future = pool.submit(pending.wait)
                daemon._runtime_executor = pool
                daemon._probe_inflight.add(future)
                try:
                    state = prepared(daemon)
                    assert state["child"].poll() is None
                    result["cases"].append({
                        "case": "confirmed-nonstart-cooldown", "retry_stamp": stamp,
                        "prepared_after_retry": state["prepared"],
                        "native_refusal_count": len(native_attempts),
                        "seam": "SDK spawn_instance process factory invokes actual OS missing-executable refusal and reservation cancellation",
                    })
                finally:
                    source.spawn_candidate = original_spawn
                    pending.set()
                    pool.shutdown(wait=True, cancel_futures=True)
            finally:
                state = daemon._source_handoff
                if state and state.get("child") is not None:
                    assert source._cancel_owned_candidate(state["child"], root)
                    settlement(state["child"])
                daemon.shutdown()
                daemon.release_singleton()

            original_spawn = source.spawn_candidate
            daemon = owner("native-unknown")
            assert daemon.acquire_singleton()
            sentinel = []
            release_unknown = root / "profile" / "unknown-control.done"
            assert not release_unknown.exists()
            try:
                def lost_reply(*_args, **_kwargs):
                    from agent_dispatch.procutil import spawn_dispatch_daemon

                    child = spawn_dispatch_daemon(
                        [str(python), "-I", "-B", "-c",
                         "import pathlib,time\np=pathlib.Path(" + repr(str(release_unknown)) + ")\n"
                         "deadline=time.monotonic()+60\n"
                         "while not p.exists() and time.monotonic()<deadline: time.sleep(0.05)"],
                        log_name="logs/native-unknown-control.log", cwd=root,
                    )
                    sentinel.append(child)
                    error = DaemonSpawnError(
                        "fixture lost native creation reply", cleanup_confirmed=False,
                        process_created=True, process_identity=(child.pid, birth_identity(child.pid)),
                    )
                    error.instance_receipt = child.instance_receipt
                    raise error

                source.spawn_candidate = lost_reply
                assert daemon._maybe_self_update() is False
                state = daemon._source_handoff
                assert state["phase"] == "unconfirmed"
                assert daemon._maybe_self_update() is False
                assert len(sentinel) == 1 and sentinel[0].poll() is None
                assert daemon._source_handoff is state
                held(daemon)
                result["cases"].append({
                    "case": "unknown-live-no-preparation-control",
                    "pid": sentinel[0].pid, "birth": birth_identity(sentinel[0].pid),
                    "no_replay_or_subject_termination": True,
                    "limit": "native SDK sentinel without app preparation; injected lost reply, not OS permission denial",
                })
            finally:
                source.spawn_candidate = original_spawn
                release_unknown.touch()
                for child in sentinel:
                    assert child.wait(timeout=10) == 0
                    settlement(child)
                daemon.shutdown()
                daemon.release_singleton()
            assert client.get(request["held_id"]) == request["held_before"]
            assert client.reserve_spawn(request["reserved_id"])["reservation"] == request["reservation"]
            result["held_work_preserved"] = True
        finally:
            result["selected_after"] = runtime.current_version(root)
            result["completed"] = len(result["cases"]) == (2 if request.get("remaining_only") else 4)
            Path(request["output"]).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
