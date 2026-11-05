"""Gateway-owned ensure and exact stop behind the existing application installer."""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import secrets
import sys
import time

from . import gateway_host as host
from .gateway_host import RuntimeEvidenceError
from .planning import MAX_BYTES, PlanError, decode
from .private_state import private_directory, read_private_file


PENDING = "launch.json"


def pending_launch(root: Path) -> dict | None:
    path = root / host.STATE / PENDING
    if not os.path.lexists(path):
        return None
    value = decode(read_private_file(path, MAX_BYTES))
    if (
        type(value) is not dict or set(value) not in (
            {"generation", "token"}, {"generation", "token", "retiring"},
        )
        or "retiring" in value and value["retiring"] is not True
        or not isinstance(value["generation"], str) or not value["generation"]
        or len(value["generation"]) > 256 or Path(value["generation"]).name != value["generation"]
        or not isinstance(value["token"], str) or len(value["token"]) != 32
        or any(char not in "0123456789abcdef" for char in value["token"])
    ):
        raise PlanError("Gateway launch outcome is unresolved")
    return value


def require_launch(root: Path, generation: str, token: str) -> None:
    if pending_launch(root) != {"generation": generation, "token": token}:
        raise PlanError("Gateway launch admission changed before publication")


def clear_launch(root: Path, token: str) -> None:
    value = pending_launch(root)
    if value is not None and value["token"] == token:
        (root / host.STATE / PENDING).unlink()


def _record(root: Path) -> dict | None:
    path = root / host.STATE / host.RUNTIME
    if not os.path.lexists(path):
        return None
    content = host.read_runtime_file(path)
    try:
        value = decode(content)
    except PlanError:
        raise RuntimeEvidenceError("Gateway host runtime evidence is invalid") from None
    if (
        type(value) is not dict or set(value) != {
            "schema", "version", "generation", "selection", "pid", "birth", "port", "token",
        } or value["schema"] != "fleet-manager.gateway-host-runtime"
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["pid"]) is not int or value["pid"] <= 0
        or type(value["port"]) is not int or not 0 < value["port"] < 65536
        or any(not isinstance(value[key], str) or not value[key] or len(value[key]) > 256
               for key in ("generation", "selection", "birth", "token"))
        or len(value["token"]) != 32 or any(char not in "0123456789abcdef" for char in value["token"])
    ):
        raise RuntimeEvidenceError("Gateway host runtime evidence is invalid")
    return value


def _call(value: dict, action: str, *, replacement: str | None = None, timeout: float = 2) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", value["port"], timeout=timeout)
    try:
        body = b"" if action == "status" else json.dumps({
            "generation": value["generation"], "replacement": replacement,
        }).encode("ascii")
        connection.request("POST", "/" + action, body, {
            "X-Fleet-Host": value["token"], "Content-Type": "application/json",
        })
        response = connection.getresponse()
        data = response.read(4097)
        if response.status != 200 or len(data) > 4096:
            raise PlanError("Gateway host control was refused")
        result = decode(data)
        if (
            type(result) is not dict
            or result.get("schema") != "fleet-manager.gateway-host-control"
            or type(result.get("version")) is not int or result["version"] != 1
            or result.get("pid") != value["pid"]
            or result.get("birth") != value["birth"]
            or result.get("generation") != value["generation"]
            or result.get("selection") != value["selection"]
        ):
            raise PlanError("Gateway host control identity differs from its native owner")
        return result
    finally:
        connection.close()


def _probe(value: dict) -> dict | None:
    from zdd.process_exit import open_process

    try:
        with open_process(value["pid"], value["birth"]) as reference:
            result = _call(value, "status")
            if reference.wait(0):
                return None
            return result
    except ProcessLookupError:
        return None
    except (ValueError, OverflowError) as error:
        raise RuntimeEvidenceError("Gateway native owner identity is unavailable") from error
    except (ConnectionError, http.client.HTTPException, TimeoutError) as error:
        raise PlanError("Gateway host control is unavailable; native exit is not confirmed") from error


def inspect(root: Path, selection: dict) -> dict:
    if not os.path.lexists(root / host.STATE):
        return {"role": "gateway", "state": "not-running", "mutations_performed": False}
    lock = _lock(root, time.monotonic() + 5)
    try:
        if pending_launch(root) is not None:
            return {
                "role": "gateway", "state": "launch-unresolved", "native_exit_confirmed": False,
                "mutations_performed": False,
            }
        value = _record(root)
        if value is None:
            return {"role": "gateway", "state": "not-running", "mutations_performed": False}
        result = _probe(value)
        if result is None:
            return {"role": "gateway", "state": "not-running", "mutations_performed": False}
        result["selected"] = (
            value["generation"] == host.selected_version(root)
            and value["selection"] == host.selection_digest(selection)
            and result.get("startup") == host.startup_digest(selection)
        )
        result["mutations_performed"] = False
        return result
    finally:
        lock.release()


def _stop(root: Path, value: dict, *, replacement: str | None, deadline: float,
          wait: bool = True) -> None:
    from zdd.process_exit import open_process

    try:
        reference = open_process(value["pid"], value["birth"])
    except ProcessLookupError:
        return
    with reference:
        _call(value, "stop", replacement=replacement, timeout=min(2, deadline - time.monotonic()))
        if wait and not reference.wait(min(10, max(0, deadline - time.monotonic()))):
            raise PlanError("Gateway host stop is unresolved; native exit is not confirmed")


def _lock(root: Path, deadline: float):
    from zdd.cutover_lock import CutoverLock

    private_directory(root / host.STATE)
    lock = CutoverLock(root / host.STATE)
    lock.acquire(timeout=max(0, deadline - time.monotonic()))
    return lock


