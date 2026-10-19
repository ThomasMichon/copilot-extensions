"""Installer-owned activation of the actual completed running target."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import _installation_context as api
from .watch_entry import incumbent, run_target
from .watch_installation import PLUGIN_ID, WatchInstallation
from .watch_invocation import WatchInvocation, capture_running
from .watch_journal import WatchJournal
from .watch_selection import WatchSelection


def invocation(context: Path, payload: Path, snapshot_id: str, runtime_version: str) -> WatchInvocation:
    root = Path(sys.prefix).parent.parent
    if root.name != PLUGIN_ID or root.parent.name != "plugins":
        raise PermissionError("watch installer must execute in its actual completed target slot")
    _, profile = api._current_environment(
        environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
    )
    installation = WatchInstallation.capture(
        receipt=context, plugin_root=root, payload_root=payload,
        durable_home=root.parent.parent.parent.parent, os_profile=profile, environment={},
    )
    runtime = capture_running(installation, environment={})
    if (
        (runtime.snapshot_id, runtime.runtime_version) != (snapshot_id, runtime_version)
        or not api.paths_equal(runtime.payload_root, payload)
    ):
        raise PermissionError("watch installer target differs from the actual running slot")
    return WatchInvocation(installation, runtime, str(context))


def _manifest(target: WatchInvocation) -> bytes:
    runtime = target.runtime
    completed = api.validate_runtime_slot_completion(
        context=target.installation.receipt,
        expected_marketplace_id=target.installation.marketplace_id, expected_plugin_id=PLUGIN_ID,
        snapshot_id=runtime.snapshot_id, runtime_version=runtime.runtime_version,
        expected_payload_root=runtime.payload_root, expected_payload_version=runtime.payload_version,
        durable_home=target.installation.durable_home, environment={},
    )
    return json.dumps({
        "schema_version": 3, "service": PLUGIN_ID, "runtime": "python",
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "kind": "marketplace", "path": str(runtime.payload_root),
            "plugin": PLUGIN_ID, "version": runtime.payload_version,
            "content_hash": completed["payloadSha256"], "snapshot_id": runtime.snapshot_id,
        },
        "venv": str(runtime.runtime_root),
    }, sort_keys=True).encode("utf-8")


def activate(target: WatchInvocation, *, timeout: float = 120.0) -> dict:
    target.require_current()
    installation = target.fresh_installation()
    with installation.transaction(timeout=timeout, environment={}) as guard:
        target.require_current()
        owner = incumbent(target, guard, timeout=timeout)
        if owner is not None and owner.instance.runtime == target.runtime:
            return {"status": "ready", "activated": True, "ownerStarted": False}
        journal = WatchJournal.begin(
            installation, guard, target=target.runtime, target_manifest=_manifest(target),
            predecessor=None if owner is None else owner.instance, environment={},
        )
        if owner is None:
            WatchSelection(journal).publish()
            journal.commit()
            journal.finish()
            journal.archive()
        else:
            result = run_target(journal, target, poll_interval=30.0, timeout=timeout)
            if not result.ok or journal._document["phase"] != "complete":
                raise RuntimeError("watch installer activation rolled back")
        return {"status": "ready", "activated": True, "ownerStarted": owner is not None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--runtime-version", required=True)
    args = parser.parse_args(argv)
    try:
        target = invocation(args.context, args.payload_root, args.snapshot_id, args.runtime_version)
        print(json.dumps(activate(target)))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Watch activation refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
