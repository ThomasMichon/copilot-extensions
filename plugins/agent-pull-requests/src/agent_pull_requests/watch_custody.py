"""Exact reserved-child handshakes, not shared active-owner discovery."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from .watch_installation import WatchInstallation
from .watch_private import write_private
from .watch_runtime import WatchInstance, WatchRuntime


def require_absent(installation: WatchInstallation) -> None:
    """Probe native writer availability, not stale rendezvous or holder PID text."""
    from single_instance_lease import SingleInstance

    if os.path.lexists(installation.state_root):
        info = installation.state_root.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise PermissionError("watch absence probe requires an ordinary state directory")
        lease = SingleInstance(installation.state_root, service="agent-pull-requests-watch")
        lease.acquire()
        lease.release()


def endpoint_value(endpoint: tuple[str, int, str]) -> dict:
    host, port, token = endpoint
    if (
        host != "127.0.0.1" or type(port) is not int or not 0 < port <= 65535
        or not isinstance(token, str) or not 32 <= len(token) <= 256
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in token)
    ):
        raise ValueError("watch custody requires a private authenticated loopback endpoint")
    return {"host": host, "port": port, "token": token}


def endpoint_tuple(value: object) -> tuple[str, int, str]:
    if not isinstance(value, dict) or set(value) != {"host", "port", "token"}:
        raise ValueError("watch endpoint custody has unknown or missing fields")
    endpoint = value["host"], value["port"], value["token"]
    endpoint_value(endpoint)
    return endpoint


def launch_path(installation: WatchInstallation, document: dict) -> Path:
    # The transaction ID has already been validated by the journal reader.
    return installation.run_root / f'watch-owner-launch-{document["transactionId"]}.json'


def publish_launch(
    installation: WatchInstallation, instance: WatchInstance, *,
    endpoint: tuple[str, int, str], read_health,
    environment: Mapping[str, str] | None = None,
) -> None:
    """The actual effect-free child writes once under its live controller's guard."""
    from .watch_journal import (
        _cleanup_document, _controller_held, _passive_health, _read_identity,
    )

    installation.require_current(environment=environment)
    document = _read_identity(installation, environment=environment)
    _controller_held(installation, document["controller"], None)
    _cleanup_document(installation, document)
    if (
        instance.pid != os.getpid() or document["phase"] != "prepared"
        or document["successor"] is not None
        or document["launchInstanceId"] != instance.instance_id
        or document["target"] != instance.runtime.evidence()
        or (document["predecessor"] is not None
            and instance.pid == document["predecessor"]["pid"])
    ):
        raise PermissionError("watch launch is not the exact reserved passive child")
    value = endpoint_value(endpoint)
    health = read_health()
    _passive_health(instance, health)
    instance.require_live(
        installation, read_health=lambda: health, environment=environment,
    )
    # Recheck all custody after the live health proof, before publication.
    if _read_identity(installation, environment=environment) != document:
        raise PermissionError("watch launch transaction changed during attestation")
    _controller_held(installation, document["controller"], None)
    installation.require_current(environment=environment)
    _cleanup_document(installation, document)
    write_private(launch_path(installation, document), {
        "schema": 1, "transactionId": document["transactionId"],
        "installation": document["installation"], "instance": instance.evidence(),
        "endpoint": value,
    }, replace=False)


def read_launch(
    installation: WatchInstallation, document: dict, *,
    environment: Mapping[str, str] | None = None,
) -> tuple[WatchInstance, tuple[str, int, str]]:
    """Read only the known transaction's receipt; health and control remain separate."""
    from .watch_journal import _json_object, _private_bytes

    raw = _private_bytes(launch_path(installation, document))
    if raw is None:
        raise FileNotFoundError("watch reserved child has not published launch custody")
    value = _json_object(raw)
    if (
        set(value) != {"schema", "transactionId", "installation", "instance", "endpoint"}
        or type(value["schema"]) is not int or value["schema"] != 1
        or value["transactionId"] != document["transactionId"]
        or value["installation"] != document["installation"]
    ):
        raise PermissionError("watch launch receipt belongs to another transaction or cell")
    target = WatchRuntime.from_identity_evidence(
        document["target"], installation, environment=environment,
    )
    recorded = value["instance"]
    if not isinstance(recorded, dict):
        raise ValueError("watch launch instance must be an object")
    instance = WatchInstance(
        target, recorded.get("pid"), recorded.get("startTime"), recorded.get("instanceId"),
    )
    if (
        instance.evidence() != recorded or instance.instance_id != document["launchInstanceId"]
        or (document["predecessor"] is not None
            and instance.pid == document["predecessor"]["pid"])
        or document["successor"] not in (None, instance.evidence())
    ):
        raise PermissionError("watch launch receipt does not match its reserved child")
    endpoint = endpoint_tuple(value["endpoint"])
    if document["endpoints"]["successor"] not in (None, value["endpoint"]):
        raise PermissionError("watch launch endpoint changed after binding")
    return instance, endpoint