def stop(root: Path, selection: dict) -> dict:
    deadline = time.monotonic() + 30
    lock = _lock(root, deadline)
    try:
        value = _record(root)
        pending = pending_launch(root)
        if pending is not None and (
            value is None or pending != {
                "generation": value["generation"], "token": value["token"],
            }
        ):
            raise PlanError("Gateway launch outcome is unresolved; native exit is not confirmed")
        if value is not None:
            if (
                value["generation"] != host.selected_version(root)
                or value["selection"] != host.selection_digest(selection)
            ):
                raise PlanError("Gateway stop does not address the current selected host")
            _stop(root, value, replacement=None, deadline=deadline)
            clear_launch(root, value["token"])
        return {"role": "gateway", "state": "stopped", "native_exit_confirmed": True}
    finally:
        lock.release()


def _settle_failed_launch(root, selection, slot, nonce, startup, process):
    lock = _lock(root, time.monotonic() + 5)
    retire = False
    try:
        try:
            current = _record(root)
        except RuntimeEvidenceError:
            current = None
        if current is not None and current["token"] == nonce and current["generation"] == slot.name:
            try:
                observed = _probe(current)
            except (PlanError, PermissionError):
                observed = None
            if (
                observed is not None and observed.get("ready") is True
                and current["selection"] == host.selection_digest(selection)
                and observed.get("startup") == startup
                and host.selected_version(root) == slot.name
                and host.startup_digest(selection) == startup
            ):
                return {**observed, "reused": False}
        elif pending_launch(root) == {"generation": slot.name, "token": nonce}:
            host.write_private(root / host.STATE / PENDING, {
                "generation": slot.name, "token": nonce, "retiring": True,
            })
            retire = True
    finally:
        lock.release()
    if retire:
        process.terminate()
        process.wait(timeout=5)
        lock = _lock(root, time.monotonic() + 5)
        try:
            clear_launch(root, nonce)
        finally:
            lock.release()
    return None


def ensure(root: Path, selection: dict) -> dict:
    from agent_procutil import spawn_daemon
    from agent_procutil.daemon_instances import DaemonInstanceSpec
    from . import gateway_host_runtime

    deadline = time.monotonic() + 30
    slot = host.installed_slot(root, current=True)
    expected = host.selection_digest(selection)
    lock = _lock(root, deadline)
    try:
        startup = host.startup_digest(selection)
        pending = pending_launch(root)
        if pending is not None:
            raise PlanError("Gateway launch outcome is unresolved; wait for its owner publication")
        try:
            current = _record(root)
        except RuntimeEvidenceError:
            current = None
        if current is not None:
            try:
                observed = _probe(current)
            except (PlanError, PermissionError):
                if current["generation"] == slot.name:
                    raise PlanError(
                        "Current Gateway host ownership is unresolved; native exit is not confirmed"
                    ) from None
                observed = None
            if observed is not None and current["generation"] == slot.name:
                if (
                    current["selection"] != expected or observed.get("ready") is not True
                    or observed.get("startup") != startup
                ):
                    raise PlanError("Current Gateway host does not match its requested readiness")
                if host.selected_version(root) != slot.name:
                    raise PlanError("Gateway selection changed during readiness")
                if host.startup_digest(selection) != startup:
                    raise PlanError("Gateway startup inputs changed during readiness")
                return {**observed, "reused": True}
            if observed is not None:
                try:
                    _stop(root, current, replacement=slot.name, deadline=deadline, wait=False)
                except (
                    PlanError, PermissionError, ConnectionError,
                    http.client.HTTPException, TimeoutError,
                ):
                    pass
        if host.selected_version(root) != slot.name:
            raise PlanError("Gateway selection changed before launch")
        nonce = secrets.token_hex(16)
        host.write_private(root / host.STATE / PENDING, {
            "generation": slot.name, "token": nonce,
        })
        command = [sys.executable, "-I", "-B", "-m", "fleet_manager.gateway_host_runtime"]
        try:
            process = spawn_daemon(
                command, cwd=root / host.STATE, log_path=root / host.STATE / "host.log",
                env_overrides={
                    "FLEET_MANAGER_HOME": str(root), "FLEET_MANAGER_HOST_NONCE": nonce,
                }, display="Fleet Gateway host",
                instance=DaemonInstanceSpec(
                    "fleet-manager-service-gateway", root / host.STATE,
                    Path(gateway_host_runtime.__file__),
                ),
            )
        except BaseException as error:
            if (
                getattr(error, "cleanup_confirmed", False) is True
                or getattr(error, "process_created", None) is False
            ):
                clear_launch(root, nonce)
            raise
    finally:
        lock.release()
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise PlanError("Incoming Gateway host exited before readiness")
            lock = _lock(root, deadline)
            try:
                try:
                    current = _record(root)
                except RuntimeEvidenceError:
                    current = None
                if (
                    current is not None and current["generation"] == slot.name
                    and current["selection"] == expected and current["token"] == nonce
                ):
                    observed = _probe(current)
                    if (
                        observed is not None and observed.get("ready") is True
                        and observed.get("startup") == startup
                    ):
                        if host.selected_version(root) != slot.name:
                            raise PlanError("Gateway selection changed during readiness")
                        if host.startup_digest(selection) != startup:
                            raise PlanError("Gateway startup inputs changed during readiness")
                        return {**observed, "reused": False}
            finally:
                lock.release()
            time.sleep(.1)
        raise PlanError("Incoming Gateway host readiness deadline expired")
    except BaseException as error:
        observed = _settle_failed_launch(root, selection, slot, nonce, startup, process)
        if observed is not None and isinstance(error, PlanError):
            return observed
        raise
