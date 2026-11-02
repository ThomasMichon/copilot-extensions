"""Actual cell Task qualification inside the admitted disposable Windows lane."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path


def qualify(outer: Path, controller: Path, runtime, pu, callers, children, jobs) -> dict:
    from windows_broker_protocol_acceptance import admit, fixture_environment

    admit(outer, True)
    from agent_procutil import windows_broker_installation as installation
    from agent_procutil import windows_task_broker as broker
    from agent_procutil.daemon_broker import _outside_job
    from agent_procutil.windows_broker_native import NativeChild, _duplicate
    from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
    from zdd.private_protocol import Deadline
    from zdd.private_windows_native import native

    source = Path(__file__).resolve().parents[1]
    profile = outer.with_name(outer.name + "-cell-profile")
    durable = outer.with_name(outer.name + "-cell-state")
    payload = outer.with_name(outer.name + "-cell-payload")
    package = payload / "src" / "agent_pull_requests"
    package.mkdir(parents=True)
    original = source / "plugins" / "agent-pull-requests"
    for name in ("plugin.json", "pyproject.toml"):
        shutil.copyfile(original / name, payload / name)
    for fragment in (original / "src" / "agent_pull_requests").glob("_installation_context*.py"):
        shutil.copyfile(fragment, package / fragment.name)
    spec = importlib.util.spec_from_file_location("_native_cell_fixture", package / "_installation_context.py")
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    spec.loader.exec_module(api)
    version = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))["version"]
    receipt = api.stamp_context(
        payload_root=payload, plugin_id="agent-pull-requests", payload_version=version,
        payload_origin="explicit", expected_namespace_generation=0,
        expected_install_generation=0, source_descriptor={
            "source": "github", "repo": "example/marketplace",
        }, marketplace_key="example", durable_home=durable, environment={},
    )
    policy = profile / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    }), encoding="utf-8")
    api.compare_and_swap_activation(
        context=receipt["installReceipt"], expected_marketplace_id=receipt["marketplaceId"],
        expected_plugin_id="agent-pull-requests",
        expected_namespace_generation=receipt["namespaceGeneration"],
        expected_install_generation=receipt["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
        durable_home=durable, os_profile=profile,
    )
    root = Path(receipt["pluginRoot"])
    snapshot = root / "snapshots" / "cold"
    scripts = snapshot / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(payload / "pyproject.toml", snapshot / "pyproject.toml")
    shutil.copytree(package, snapshot / "src" / "agent_pull_requests")
    for helper in (source / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(helper, scripts / helper.name)
    for name in ("runtime_launcher.py", "installer-engine.ps1", "installer-engine.sh"):
        shutil.copyfile(source / "libs" / "installer-engine" / name, scripts / name)
    common = {
        "context": receipt["installReceipt"], "durable_home": durable,
        "expected_marketplace_id": receipt["marketplaceId"],
        "expected_plugin_id": "agent-pull-requests",
        "expected_payload_root": payload, "expected_payload_version": version,
        "snapshot_id": "cold", "environment": {},
    }
    api.stamp_snapshot_provenance(
        **{key: value for key, value in common.items()
           if key not in {"expected_payload_root", "expected_payload_version"}},
        expected_namespace_generation=receipt["namespaceGeneration"],
        expected_install_generation=receipt["generation"],
    )
    slot_args = {**common, "runtime_version": "1.0.0"}
    slot = Path(api.provision_runtime_slot(**slot_args)["slotRoot"])
    digest = api._snapshot_content_sha256(snapshot)
    with runtime.construction_lease(root, slot.name):
        venv.EnvBuilder(with_pip=False).create(slot)
        packages = slot / "Lib" / "site-packages"
        for item in controller.iterdir():
            if item.is_dir():
                shutil.copytree(item, packages / item.name)
        runtime.retain_runtime_helpers(
            slot, complete=False,
            context_source=snapshot / "src" / "agent_pull_requests" / "_installation_context.py",
        )
        runtime.mark_complete(root, slot.name, payload_hash=digest)
        api.complete_runtime_slot(**slot_args)
    execution = outer.with_name(outer.name + "-cell-execution")

    def operation(action: str):
        code = (
            "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
            "from update_driver.windows_broker import adopt,remove;"
            f"{'adopt(Path(sys.argv[2]),Path(sys.argv[3]),root=Path(sys.argv[4]))' if action == 'adopt' else 'remove(Path(sys.argv[2]),root=Path(sys.argv[4]))'}"
        )
        request = enqueue(
            RequestSpec("cell-broker-" + action, cwd=str(outer)),
            runner=TrustedRunner((
                sys.executable, "-I", "-c", code, str(controller), str(root),
                str(slot), str(execution),
            ), str(outer)), root=execution,
        )
        drain(root=execution)
        result = status(request.id, root=execution)
        if result.state != "completed" or result.exit_code != 0:
            raise RuntimeError("actual cell broker " + action + " failed: " + result.log_path.read_text()[-8192:])

    config = installation.registration(root, slot)
    try:
        operation("adopt")
        before = (slot / ".runtime-slot-completion.json").read_bytes()
        api.validate_runtime_slot_completion(**slot_args)
        runtime.retain_runtime_helpers(
            slot, complete=True,
            context_source=snapshot / "src" / "agent_pull_requests" / "_installation_context.py",
        )
        operation("adopt")
        if (slot / ".runtime-slot-completion.json").read_bytes() != before:
            raise RuntimeError("cell repeat adoption rewrote immutable completion")
        shutil.rmtree(payload)
        pending = []
        for index in range(2):
            caller_root = outer / ("ce-broker-probe-cell-" + str(index))
            caller_root.mkdir()
            shutil.copyfile(outer / "resident.py", caller_root / "resident.py")
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [
                    sys.executable, "-I", "-S",
                    str(Path(__file__).with_name("windows_broker_protocol_acceptance.py")),
                    "caller", str(packages), str(caller_root),
                ], require_job=True, allow_breakaway=False,
                env=fixture_environment(dict(os.environ), control=True),
                cwd=caller_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, creationflags=0x08000000,
            )
            callers.append(process)
            if job is None:
                raise RuntimeError("cell caller native Job unavailable")
            jobs.append(job)
            pending.append((process, job, caller_root))
        for process, _job, _caller_root in pending:
            process.stdin.write("launch\n")
            process.stdin.flush()
        adopted_children = []
        for process, job, caller_root in pending:
            offer_path = caller_root / "caller-offer.json"
            deadline = time.monotonic() + 40
            while not offer_path.exists():
                if process.poll() is not None:
                    raise RuntimeError("cell installed caller failed: " + process.stderr.read()[-8192:])
                if time.monotonic() >= deadline:
                    raise TimeoutError("cell native custody transfer deadline")
                time.sleep(0.025)
            offer = json.loads(offer_path.read_text(encoding="utf-8"))
            handle = _duplicate(int(process._handle), offer["source_handle"])
            child = NativeChild.from_handle(handle, offer["pid"], ["owned-cell-resident"])
            children.append(child)
            identity = native().identity(handle, child.pid)
            if identity.start_time != offer["birth"] or not _outside_job(child):
                raise RuntimeError("cell actual child identity/lifetime refused")
            adopted_children.append(child)
        if len({child.pid for child in adopted_children}) != 2:
            raise RuntimeError("concurrent cell requests did not return distinct residents")
        for process, job, _caller_root in pending:
            job.terminate_and_wait(5)
            job.close()
            process.wait(timeout=5)
        for child in adopted_children:
            if child.poll() is not None:
                raise RuntimeError("cell resident died with caller Job")
            child.retire(Deadline(5))
            child.close()
    finally:
        if config.config_path.exists():
            operation("remove")
    if config.config_path.exists() or broker.task_operation(
        "inspect", config, Deadline(10),
    ) != {"installed": False, "accepting": False}:
        raise RuntimeError("cell owned Task removal was not confirmed")
    return {
        "actual_cell_factory": True, "retained_api_and_completion": True,
        "cold_and_repeat_task_adoption": True, "payload_disappeared_before_launch": True,
        "installed_module_and_task_entrypoint": True, "caller_job_closed": True,
        "actual_child_survived_and_retired": True, "owned_task_removed": True,
        "concurrent_distinct_residents": 2,
        "synthetic_application_packages": True,
    }
