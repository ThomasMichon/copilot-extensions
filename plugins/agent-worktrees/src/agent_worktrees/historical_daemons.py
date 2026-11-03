"""Finite historical monitor recognition with owner-admitted native retirement."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from contextlib import ExitStack
from pathlib import Path

from zdd import breadcrumb, generations, routing
from agent_procutil.process_retirement import PinnedProcess
from agent_procutil._daemon_instance_identity import birth_identity

from . import daemon_health, locks, runtime_monitor_retirements as retention
from . import status_monitor_cutover as control

DETECTOR = "worktrees-monitor-control-v1"
REFERENCE_PREFIX = "worktrees-historical-monitor:v1:"
MAX_RESPONSE = 262144


def capabilities() -> dict:
    return {
        "detector": DETECTOR, "revision": 1,
        "inspect": True, "apply": sys.platform == "win32" or sys.platform.startswith("linux"),
        "protocol": "status-monitor-control-v1", "update_prerequisite": False,
    }


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ordinary(path: Path, limit: int) -> bytes:
    if path.is_symlink() or path.resolve(strict=True) != path.absolute():
        raise ValueError("monitor ownership evidence must not traverse links")
    before = path.stat()
    if not path.is_file() or before.st_size > limit:
        raise ValueError("monitor ownership evidence is not a bounded ordinary file")
    data = path.read_bytes()
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
        after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size
    ) or len(data) > limit:
        raise ValueError("monitor ownership evidence changed while reading")
    return data


def _client(root: Path, endpoint: routing.Endpoint):
    if endpoint.client_host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("historical monitor endpoint is not loopback")
    token = _ordinary(control.control_token_path(root), 4096).decode("utf-8").strip()
    if not token:
        raise ValueError("historical monitor control token is absent")
    return control.ControlClient(endpoint.base_url, token=token, timeout=3.0)


def _health(root: Path, endpoint: routing.Endpoint, helper) -> tuple[dict, str]:
    if type(endpoint.pid) is not int or endpoint.pid <= 0 or not endpoint.process_start_time:
        raise ValueError("historical endpoint lacks a native process identity")
    start = endpoint.process_start_time
    if locks.process_start_time(endpoint.pid) != start:
        raise ValueError("historical endpoint native identity changed")
    health = _client(root, endpoint)._request(
        "health", max_response_bytes=MAX_RESPONSE, total_timeout=3.0,
    )
    slot = health.get("runtime_slot")
    prefix = health.get("runtime_prefix")
    if (
        health.get("pid") != endpoint.pid or health.get("process_start_time") != start
        or health.get("status") not in {"ready", "draining"}
        or not isinstance(slot, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", slot)
        or slot == "dev" or slot.startswith("dev-") or slot.endswith(".")
        or not isinstance(prefix, str) or not Path(prefix).is_absolute()
    ):
        raise ValueError("historical control peer identity is not attributable")
    environment = root / "versions" / slot / ".venv"
    if Path(prefix) != environment or environment.resolve(strict=True) != environment:
        raise ValueError("historical peer runtime does not belong to this installation")
    if not helper.is_complete(root, slot):
        raise ValueError("historical peer has no complete immutable runtime")
    if locks.process_start_time(endpoint.pid) != start:
        raise ValueError("historical peer changed during authentication")
    return health, slot


def _reference(evidence: dict) -> str:
    suffix = "/".join(f"{item['pid']}@{item['start']}" for item in evidence["targets"])
    if len(suffix) > 200 or any(
        re.fullmatch(r"[1-9][0-9]*@[0-9]+(?::[0-9]+)*", item) is None
        for item in suffix.split("/")
    ):
        raise ValueError("historical monitor reference identity is unsupported")
    return REFERENCE_PREFIX + suffix


def _observe(root: Path) -> tuple[list[dict], dict[str, dict]]:
    root = root.absolute()
    if root.resolve(strict=True) != root:
        raise ValueError("historical cleanup runtime root must not traverse links")
    ctx = daemon_health._context()
    if ctx.config_dir != root / "status-monitor-routing":
        raise ValueError("historical cleanup requires the selected Worktrees installation")
    for evidence_path in (
        routing.routing_table_path(ctx.config_dir), breadcrumb.breadcrumb_path(ctx.config_dir),
        root / "status-monitor.lock",
    ):
        if evidence_path.exists() or evidence_path.is_symlink():
            _ordinary(evidence_path, 1048576)
    table = routing.read_table(ctx.config_dir, strict=True)
    refusal = breadcrumb.recovery_refusal(ctx.config_dir, breadcrumb.read_breadcrumb(ctx.config_dir))
    census = ctx.list_candidates()
    if len(census) > 256:
        raise ValueError("historical monitor census exceeds its finite bound")
    groups, uncertainty = generations.group_generations(
        {item.pid: item for item in census}, structural=ctx.group_launchers,
        is_launcher_image=ctx.is_launcher_image,
    )
    helper = retention._runtime()
    active = routing.Endpoint.from_dict((table or {}).get("active") or {})
    previous = routing.Endpoint.from_dict((table or {}).get("previous") or {})
    active_slot = None
    if active is not None:
        health, active_slot = _health(root, active, helper)
        if health.get("status") != "ready" or health.get("published") is not True:
            raise ValueError("current serving monitor is not positively ready")
        if helper.current_version(root) != active_slot:
            raise ValueError("current serving monitor is not the selected runtime")
    lock = locks.read_lock(root / "status-monitor.lock")
    refs = helper._leases.read_runtime_references(root)
    findings, plans = [], {}
    for group in groups:
        resource = f"status-monitor:{group.key}"
        reason = refusal or uncertainty
        evidence = None
        if not reason and (
            active is None or group.pids & {active.pid, (lock or {}).get("pid")}
        ):
            reason = "current or unconfirmed serving generation is protected"
        if not reason and (
            previous is None or previous.pid != group.worker.pid
            or previous.process_start_time != group.worker.start_time
        ):
            reason = "no authenticated archived control endpoint for this generation"
        if not reason:
            try:
                _health_record, slot = _health(root, previous, helper)
                if slot == active_slot:
                    raise ValueError("selected runtime generation is protected")
                evidence = {
                    "root": str(root), "slot": slot, "route": table,
                    "targets": [{"pid": item["pid"], "start": item["start_time"]}
                                for item in group.targets()],
                    "marker": hashlib.sha256(_ordinary(
                        root / "versions" / slot / ".install-complete.json", 32768,
                    )).hexdigest(),
                }
                owner = _reference(evidence)
                if any(slot in values and name != owner for name, values in refs.items()):
                    raise ValueError("another runtime retention owner protects this generation")
                if owner in refs and refs[owner] != [slot]:
                    raise ValueError("historical cleanup retention evidence changed")
                if retention.pending_slot(root, slot):
                    raise ValueError("resident retirement custody protects this generation")
                plans[resource] = evidence
            except (OSError, ValueError, RuntimeError) as error:
                reason = str(error)
        finding = {
            "detector": DETECTOR, "revision": 1, "resource": resource,
            "status": "preserved" if reason else "candidate",
            "reason": reason or "authenticated displaced legacy control peer",
        }
        if not reason:
            finding.update(evidence=evidence, sha256=_digest(evidence))
        findings.append(finding)
    if routing.read_table(ctx.config_dir, strict=True) != table:
        raise ValueError("monitor route changed during historical inspection")
    return findings, plans


def _retained(root: Path) -> list[dict]:
    findings = []
    for owner, slots in retention._runtime()._leases.read_runtime_references(root).items():
        if not owner.startswith(REFERENCE_PREFIX):
            continue
        suffix = owner[len(REFERENCE_PREFIX):]
        parts = suffix.split("/")
        if len(parts) not in {1, 2} or len(slots) != 1 or any(
            re.fullmatch(r"[1-9][0-9]*@[0-9]+(?::[0-9]+)*", item) is None for item in parts
        ):
            raise ValueError("historical monitor retention identity is malformed")
        targets = [{"pid": int(item.split("@", 1)[0]), "start": item.split("@", 1)[1]}
                   for item in parts]
        exited = all(retention._exit_state(target) is True for target in targets)
        evidence = {"root": str(root), "owner": owner, "slot": slots[0], "targets": targets}
        findings.append({
            "detector": DETECTOR, "revision": 1,
            "resource": "status-monitor-retention:" + suffix,
            "status": "candidate" if exited else "preserved",
            "reason": "exact original generation exited; code hold can settle" if exited else
                      "original native exit remains unconfirmed; code hold retained",
            "evidence": evidence, "sha256": _digest(evidence),
        })
    return findings


def inspect(root: Path) -> list[dict]:
    return [*_observe(root)[0], *_retained(root)]


def _result(finding: dict, status: str, reason: str) -> dict:
    return {"resource": finding.get("resource"), "status": status, "reason": reason}


def _require(root: Path, finding: dict) -> dict:
    findings, plans = _observe(root)
    observed = next((item for item in findings if item["resource"] == finding.get("resource")), None)
    if observed != finding or finding.get("status") != "candidate":
        raise ValueError("historical monitor observation changed; inspect again")
    return plans[finding["resource"]]


def apply(root: Path, finding: dict) -> dict:
    if not isinstance(finding, dict) or not isinstance(finding.get("resource"), str):
        raise ValueError("historical monitor selection has no resource identity")
    if not capabilities()["apply"]:
        return _result(finding, "preserved", "native process pinning is unsupported on this platform")
    directory = root / "status-monitor-routing"
    if (not root.is_absolute() or root.resolve(strict=True) != root
        or directory.resolve(strict=True) != directory
        or daemon_health._context().config_dir != directory):
        return _result(finding, "preserved", "selected native runtime scope changed")
    guard = control._acquire_cutover_lock(directory, timeout_s=3.0)
    retained = False
    try:
        helper = retention._runtime()
        if finding.get("resource", "").startswith("status-monitor-retention:"):
            observed = next((item for item in _retained(root) if item == finding), None)
            if observed is None or observed["status"] != "candidate":
                raise ValueError("original native exit is unconfirmed or retention evidence changed")
            evidence = observed["evidence"]
            helper._leases.replace_runtime_references(
                root, evidence["owner"], [], expected=[evidence["slot"]],
            )
            return _result(finding, "applied", "exact original native exit confirmed; code hold released")
        evidence = _require(root, finding)
        owner, slot = _reference(evidence), evidence["slot"]
        with ExitStack() as stack:
            pins = []
            for target in evidence["targets"]:
                pin = stack.enter_context(PinnedProcess(target["pid"], birth_identity(target["pid"])))
                if locks.process_start_time(target["pid"]) != target["start"] or pin.exited():
                    raise ValueError("historical monitor native identity changed before pinning")
                pins.append(pin)
            _require(root, finding)
            references = helper._leases.read_runtime_references(root)
            if owner not in references:
                helper._leases.replace_runtime_references(root, owner, [slot], expected=[])
            elif references[owner] != [slot]:
                raise ValueError("historical monitor runtime retention changed")
            retained = True
            endpoint = routing.Endpoint.from_dict(evidence["route"]["previous"])
            client = _client(root, endpoint)
            drained = client._request(
                "drain", {"timeout": 5.0, "poll": 0.1, "force": False},
                max_response_bytes=MAX_RESPONSE, total_timeout=10.0, read_timeout=10.0,
            )
            if drained.get("drained") is not True:
                return _result(finding, "pending", "accepted work remains unsettled; original runtime retained")
            _require(root, finding)
            old_health, _ = _health(root, endpoint, helper)
            if old_health.get("status") != "draining":
                return _result(finding, "pending", "closed admission is unconfirmed; original runtime retained")
            if _client(root, endpoint).token != client.token:
                raise ValueError("monitor control identity changed after drain")
            response = client._request("shutdown", max_response_bytes=MAX_RESPONSE, total_timeout=3.0)
            if response.get("shutdown") is not True:
                return _result(finding, "pending", "shutdown outcome unconfirmed; original runtime retained")
            for pin in pins:
                if not pin.exited(5.0):
                    # Only a positively drained, still-displaced peer may use bounded native force.
                    if routing.read_table(directory, strict=True) != evidence["route"]:
                        return _result(finding, "pending", "route changed; original runtime retained")
                    refusal = breadcrumb.recovery_refusal(directory, breadcrumb.read_breadcrumb(directory))
                    if refusal:
                        return _result(finding, "pending", refusal)
                    if pin is pins[0]:
                        old_health, _ = _health(root, endpoint, helper)
                        if old_health.get("status") != "draining":
                            return _result(finding, "pending", "closed admission changed before native retirement")
                    elif not pins[0].exited():
                        return _result(finding, "pending", "original worker exit is unconfirmed")
                    active = routing.Endpoint.from_dict(evidence["route"]["active"])
                    health, current_slot = _health(root, active, helper)
                    if (health.get("status") != "ready" or health.get("published") is not True
                        or helper.current_version(root) != current_slot
                        or pin.pid in {active.pid, (locks.read_lock(root / "status-monitor.lock") or {}).get("pid")}
                        or retention.pending_slot(root, slot)
                        or any(slot in values and name != owner for name, values in
                               helper._leases.read_runtime_references(root).items())):
                        return _result(finding, "pending", "retirement owner protection changed")
                    if routing.read_table(directory, strict=True) != evidence["route"]:
                        return _result(finding, "pending", "route changed at retirement boundary")
                    pin.terminate(timeout=5.0)
            helper._leases.replace_runtime_references(root, owner, [], expected=[slot])
            return _result(finding, "applied", "selected generation positively exited; exact code hold released")
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        return _result(finding, "pending" if retained else "preserved", str(error))
    finally:
        guard.release()
