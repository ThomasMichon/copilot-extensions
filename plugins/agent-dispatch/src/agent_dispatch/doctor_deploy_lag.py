"""Advisory deploy-lag finding for ``agent-dispatch doctor``.

Task/lease/reservation findings can be symptoms of a coordinator still running
code older than fixes that already merged and installed. This module compares
three versions and reports where they diverge:

* **running** -- the coordinator process, as advertised by its ``/health``;
* **installed** -- the published runtime slot (the ``current-version`` marker),
  falling back to this CLI's own payload version;
* **channel** -- the latest version on the configured release channel, when a
  reader for it is available.

It is read-only and takes no action: no restart and no update. Any version it
cannot read is reported as ``unknown``, never as current.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .procutil import _version_sort_key

ChannelReader = Callable[[], "str | None"]


def _compare(older_label: str, older: str | None, newer_label: str, newer: str | None) -> dict[str, Any]:
    if not older or not newer:
        missing = older_label if not older else newer_label
        return {"status": "unknown", "detail": f"{missing} version unavailable"}
    if older == newer:
        return {"status": "current", "detail": f"{older_label} matches {newer_label} ({older})"}
    if _version_sort_key(older) < _version_sort_key(newer):
        return {"status": "behind", "detail": f"{older_label} {older} is older than {newer_label} {newer}"}
    return {"status": "differs", "detail": f"{older_label} {older} differs from {newer_label} {newer}"}


def deploy_lag(
    *,
    running: str | None,
    installed: str | None,
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

    restart = _compare("running", running, "installed", installed)
    update = (
        {"status": "unknown", "detail": channel_error}
        if channel_error
        else _compare("installed", installed, "channel", channel)
    )
    findings = []
    if restart["status"] == "behind":
        findings.append("running coordinator is older than the installed payload; a restart would pick it up")
    if update["status"] == "behind":
        findings.append("installed payload is older than the release channel; an update is pending")
    return {
        "advisory": True,
        "versions": {"running": running, "installed": installed, "channel": channel},
        "restart_pending": restart,
        "update_pending": update,
        "findings": findings,
    }


def gather(client: Any, *, install_root: Path | None = None, channel_reader: ChannelReader | None = None) -> dict[str, Any]:
    """Read running/installed versions and return :func:`deploy_lag`'s block."""
    from . import __version__
    from .self_update import read_current_version

    try:
        running = client.health().get("version") or None
    except Exception:  # noqa: BLE001 - an unreachable coordinator is "unknown"
        running = None
    if install_root is None:
        from .install_paths import install_dir

        install_root = install_dir()
    installed = read_current_version(install_root) or __version__ or None
    return deploy_lag(running=running, installed=installed, channel_reader=channel_reader)
