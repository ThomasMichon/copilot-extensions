"""Advisory deploy-lag finding for ``agent-dispatch doctor``.

Task/lease/reservation findings can be symptoms of a coordinator still running
code older than fixes that already merged and installed. This module compares
four versions and reports where they diverge:

* **running** -- the coordinator process, as advertised by its ``/health``;
* **runtime** -- this host's selected runtime slot (the ``current-version``
  marker under the install root);
* **payload** -- this host's installed plugin payload (``plugin.json``), which
  a payload-only update advances before a new runtime slot is installed;
* **channel** -- the latest version on the configured release channel, when a
  reader for it is available.

``running`` is compared with ``runtime`` only when the coordinator is verified
to be a process on this host; a shared, explicit or failover coordinator runs
another installation, so that comparison is ``unknown``.

It is read-only and takes no action: no restart and no update. Any version it
cannot read or order is reported as ``unknown``, never as current.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

ChannelReader = Callable[[], "str | None"]
PAYLOAD_ROOT_ENV = "COPILOT_PLUGIN_ROOT"


def _default_payload_root() -> Path:
    override = os.environ.get(PAYLOAD_ROOT_ENV)
    if override:
        return Path(override)
    return Path.home() / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-dispatch"


def read_payload_version(payload_root: Path) -> str | None:
    try:
        data = json.loads((payload_root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) and version.strip() else None


def _compare(older_label: str, older: str | None, newer_label: str, newer: str | None) -> dict[str, Any]:
    if not older or not newer:
        missing = older_label if not older else newer_label
        return {"status": "unknown", "detail": f"{missing} version unavailable"}
    try:
        old_v, new_v = Version(older), Version(newer)
    except InvalidVersion:
        if older == newer:
            return {"status": "current", "detail": f"{older_label} matches {newer_label} ({older})"}
        return {"status": "unknown", "detail": f"cannot order {older_label} {older} against {newer_label} {newer}"}
    if old_v == new_v:
        return {"status": "current", "detail": f"{older_label} matches {newer_label} ({older})"}
    if (old_v.local or new_v.local) and old_v.public == new_v.public:
        # Same release, different source/repair generation: hashes have no order.
        return {"status": "unknown", "detail": f"{older_label} {older} and {newer_label} {newer} are different builds of one release"}
    if old_v < new_v:
        return {"status": "behind", "detail": f"{older_label} {older} is older than {newer_label} {newer}"}
    return {"status": "ahead", "detail": f"{older_label} {older} is newer than {newer_label} {newer}"}


def deploy_lag(
    *,
    running: str | None,
    runtime: str | None,
    payload: str | None,
    coordinator_local: bool,
    channel_reader: ChannelReader | None = None,
) -> dict[str, Any]:
    """Build the advisory deploy-lag block from already-gathered versions."""
    channel: str | None = None
    channel_error: str | None = None
    if channel_reader is None:
        channel_error = "no release-channel reader configured on this host"
    else:
        try:
            channel = channel_reader()
        except Exception as exc:  # noqa: BLE001 - any read failure is "unknown"
            channel_error = f"channel read failed: {exc}"
        if not channel and channel_error is None:
            channel_error = "channel read returned no version"

    restart = (
        _compare("running", running, "runtime", runtime)
        if coordinator_local
        else {"status": "unknown", "detail": "coordinator is not a verified process on this host"}
    )
    install = _compare("runtime", runtime, "payload", payload)
    update = (
        {"status": "unknown", "detail": channel_error}
        if channel_error
        else _compare("payload", payload, "channel", channel)
    )
    findings = []
    if restart["status"] == "behind":
        findings.append("running coordinator is older than the selected runtime; a restart would pick it up")
    if install["status"] == "behind":
        findings.append("selected runtime is older than the installed payload; a runtime install is pending")
    if update["status"] == "behind":
        findings.append("installed payload is older than the release channel; an update is pending")
    return {
        "advisory": True,
        "versions": {"running": running, "runtime": runtime, "payload": payload, "channel": channel},
        "coordinator_local": coordinator_local,
        "restart_pending": restart,
        "install_pending": install,
        "update_pending": update,
        "findings": findings,
    }


def _is_local_process(slot: Any) -> bool:
    """True when ``/health``'s slot pid + start time match a process on this host."""
    if not isinstance(slot, dict):
        return False
    pid, started = slot.get("pid"), slot.get("process_start_time")
    if type(pid) is not int or pid <= 0 or started is None:
        return False
    try:
        from zdd.diagnostics import process_start_time

        return bool(process_start_time(pid) == started)
    except Exception:  # noqa: BLE001 - unverifiable means not local
        return False


def gather(
    client: Any,
    *,
    install_root: Path | None = None,
    payload_root: Path | None = None,
    channel_reader: ChannelReader | None = None,
    is_local: Callable[[Any], bool] = _is_local_process,
) -> dict[str, Any]:
    """Read the versions and return :func:`deploy_lag`'s block."""
    from .self_update import read_current_version

    try:
        health = client.health()
    except Exception:  # noqa: BLE001 - an unreachable coordinator is "unknown"
        health = {}
    running = health.get("version") or None
    if install_root is None:
        from .install_paths import install_dir

        install_root = install_dir()
    return deploy_lag(
        running=running,
        runtime=read_current_version(install_root) or None,
        payload=read_payload_version(payload_root or _default_payload_root()),
        coordinator_local=is_local(health.get("slot")),
        channel_reader=channel_reader,
    )
