"""Opt-in real-Docker smoke test for the restricted transport boundary."""

from __future__ import annotations

import os
import hashlib
import json
import subprocess
import sys
import time
import uuid
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml

from agent_containers.config import FleetConfig
from agent_containers.fleet import _image_run
from agent_containers.lifecycle import (
    DockerContainerInfo,
    inspect_state,
    restricted_policy_errors,
)
from agent_procutil import no_window_flags

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_CONTAINERS_E2E") != "1",
    reason="set AGENT_CONTAINERS_E2E=1 to run real-Docker smoke",
)


def _prepare_payload_runtime(root: Path, expected_package: Path) -> None:
    runtime_version = version("agent-containers")
    slot = root / "versions" / runtime_version
    shutil.copytree(Path(sys.prefix), slot, symlinks=True)
    runtime_python = slot / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    probe = subprocess.run(
        [str(runtime_python), "-c", "import agent_containers;print(agent_containers.__file__)"],
        capture_output=True, text=True, timeout=30, creationflags=no_window_flags(),
    )
    assert probe.returncode == 0, probe.stderr
    assert Path(probe.stdout.strip()).resolve() == expected_package.resolve()
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": runtime_version, "completed_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
    }), encoding="utf-8")
    (root / "current-version").write_text(runtime_version, encoding="utf-8")


