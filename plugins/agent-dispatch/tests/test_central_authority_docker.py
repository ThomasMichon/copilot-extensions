"""Opt-in native CLI authority proof with separate fresh central/peer containers."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_procutil import no_window_flags

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("AGENT_DISPATCH_CENTRAL_AUTHORITY_E2E") != "1",
        reason="set AGENT_DISPATCH_CENTRAL_AUTHORITY_E2E=1 for central/local Docker acceptance",
    ),
    pytest.mark.timeout(300),
]
_REPO = "example.com/acme/central-local-acceptance"


def _docker(*argv):
    return subprocess.run(
        ["docker", *argv], capture_output=True, text=True, timeout=45,
        creationflags=no_window_flags(),
    )


def _run(*argv):
    result = _docker(*argv)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _remove_owned(kind, name):
    removed = _docker("rm", "-f", "-v", name) if kind == "container" else _docker("network", "rm", name)
    command = [kind, "ls", "--filter", f"name={name}"]
    if kind == "container":
        command.append("--all")
    command.extend(["--format", "{{json .Names}}" if kind == "container" else "{{json .Name}}"])
    inventory = _docker(*command)
    assert inventory.returncode == 0, (
        f"{kind} inventory failed: exit={inventory.returncode}; "
        f"stdout={inventory.stdout!r}; stderr={inventory.stderr!r}"
    )
    names = [json.loads(line) for line in inventory.stdout.splitlines()]
    assert all(isinstance(value, str) for value in names), f"{kind} inventory malformed"
    assert name not in names, (
        f"{kind} cleanup failed: exit={removed.returncode}; "
        f"stdout={removed.stdout!r}; stderr={removed.stderr!r}"
    )


def _native(container, *argv, expected=0, env=None):
    prefix = ["exec"]
    for key, value in (env or {}).items():
        prefix.extend(["--env", f"{key}={value}"])
    result = _docker(*prefix, container, "python", "-m", "agent_dispatch", *argv)
    assert result.returncode == expected, result.stderr
    return result


def _wait(probe):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.1)
    pytest.fail("fresh central/peer boundary did not become ready")


def test_native_central_authority_never_promotes_a_local_queue(tmp_path):
    lane_path = Path(__file__).resolve().parents[3] / "tools/clean-room/central-local-authority/run.py"
    spec = importlib.util.spec_from_file_location("central_authority_owner", lane_path)
    lane = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lane)
    owner = lane.read_owner(os.environ)
    evidence = Path(owner["evidence"])
    evidence.mkdir(exist_ok=False)
    report = {
        "scenario": "central-local-authority", "tier": "P", "family": "F1",
        "result": "fail", "cleanup": "pending", "stages": [], "jams": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    image = os.environ["AGENT_DISPATCH_E2E_CENTRAL_IMAGE"]
    suffix = owner["suffix"]
    central, peer, uid_probe = owner["containers"]
    network, = owner["networks"]
    central_token, local_token = (uuid.uuid4().hex for _ in range(2))
    report["env"] = {"image": image, "host_os": os.name, "worker_os": "linux"}
    report["resources"] = {"network": network, "containers": [central, peer, uid_probe]}
    (evidence / "cr-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        with ExitStack() as cleanup:
            cleanup.callback(_remove_owned, "network", network)
            _run("network", "create", "--internal", network)
            cleanup.callback(_remove_owned, "container", uid_probe)
            uid, gid = _run(
                "run", "--rm", "--name", uid_probe, "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--entrypoint", "python", image, "-c",
                "import os;print(os.getuid(),os.getgid())",
            ).split()
            for name, token in ((central, central_token), (peer, local_token)):
                extra = []
                if name == peer:
                    for key, value in {
                        "AGENT_DISPATCH_TASK_AUTHORITY": "central",
                        "AGENT_DISPATCH_URL": "http://127.0.0.1:8787",
                        "AGENT_DISPATCH_SHARED_URL": "http://central:8787",
                        "AGENT_DISPATCH_SHARED_TOKEN": central_token,
                        "AGENT_DISPATCH_FEDERATION_ROLE": "peer",
                        "AGENT_DISPATCH_FEDERATION_INSTANCE": "acceptance-peer",
                    }.items():
                        extra.extend(["-e", f"{key}={value}"])
                cleanup.callback(_remove_owned, "container", name)
                _run(
                    "run", "-d", "--name", name, "--network", network,
                    "--network-alias", "central" if name == central else "peer",
                    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--memory", "1g", "--cpus", "1", "--pids-limit", "64",
                    "--tmpfs", f"/home/dispatch/.agent-dispatch:rw,nosuid,size=128m,uid={uid},gid={gid},mode=0700",
                    "--tmpfs", "/tmp:rw,nosuid,size=64m",
                    "-e", "AGENT_DISPATCH_HOST=0.0.0.0", "-e", "AGENT_DISPATCH_PORT=8787",
                    "-e", "AGENT_DISPATCH_DB=/home/dispatch/.agent-dispatch/queue.db",
                    "-e", f"AGENT_DISPATCH_TOKEN={token}", *extra, image,
                )

                def ready():
                    state = json.loads(_run("inspect", name))[0]["State"]
                    assert state["Running"], _run("logs", name)
                    probe = _docker("exec", name, "python", "-m", "agent_dispatch", "--local", "health")
                    return probe.returncode == 0

                _wait(ready)
            report["stages"].append({"name": "fresh-services", "status": "pass"})

            def registered_peer():
                directory = json.loads(_native(peer, "federation", "status").stdout)
                rows = [row for row in directory["peers"] if row["instance"] == "acceptance-peer"]
                return rows[0] if rows else None

            presence = _wait(registered_peer)
            assert presence["role"] == "peer"
            refused_role = _native(peer, "federation", "run", "--role", "coordinator", "--once", expected=1)
            assert "must be a peer or satellite" in refused_role.stderr
            refused_auth = _native(
                peer, "health", expected=1, env={"AGENT_DISPATCH_SHARED_TOKEN": "invalid-central-token"},
            )
            assert "401" in refused_auth.stderr
            report["stages"].append({
                "name": "authenticated-presence-only", "status": "pass", "role": presence["role"],
                "role_refusal_exit": refused_role.returncode, "auth_refusal_exit": refused_auth.returncode,
            })

            remote = json.loads(_native(peer, "create", "central task", "--repo", _REPO).stdout)
            local = json.loads(_native(peer, "--local", "create", "local-only task", "--repo", _REPO).stdout)
            root = Path(__file__).resolve().parents[1]
            for authority, task in (("central", remote), ("local", local)):
                assert task["claim_ref"] == f"dispatch-{authority}-task:{task['id']}"
                manifest = json.loads(
                    (root / "claim-providers" / f"dispatch-{authority}-task.json").read_text(),
                )
                callback = _native(
                    peer, *manifest["status_command"][1:], "claim-status", task["id"],
                    env={
                        "AGENT_DISPATCH_TASK_AUTHORITY": "local" if authority == "central" else "central",
                        manifest["no_self_provision_env"]: "1",
                    },
                )
                assert json.loads(callback.stdout)["exists"] is True
            ambiguous = _native(peer, "claim-status", remote["id"], expected=1)
            assert "original authority" in ambiguous.stderr
            overridden = _native(
                peer, "--local", "show", local["id"],
                env={"AGENT_DISPATCH_URL": "http://central:8787"},
            )
            assert json.loads(overridden.stdout)["id"] == local["id"]
            assert remote["id"] != local["id"]
            local_absence = _native(peer, "--local", "show", remote["id"], expected=1)
            central_absence = _native(peer, "show", local["id"], expected=1)
            assert "404" in local_absence.stderr and "404" in central_absence.stderr
            central_rows = json.loads(_native(peer, "list", "--repo", _REPO).stdout)
            local_rows = json.loads(_native(peer, "--local", "list", "--repo", _REPO).stdout)
            assert [row["id"] for row in central_rows] == [remote["id"]]
            assert [row["id"] for row in local_rows] == [local["id"]]
            owner = "acceptance-peer/worker"
            claimed = json.loads(_native(peer, "claim", remote["id"], "--worker", owner, "--repo", _REPO).stdout)
            assert claimed["id"] == remote["id"] and claimed["status"] == "claimed"
            started = json.loads(_native(peer, "start", remote["id"], owner).stdout)
            assert started["status"] == "started"
            _native(peer, "progress", remote["id"], owner, "--summary", "central owner retained")
            report["stages"].append({
                "name": "independent-task-authorities", "status": "pass",
                "central_status": started["status"], "central_owner": started["owner"],
                "local_status": local["status"], "central_ref": remote["claim_ref"],
                "local_ref": local["claim_ref"],
            })

            _run("network", "disconnect", network, central)
            try:
                outage_read = _native(peer, "show", remote["id"], expected=1)
                outage_write = _native(peer, "complete", remote["id"], owner, expected=1)
                for response in (outage_read, outage_write):
                    assert "cannot reach coordinator" in response.stderr
                still_local = json.loads(_native(peer, "--local", "show", local["id"]).stdout)
                assert still_local["status"] == local["status"]
                during_outage = json.loads(_native(peer, "--local", "list", "--repo", _REPO).stdout)
                assert len(during_outage) == 1
                for field in ("id", "status", "owner", "generation", "updated_at"):
                    assert during_outage[0][field] == local_rows[0][field]
                _native(peer, "--local", "show", remote["id"], expected=1)
            finally:
                _run("network", "connect", "--alias", "central", network, central)
            retained = json.loads(_native(peer, "show", remote["id"]).stdout)
            assert retained["status"] == "started" and retained["owner"] == started["owner"]
            assert retained["generation"] == started["generation"]
            assert _wait(registered_peer)["role"] == "peer"
            completed = json.loads(_native(peer, "complete", remote["id"], owner).stdout)
            assert completed["status"] == "completed"
            report["stages"].append({
                "name": "outage-does-not-transfer-ownership", "status": "pass",
                "read_exit": outage_read.returncode, "write_exit": outage_write.returncode,
                "retained_generation": retained["generation"], "final_status": completed["status"],
            })
        report["result"] = "pass"
    finally:
        removed = False
        try:
            assert not _run("ps", "--all", "--filter", f"name={suffix}", "--format", "{{.ID}}").strip()
            assert not _run("network", "ls", "--filter", f"name={network}", "--format", "{{.ID}}").strip()
            removed = True
        finally:
            report["cleanup"] = "complete" if removed else "failed"
            if not removed:
                report["result"] = "fail"
            if report["result"] != "pass":
                report["jams"].append({
                    "category": "bridge-service", "evidence": "pytest failure and completed stages",
                    "hint": "Inspect the actual central/peer boundary; never disable authentication or promote the local queue.",
                })
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            (evidence / "cr-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
