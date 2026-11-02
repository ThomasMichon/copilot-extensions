"""Real source-only daemon consumers; run inside the disposable scenario box."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def wait_until(predicate, *, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("the expected lifecycle transition was not observed")


def load_api(source: Path):
    sys.path.insert(0, str(source / "libs" / "agent-procutil" / "src"))
    from agent_procutil import daemon_instances

    return daemon_instances


def start_worker(api, source, root, registry, service, overlay=None, *, abrupt=False):
    from agent_procutil import no_window_flags

    runtime = root / "runtime"
    runtime.mkdir(mode=0o700, parents=True)
    ready = root / "ready.json"
    policy = api.DaemonLeasePolicy(ttl_seconds=1.5, max_lifetime_seconds=6)
    spec = api.DaemonInstanceSpec(
        service=service,
        runtime_root=runtime,
        source=Path(__file__).with_name("worker.py"),
        purpose="unattended-test" if overlay is not None else "production",
        owner="clean-room-run" if overlay is not None else None,
        registry_root=registry,
        lease=policy if overlay is not None else None,
    )
    command = [
        sys.executable, "-B", str(Path(__file__).with_name("worker.py")),
        "--source", str(source), "--runtime", str(runtime),
        "--ready", str(ready), "--service", service,
    ]
    if abrupt:
        command.append("--abrupt-exit")
    env = dict(os.environ)
    if overlay is not None:
        env.update(overlay)
    process = api.spawn_instance(
        command, instance=spec, env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, creationflags=no_window_flags(),
    )
    try:
        wait_until(lambda: ready.is_file() or process.poll() is not None)
        if not ready.is_file():
            records = [
                {key: row.get(key) for key in ("service", "purpose", "state", "pid")}
                for row in api.inspect_instances(registry)
                if row.get("instance_id") == process.instance_receipt.instance_id
            ]
            raise AssertionError(
                f"{service} exited {process.returncode} before admission: "
                f"{process.stderr.read()[:1200]}; records={records}"
            )
        state = json.loads(ready.read_text(encoding="utf-8"))
        assert state["instance_id"] == process.instance_receipt.instance_id
        return process, process.instance_receipt, runtime, state
    except BaseException:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        process.stderr.close()
        api.cleanup_instance(process.instance_receipt)
        raise


def stop_owned(api, process, receipt):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)
    if process.stderr is not None:
        process.stderr.close()
    return api.cleanup_instance(receipt)


def owner_fixture(args) -> None:
    api = load_api(args.source)
    policy = api.DaemonLeasePolicy(ttl_seconds=1.5, max_lifetime_seconds=6)
    with api.daemon_test_lifecycle(
        owner="clean-room-run", registry_root=args.work_root / "registry", lease=policy,
    ) as overlay:
        _process, receipt, _, state = start_worker(
            api, args.source, args.work_root / "owner-loss",
            args.work_root / "registry", "proof-owner-loss", overlay,
        )
        state["registry"] = str(receipt.registry_root)
        print(json.dumps(state), flush=True)
        os._exit(0)


def explicit_interactive_policy(api, source, root):
    defaults = api.DaemonLeasePolicy()
    assert defaults.ttl_seconds == 600
    assert defaults.max_lifetime_seconds == 14400
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700, parents=True)
    try:
        spec = api.DaemonInstanceSpec(
            service="proof-interactive",
            runtime_root=runtime,
            source=Path(__file__).with_name("worker.py"),
            purpose="interactive-test",
            owner="clean-room-run",
            registry_root=root / "registry",
        )
        receipt = api.reserve_instance(spec)
    except ValueError as error:
        assert "interactive" in str(error).lower(), error
    else:
        api.cancel_instance_reservation(receipt, creation_refused=True)
        raise AssertionError("interactive testing silently inherited unattended defaults")
    explicit = api.DaemonLeasePolicy(ttl_seconds=600, max_lifetime_seconds=172800)
    receipt = api.reserve_instance(
        api.DaemonInstanceSpec(
            service="proof-interactive-explicit",
            runtime_root=runtime,
            source=Path(__file__).with_name("worker.py"),
            purpose="interactive-test",
            owner="clean-room-run",
            registry_root=root / "registry",
            lease=explicit,
        )
    )
    try:
        record = {}
        for line in receipt.record_path.read_text(encoding="utf-8").splitlines():
            key, value = line.split("\t", 1)
            record[key] = base64.b64decode(value, validate=True).decode("utf-8")
        assert float(record["hard_deadline"]) - float(record["created"]) >= 172799
    finally:
        api.cancel_instance_reservation(receipt, creation_refused=True)


def hard_cap_and_cross_root(api, source, root):
    registry = root / "shared-registry"
    policy = api.DaemonLeasePolicy(ttl_seconds=1.5, max_lifetime_seconds=6)
    production = start_worker(
        api, source, root / "production", registry, "proof-production"
    )
    try:
        with api.daemon_test_lifecycle(
            owner="clean-room-run", registry_root=registry, lease=policy,
        ) as overlay:
            process, receipt, runtime, _ = start_worker(
                api, source, root / "ephemeral", registry, "proof-ephemeral", overlay,
            )
            try:
                rows = api.inspect_instances(registry)
                assert {row["service"] for row in rows} >= {
                    "proof-production", "proof-ephemeral"
                }
                proof = runtime / "retained-proof.txt"
                proof.write_text("keep this evidence\n", encoding="utf-8")
                time.sleep(2)
                assert process.poll() is None, "owner renewal failed before the hard cap"
                process.wait(timeout=10)
                result = api.cleanup_instance(receipt)
                assert result.status in ("settled", "missing"), result
                assert not (runtime / "owned-runtime-state.txt").exists()
                assert proof.read_text(encoding="utf-8") == "keep this evidence\n"
                assert production[0].poll() is None, "test expiry affected production"
                protected = api.cleanup_instance(production[1])
                assert protected.status in ("held", "live"), protected
                assert production[0].poll() is None
            finally:
                stop_owned(api, process, receipt)
    finally:
        stop_owned(api, production[0], production[1])


def abrupt_owner_loss(api, source, root):
    from agent_procutil import no_window_flags

    command = [
        sys.executable, "-B", str(Path(__file__)), "--source", str(source),
        "--work-root", str(root), "--owner-fixture",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=15,
        creationflags=no_window_flags(),
    )
    assert result.returncode == 0, result.stderr[-1200:]
    state = json.loads(result.stdout)
    receipt = api.DaemonInstanceReceipt(state["instance_id"], Path(state["registry"]))
    try:
        wait_until(
            lambda: api.cleanup_instance(receipt, terminate=False).status
            in ("settled", "missing"),
            seconds=12,
        )
    finally:
        settled = api.cleanup_instance(receipt)
        assert settled.status in ("settled", "missing"), settled


def installer_reclamation(api, source, root, helpers):
    assert helpers is not None, "the source-only lifecycle helper root must be selected"
    sys.path.insert(0, str(source / "libs" / "installer-engine"))
    from installer_instance_reconcile import reconcile_installer_instances

    registry = root / "registry"
    policy = api.DaemonLeasePolicy(ttl_seconds=1.5, max_lifetime_seconds=6)
    with api.daemon_test_lifecycle(
        owner="clean-room-run", registry_root=registry, lease=policy,
    ) as overlay:
        process, receipt, runtime, _ = start_worker(
            api, source, root / "orphan", registry, "proof-installer", overlay,
            abrupt=True,
        )
        try:
            process.wait(timeout=5)
            proof = runtime / "retained-proof.txt"
            proof.write_text("not disposable\n", encoding="utf-8")
            time.sleep(6.2)
            reconcile_installer_instances(helpers, registry_root=registry)
            assert not (runtime / "owned-runtime-state.txt").exists(), (
                "the real installer adapter did not settle the declared runtime artifact"
            )
            assert proof.read_text(encoding="utf-8") == "not disposable\n"
            reconcile_installer_instances(helpers, registry_root=registry)
            assert proof.is_file(), "repeat install removed retained evidence"
        finally:
            stop_owned(api, process, receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--owner-fixture", action="store_true")
    parser.add_argument("--helper-root", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux" or not Path("/.dockerenv").is_file():
        parser.error("run this proof in its disposable Linux Docker container")
    if args.owner_fixture:
        owner_fixture(args)
        return 0
    assert not any(shutil.which(name) for name in ("cc", "gcc", "clang", "c++")), (
        "the compiler-free proof requires compilers absent, not merely unused"
    )
    assert not args.work_root.exists(), "proof requires a fresh workspace"
    args.work_root.mkdir(mode=0o700, parents=True)
    api = load_api(args.source)
    helpers = args.helper_root
    if helpers is None:
        helpers = args.work_root / "source-helpers"
        helpers.mkdir(mode=0o700)
        source_helpers = args.source / "libs" / "update-driver" / "scripts"
        for name in ("daemon_instances.sh", "daemon_instance_runtime.pyz"):
            original = source_helpers / name
            assert original.is_file() and not original.is_symlink(), "unsafe source helper"
            destination = helpers / name
            destination.write_bytes(original.read_bytes())
            destination.chmod(0o644)
            assert destination.read_bytes() == original.read_bytes(), "source publication differs"
    assert (helpers / "daemon_instances.sh").is_file()
    assert (helpers / "daemon_instance_runtime.pyz").is_file()
    print("PROBE: compiler-free-source-inputs PASS Python sources and no compiler", flush=True)
    checks = [
        ("interactive-policy-and-unattended-defaults", explicit_interactive_policy),
        ("cross-root-renewal-hard-cap", hard_cap_and_cross_root),
        ("abrupt-owner-loss", abrupt_owner_loss),
        (
            "real-installer-artifact-reclamation",
            lambda api, source, root: installer_reclamation(api, source, root, helpers),
        ),
    ]
    failures = 0
    for name, check in checks:
        try:
            check(api, args.source, args.work_root / name)
        except Exception as error:
            failures += 1
            print(f"PROBE: {name} FAIL {type(error).__name__}: {error}", flush=True)
        else:
            print(f"PROBE: {name} PASS actual process and filesystem assertions", flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