def test_restricted_container_is_writable_only_on_bounded_tmpfs():
    name = f"agent-containers-e2e-{uuid.uuid4().hex[:8]}"
    fleet = FleetConfig(
        image="node:22-bookworm-slim",
        security_profile="restricted",
        exec_user="node",
        workspace_folder="/workspace",
        acp_command="cat",
        network="none",
        memory="1g",
        cpus=1,
        pids_limit=64,
        workspace_size="128m",
        home_size="64m",
    )
    try:
        _image_run(
            "restricted-e2e",
            fleet,
            name,
            workspace_folder="/workspace",
            exec_user="node",
        )
        assert inspect_state(name) == "running"

        info = DockerContainerInfo(
            name=name,
            container_id=name,
            image=fleet.image,
            state="running",
            status="Up",
            fleet="restricted-e2e",
            security_profile="restricted",
            security_policy=fleet.security_policy_fingerprint("/workspace", "node"),
            security_image_id=None,
        )
        assert restricted_policy_errors(
            info,
            fleet,
            workspace_folder="/workspace",
            exec_user="node",
        ) == []

        writable = subprocess.run(
            [
                "docker", "exec", "-u", "node", name, "bash", "-lc",
                'touch /workspace/work && touch "$HOME/home" && '
                'test -w /workspace && test -w "$HOME"',
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert writable.returncode == 0, writable.stderr

        executable = subprocess.run(
            [
                "docker", "exec", "-u", "node", name, "bash", "-lc",
                "cp /bin/true /workspace/tool && chmod 700 /workspace/tool "
                "&& /workspace/tool",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert executable.returncode == 0, executable.stderr

        rootfs = subprocess.run(
            [
                "docker", "exec", "-u", "node", name,
                "bash", "-lc", "touch /etc/escape",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rootfs.returncode != 0
        assert "Read-only file system" in rootfs.stderr
    finally:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=30,
        )


def test_prepared_command_refuses_replacement_before_agent_start(monkeypatch, tmp_path):
    from agent_containers import dispatch_projection as projection
    from agent_containers import dispatch_target, lease, lifecycle, resolver
    from agent_containers.config import ContainersConfig

    name = f"agent-containers-race-{uuid.uuid4().hex[:8]}"
    fleet = FleetConfig(
        image="node:22-bookworm-slim", security_profile="restricted", dispatch_only=True,
        exec_user="node", workspace_folder="/workspace", network="none", memory="1g",
        cpus=1.0, pids_limit=64, workspace_size="128m", home_size="64m",
        acp_command=(
            '/usr/local/bin/node -e \'require("fs").writeFileSync('
            '"/workspace/agent-started","unexpected")\''
        ),
    )
    config = ContainersConfig(fleets={"dispatch-race-e2e": fleet})
    monkeypatch.setattr(resolver, "load_config", lambda: config)
    monkeypatch.setattr(projection, "_STATE", tmp_path / "dispatch-bindings")
    monkeypatch.setattr(lease, "ensure_state_dir", lambda: None)
    for key, filename in (
        ("_LOCK_FILE", "leases.lock"), ("_DEPLOY_HOLDS_FILE", "deploy-holds.json"),
        ("_SESSION_ADMISSIONS_FILE", "session-admissions.json"),
        ("LEASE_FILE", "leases.json"), ("_LEASE_DETAILS_FILE", "lease-details.json"),
    ):
        monkeypatch.setattr(lease, key, tmp_path / filename)
    config_path = tmp_path / "containers.yaml"
    config_path.write_text(yaml.safe_dump({"fleets": {
        "dispatch-race-e2e": {key: value for key, value in asdict(fleet).items() if value is not None},
    }}), encoding="utf-8")
    env = {
        **os.environ, "AGENT_CONTAINERS_CONFIG": str(config_path),
        "AGENT_CONTAINERS_STATE_DIR": str(tmp_path), "AGENT_CONTAINERS_HOME": str(tmp_path),
        "AGENT_CONTAINERS_NO_SELFPROVISION": "1",
    }

    def run(*args):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=30,
            creationflags=no_window_flags(),
        )

    try:
        _image_run("dispatch-race-e2e", fleet, name, workspace_folder="/workspace", exec_user="node")
        original_id = lifecycle.inspect_container(name)["Id"]
        binding = projection.prepare(name, original_id, {
            "task_id": "task-race", "worker_id": "worker-race", "generation": 1,
            "coordinator_epoch": 0, "coordinator_url": "http://coordinator:8080",
        })
        _prepare_payload_runtime(tmp_path, Path(projection.__file__).with_name("__init__.py"))
        resolved = subprocess.run(
            [sys.executable, "-m", "agent_containers", "namespace-resolve",
             dispatch_target.reference(name, original_id, binding["binding_id"])],
            capture_output=True, text=True, timeout=30, env=env,
            creationflags=no_window_flags(),
        )
        assert resolved.returncode == 0, resolved.stderr
        spec = json.loads(resolved.stdout)
        assert spec["venue"]["instance_id"] == original_id
        assert spec["venue"]["capabilities"]["instance_pinned_exec"] is True
        removed = run("rm", "-f", original_id)
        assert removed.returncode == 0, removed.stderr
        _image_run("dispatch-race-e2e", fleet, name, workspace_folder="/workspace", exec_user="node")
        replacement_id = lifecycle.inspect_container(name)["Id"]
        assert replacement_id != original_id
        launched = subprocess.run(
            spec["spawn_command"], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=45, env=env, creationflags=no_window_flags(),
        )
        assert launched.returncode != 0 and "binding" in launched.stderr.lower(), launched.stderr
        untouched = run(
            "exec", "-u", "node", replacement_id, "/usr/local/bin/node", "-e",
            'if(require("fs").existsSync("/workspace/agent-started"))process.exit(1)',
        )
        assert untouched.returncode == 0, untouched.stderr
    finally:
        run("rm", "-f", name)


def test_dispatch_projection_uses_real_root_owned_rotating_file(monkeypatch, tmp_path):
    from agent_containers import dispatch_projection as projection
    from agent_containers import dispatch_target, lease, lifecycle, replacement, resolver
    from agent_containers.config import ContainersConfig

    name = f"agent-containers-dispatch-{uuid.uuid4().hex[:8]}"
    fleet = FleetConfig(
        image="node:22-bookworm-slim", security_profile="restricted",
        dispatch_only=True, exec_user="node", workspace_folder="/workspace",
        acp_command="cat", network="none", memory="1g", cpus=1.0,
        pids_limit=64, workspace_size="128m", home_size="64m",
    )
    config = ContainersConfig(fleets={"dispatch-e2e": fleet})
    monkeypatch.setattr(resolver, "load_config", lambda: config)
    monkeypatch.setattr(projection, "_STATE", tmp_path / "dispatch-bindings")
    monkeypatch.setattr(lease, "ensure_state_dir", lambda: None)
    for key, filename in (
        ("_LOCK_FILE", "leases.lock"), ("_DEPLOY_HOLDS_FILE", "deploy-holds.json"),
        ("_SESSION_ADMISSIONS_FILE", "session-admissions.json"),
        ("LEASE_FILE", "leases.json"), ("_LEASE_DETAILS_FILE", "lease-details.json"),
    ):
        monkeypatch.setattr(lease, key, tmp_path / filename)

    def run(*args):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=30,
            creationflags=no_window_flags(),
        )

    holder = None
    holder_errors = (tmp_path / "worker-wrapper.stderr").open("wb")
    try:
        _image_run("dispatch-e2e", fleet, name, workspace_folder="/workspace", exec_user="node")
        inspected = lifecycle.inspect_container(name)
        physical_id = inspected["Id"]
        facts = {
            "task_id": "task-e2e", "worker_id": "worker-e2e", "generation": 1,
            "coordinator_epoch": 0, "coordinator_url": "http://coordinator:8080",
        }
        with pytest.raises(RuntimeError, match="retained task binding"):
            resolver.resolve_live_exec_target(name)
        binding = projection.prepare(name, physical_id, facts)
        for index in range(2):
            token = uuid.uuid4().hex
            request = {
                "binding_id": binding["binding_id"], "owner_session_id": "native-e2e",
                "task_token": token, "expires_at": time.time() + 500 + index,
            }
            projection.project(name, physical_id, request)
            digest = hashlib.sha256(token.encode()).hexdigest()
            check = run(
                "exec", "-u", "node", physical_id, "/usr/local/bin/node", "-e",
                "const fs=require('fs'),c=require('crypto');"
                "const p='/run/agent-dispatch/task-token',s=fs.statSync(p);"
                "if(s.uid!==0||(s.mode&0o777)!==0o444)process.exit(1);"
                f"if(c.createHash('sha256').update(fs.readFileSync(p)).digest('hex')!=='{digest}')process.exit(2);"
                "try{fs.writeFileSync(p,'changed');process.exit(3)}catch(e){"
                "if(!['EACCES','EPERM'].includes(e.code))process.exit(4)}",
            )
            assert check.returncode == 0, check.stderr
            assert token not in projection._path(physical_id).read_text(encoding="utf-8")
            assert token not in json.dumps(lifecycle.inspect_container(physical_id))
        request = _assert_delayed_publication_is_guest_fenced(
            monkeypatch, projection, name, physical_id, inspected, request, run,
        )
        with pytest.raises(RuntimeError, match="another task incarnation"):
            projection.prepare(name, physical_id, {**facts, "generation": 2})
        with pytest.raises(RuntimeError, match="cannot be rebound"):
            projection.project(name, physical_id, {**request, "owner_session_id": "native-other"})
        target = resolver.resolve_live_exec_target(name, dispatch_binding_id=binding["binding_id"])
        with lease.session_admission(
            name, dispatch_container_id=physical_id, dispatch_binding_id=binding["binding_id"],
        ) as admission:
            worker_admission = projection._reserve_worker_admission(
                name, physical_id, inspected, admission_token=admission.token,
            )
            assert projection._publish_worker_admission(physical_id, inspected, worker_admission) == 0
            command = projection.build_spawn_command(
                target, binding,
                "/usr/local/bin/node -e 'if(process.env.AGENT_DISPATCH_TASK_WORKER_ID!==\"worker-e2e\""
                "||process.env.GH_TOKEN||process.env.AGENT_DISPATCH_CONTROL_TOKEN)process.exit(1)'",
                worker_admission=worker_admission,
            )
            worker = subprocess.run(
                command, capture_output=True, text=True, timeout=30,
                creationflags=no_window_flags(),
            )
        assert worker.returncode == 0, worker.stderr
        config_path = tmp_path / "containers.yaml"
        cli_fleet = {key: value for key, value in asdict(fleet).items() if value is not None}
        cli_fleet["acp_command"] = (
            "/usr/local/bin/node -e 'if(process.env.AGENT_DISPATCH_TASK_ID!==\"task-e2e\""
            "||process.env.AGENT_DISPATCH_TASK_WORKER_ID!==\"worker-e2e\""
            "||process.env.GH_TOKEN||process.env.AGENT_DISPATCH_CONTROL_TOKEN)process.exit(1)'"
        )
        config_path.write_text(
            yaml.safe_dump({"fleets": {"dispatch-e2e": cli_fleet}}), encoding="utf-8",
        )
        # The emitted payload shim requires a completed runtime in this fixture's
        # own state root; do not let this transport proof self-provision a host.
        _prepare_payload_runtime(tmp_path, Path(projection.__file__).with_name("__init__.py"))
        env = {
            **os.environ, "AGENT_CONTAINERS_CONFIG": str(config_path),
            "AGENT_CONTAINERS_STATE_DIR": str(tmp_path),
            "AGENT_CONTAINERS_HOME": str(tmp_path), "AGENT_CONTAINERS_NO_SELFPROVISION": "1",
        }
        cli = [sys.executable, "-m", "agent_containers"]
        prepared = dispatch_target.reference(name, physical_id, binding["binding_id"])
        ready = subprocess.run(
            [*cli, "namespace-ensure-ready", prepared],
            capture_output=True, text=True, timeout=30, env=env,
            creationflags=no_window_flags(),
        )
        assert ready.returncode == 0, ready.stderr
        resolved = subprocess.run(
            [*cli, "namespace-resolve", prepared],
            capture_output=True, text=True, timeout=30, env=env,
            creationflags=no_window_flags(),
        )
        assert resolved.returncode == 0, resolved.stderr
        spec = json.loads(resolved.stdout)
        assert spec["venue"]["instance_id"] == physical_id
        assert spec["venue"]["ready"] is True and spec["venue"]["dispatch_only"] is True
        assert spec["venue"]["capabilities"]["instance_pinned_exec"] is True
        assert not {"worktree_id", "repo"} & spec.keys()
        ordinary = subprocess.run(
            [*cli, "namespace-ensure-ready", name],
            capture_output=True, text=True, timeout=30, env=env,
            creationflags=no_window_flags(),
        )
        assert ordinary.returncode != 0 and "explicit task preparation" in ordinary.stderr
        launch = subprocess.run(
            spec["spawn_command"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=45,
            env=env, creationflags=no_window_flags(),
        )
        assert launch.returncode == 0, launch.stderr
        peer = subprocess.run(
            [*cli, "exec", "--stdio", name], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=30, env=env,
            creationflags=no_window_flags(),
        )
        assert peer.returncode != 0 and "retained task binding" in peer.stderr
        cli_fleet["acp_command"] = (
            "/usr/local/bin/node -e 'require(\"fs\").writeFileSync(\"/workspace/worker-ready\","
            "String(process.pid));setInterval(()=>{},1000)'"
        )
        config_path.write_text(
            yaml.safe_dump({"fleets": {"dispatch-e2e": cli_fleet}}), encoding="utf-8",
        )
        launch_args = [
            *cli, "exec", "--stdio", name, "--dispatch-binding-id", binding["binding_id"],
            "--expected-container-id", physical_id,
        ]
        holder = subprocess.Popen(
            launch_args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=holder_errors, env=env, creationflags=no_window_flags(),
        )
        worker_pid = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready = run(
                "exec", "-u", "node", physical_id, "/usr/local/bin/node", "-e",
                "const fs=require('fs');try{process.stdout.write(fs.readFileSync("
                "'/workspace/worker-ready','utf8'))}catch(e){process.exit(1)}",
            )
            if ready.returncode == 0:
                worker_pid = int(ready.stdout)
                break
            assert holder.poll() is None, "worker wrapper exited before guest readiness"
            time.sleep(0.2)
        assert worker_pid is not None, (tmp_path / "worker-wrapper.stderr").read_text(encoding="utf-8")
        duplicate = subprocess.run(
            launch_args, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=45, env=env, creationflags=no_window_flags(),
        )
        assert duplicate.returncode == 75, duplicate.stderr
        assert holder.poll() is None
        holder.kill()
        holder.wait(timeout=10)
        alive = run(
            "exec", "-u", "node", physical_id, "/usr/local/bin/node", "-e",
            f"process.kill({worker_pid},0)",
        )
        assert alive.returncode == 0, alive.stderr
        duplicate = subprocess.run(
            launch_args, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=45, env=env, creationflags=no_window_flags(),
        )
        assert duplicate.returncode == 75, duplicate.stderr
        assert run(
            "exec", "-u", "node", physical_id, "/usr/local/bin/node", "-e",
            f"process.kill({worker_pid},0)",
        ).returncode == 0
        assert lease.active_session_admissions(name) == []
        member = lifecycle.get_container(config, name)
        assert member is not None
        for operation in ("stop", "remove", "recreate"):
            if operation == "stop":
                decision = replacement.stop_restricted_member(
                    config, fleet, member, force_abandon=True,
                )
            else:
                decision = replacement.destroy_restricted_member(
                    config, fleet, member, operation=operation,
                    force_remove=True, force_abandon=True,
                )
            assert decision.status == "deferred", decision
            assert "restricted worker lock" in decision.reason
            assert run(
                "exec", "-u", "node", physical_id, "/usr/local/bin/node", "-e",
                f"process.kill({worker_pid},0)",
            ).returncode == 0
    finally:
        if holder is not None and holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)
        holder_errors.close()
        run("rm", "-f", name)


def _assert_delayed_publication_is_guest_fenced(
    monkeypatch, projection, name, physical_id, inspected, request, run,
):
    old_token = uuid.uuid4().hex
    old_request = {**request, "task_token": old_token, "expires_at": request["expires_at"] + 100}
    original_run = subprocess.run
    paused = "/run/agent-dispatch/publication-paused"
    resume = "/run/agent-dispatch/publication-resume"
    finished = "/run/agent-dispatch/publication-finished"

    def timed_out_client(argv, **kwargs):
        if kwargs.get("input") != old_token:
            return original_run(argv, **kwargs)
        # Retain the guest publisher's input across the client timeout, but
        # delay its acquisition of the actual production publication lock.
        delayed = (
            "const fs=require('fs'),cp=require('child_process');let input='';"
            "process.stdin.setEncoding('utf8');process.stdin.on('data',c=>input+=c);"
            "process.stdin.on('end',()=>{"
            f"fs.writeFileSync({json.dumps(paused)},String(process.pid));"
            "const timer=setInterval(()=>{"
            f"if(!fs.existsSync({json.dumps(resume)}))return;"
            "clearInterval(timer);"
            "const r=cp.spawnSync('/bin/bash',['--noprofile','--norc','-c',"
            f"{json.dumps(argv[-1])}],{{input,encoding:'utf8'}});"
            f"fs.writeFileSync({json.dumps(finished)},String(r.status));"
            "process.exit(r.status===0?0:1);},50);});"
        )
        child = subprocess.Popen(
            [*argv[:-5], "/usr/local/bin/node", "-e", delayed],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=no_window_flags(),
        )
        try:
            child.communicate(input=old_token, timeout=2)
            pytest.fail("guest publisher unexpectedly completed before client timeout")
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=10)
            raise
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=10)

    with monkeypatch.context() as patch:
        patch.setattr(projection.subprocess, "run", timed_out_client)
        with pytest.raises(RuntimeError, match="delivery failed"):
            projection.project(name, physical_id, old_request)
    ready = run(
        "exec", "-u", "0", physical_id, "/usr/local/bin/node", "-e",
        f"process.stdout.write(require('fs').readFileSync({json.dumps(paused)},'utf8'))",
    )
    assert ready.returncode == 0, ready.stderr
    guest_pid = int(ready.stdout)
    assert run(
        "exec", "-u", "0", physical_id, "/usr/local/bin/node", "-e",
        f"process.kill({guest_pid},0)",
    ).returncode == 0
    newer = {
        **request, "task_token": uuid.uuid4().hex,
        "expires_at": old_request["expires_at"] + 100,
    }
    projection.project(name, physical_id, newer)
    signalled = run(
        "exec", "-u", "0", physical_id, "/usr/local/bin/node", "-e",
        f"require('fs').writeFileSync({json.dumps(resume)},'resume')",
    )
    assert signalled.returncode == 0, signalled.stderr
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        complete = run(
            "exec", "-u", "0", physical_id, "/usr/local/bin/node", "-e",
            f"try{{process.stdout.write(require('fs').readFileSync({json.dumps(finished)},'utf8'))}}"
            "catch(e){process.exit(1)}",
        )
        if complete.returncode == 0:
            break
        time.sleep(0.1)
    else:
        pytest.fail("surviving publisher did not reach its bounded guest fence")
    assert complete.stdout == "1"
    digest = hashlib.sha256(newer["task_token"].encode()).hexdigest()
    verified = run(
        "exec", "-u", "node", physical_id, "/usr/local/bin/node", "-e",
        "const fs=require('fs'),c=require('crypto');"
        f"if(c.createHash('sha256').update(fs.readFileSync({json.dumps(projection.TOKEN_FILE)}))"
        f".digest('hex')!=={json.dumps(digest)})process.exit(1);",
    )
    assert verified.returncode == 0, verified.stderr
    retained = projection._read(physical_id)
    assert retained["expires_at"] == retained["attempted_expires_at"] == newer["expires_at"]
    return newer
