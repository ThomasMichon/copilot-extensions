"""Physical-target and configuration checks; no Docker or file effects."""

from __future__ import annotations

import json
from typing import Any

from offline_job import Plan, PlanRefusal


def object_response(content: str) -> dict[str, Any]:
    value = json.loads(content)
    if not isinstance(value, dict):
        raise PlanRefusal("Docker observation is not an object")
    return value


def require_owned(plan: Plan, physical: str, snapshot: dict[str, Any]) -> None:
    if (
        snapshot.get("Id") != physical
        or snapshot.get("Name") != "/" + plan.job.name
        or snapshot.get("Config", {})
        .get("Labels", {})
        .get("copilot-extensions.offline-job")
        != plan.job.name
    ):
        raise PlanRefusal("physical container ownership differs")


def require_configuration(plan: Plan, snapshot: dict[str, Any]) -> None:
    job = plan.job
    config = snapshot["Config"]
    host = snapshot["HostConfig"]
    if (
        snapshot["Image"] != job.image
        or config["User"] != f"{job.uid}:{job.gid}"
        or config["Entrypoint"] != ["/bin/sh"]
        or config["Cmd"] != list(plan.create[-4:])
        or config["WorkingDir"] != "/job/out"
        or host["Memory"] != job.limits.memory_bytes
        or host["MemorySwap"] != job.limits.memory_bytes
        or host["NanoCpus"] != job.limits.cpu_millis * 1_000_000
        or host["PidsLimit"] != job.limits.processes
        or host["Init"] is not True
        or host["NetworkMode"] != "none"
        or host["ReadonlyRootfs"] is not True
        or host["Privileged"] is not False
        or host["CapDrop"] != ["ALL"]
        or host["CapAdd"] not in (None, [])
        or host["SecurityOpt"] != ["no-new-privileges"]
        or host["PidMode"] != ""
        or host["UTSMode"] != ""
        or host["IpcMode"] != "private"
        or host["AutoRemove"] is not False
        or host["PublishAllPorts"] is not False
        or host["PortBindings"] not in (None, {})
        or host["RestartPolicy"]["Name"] not in ("", "no")
        or snapshot["RestartCount"] != 0
    ):
        raise PlanRefusal("observed container configuration differs")
    expected_environment = {
        plan.create[index + 1].split("=", 1)[0]: plan.create[index + 1].split("=", 1)[1]
        for index, value in enumerate(plan.create)
        if value == "--env"
    }
    pairs = [value.split("=", 1) for value in config["Env"]]
    observed_environment = dict(pairs)
    if len(pairs) != len(observed_environment) or any(
        observed_environment.get(key) != value
        for key, value in expected_environment.items()
    ):
        raise PlanRefusal("observed container environment differs")
    expected_mounts = {
        "/job/out": (str(plan.results), True),
        **{item.target: (str(item.source), False) for item in job.inputs},
    }
    if job.cache is not None:
        expected_mounts["/job/cache"] = (str(job.cache), True)
    mounts = snapshot["Mounts"]
    observed_mounts = {
        item["Destination"]: (item["Source"], item["RW"])
        for item in mounts
        if item["Type"] == "bind"
    }
    if len(mounts) != len(observed_mounts) or observed_mounts != expected_mounts:
        raise PlanRefusal("observed container mount set differs")


def require_running(snapshot: dict[str, Any]) -> None:
    state = snapshot["State"]
    if (
        state["Running"] is not True
        or state["Restarting"] is not False
        or state["Paused"] is not False
        or state["OOMKilled"] is not False
        or type(state["Pid"]) is not int
        or state["Pid"] <= 0
    ):
        raise PlanRefusal("container is not running within its captured lifetime")


def require_stopped(snapshot: dict[str, Any]) -> None:
    state = snapshot["State"]
    if (
        state["Running"] is not False
        or state["Restarting"] is not False
        or state["Paused"] is not False
        or type(state["Pid"]) is not int
        or state["Pid"] != 0
    ):
        raise PlanRefusal("owned container stop is not verified")
