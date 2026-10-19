"""Installer-owned activation of the actual completed running target."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from . import _installation_context as api
from .watch_entry import _client, incumbent, run_target
from .watch_installation import PLUGIN_ID, WatchInstallation
from .watch_invocation import WatchInvocation, capture_running
from .watch_journal import WatchJournal, _target_selection, read_known_owner
from .watch_notification import positive_seconds
from .watch_recovery import reconcile
from .watch_selection import WatchSelection


_INTENT_FIELDS = ("transactionId", "installation", "target", "successor")


class _PendingActivationTimeout(TimeoutError):
    """Comparison evidence from this attempt, never a recovery capability."""

    def __init__(self, error: TimeoutError, document: dict):
        super().__init__(str(error))
        self.intent = {key: deepcopy(document[key]) for key in _INTENT_FIELDS}


def _remaining(deadline: float) -> float:
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise TimeoutError("watch installer activation deadline expired")
    return budget


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
        try:
            if owner is None:
                WatchSelection(journal).publish()
                journal.commit()
                journal.finish()
                journal.archive()
            else:
                result = run_target(journal, target, poll_interval=30.0, timeout=timeout)
                if not result.ok or journal._document["phase"] != "complete":
                    raise RuntimeError("watch installer activation rolled back")
        except TimeoutError as error:
            document = journal._current()
            if document["phase"] in {"committed", "complete"} and document["successor"] is not None:
                raise _PendingActivationTimeout(error, document) from error
            raise
        return {"status": "ready", "activated": True, "ownerStarted": owner is not None}


def _require_intent(target: WatchInvocation, document: dict, expected: dict) -> None:
    if (
        document["phase"] not in {"committed", "complete"}
        or document["successor"] is None
        or any(document[key] != expected[key] for key in _INTENT_FIELDS)
        or document["installation"] != target.installation.evidence()
        or document["target"] != target.runtime.evidence()
    ):
        raise PermissionError("watch installer reconciliation intent changed")


def _reconcile_committed(target: WatchInvocation, expected: dict, *, deadline: float) -> dict:
    target.require_current()
    installation = target.fresh_installation()
    with installation.transaction(timeout=_remaining(deadline), environment={}) as guard:
        target.require_current()
        document = read_known_owner(installation, environment={})
        _require_intent(target, document, expected)
        _target_selection(installation, document)
        owner = _client(target, document, "successor")
        status = owner.health(timeout=_remaining(deadline))
        if (
            status.get("writerHeld") is not True or status.get("readReady") is not True
            or type(status.get("effectsEnabled")) is not bool
        ):
            raise PermissionError("watch installer reconciliation owner is unconfirmed")
        _remaining(deadline)
        journal = WatchJournal.recover(
            installation, guard, environment={}, expected_document=document,
        )
        _require_intent(target, journal._current(), expected)
        reconcile(journal, timeout=min(120.0, _remaining(deadline)))
        document = journal._current()
        _require_intent(target, document, expected)
        if document["phase"] != "complete":
            raise PermissionError("watch installer reconciliation remains pending")
        _target_selection(installation, document)
        status = _client(target, document, "successor").health(timeout=_remaining(deadline))
        if not all(status.get(key) is True for key in (
            "writerHeld", "readReady", "effectsEnabled",
        )):
            raise PermissionError("watch installer reconciled owner is unconfirmed")
        _remaining(deadline)
        return {
            "status": "ready", "activated": True, "ownerStarted": False,
            "reconciled": True, "initialOutcome": "activation-timeout",
        }


def activate_with_reconciliation(target: WatchInvocation, *, timeout: float = 300.0) -> dict:
    """One initial attempt and one exact-owner reconciliation share a deadline."""
    return _activate_until(target, time.monotonic() + positive_seconds(timeout))


def _activate_until(target: WatchInvocation, deadline: float) -> dict:
    try:
        result = activate(target, timeout=min(120.0, _remaining(deadline)))
    except _PendingActivationTimeout as error:
        print(
            f"Watch activation timed out: {error}; checking the exact committed owner "
            "before one bounded reconciliation.",
            file=sys.stderr,
        )
        result = _reconcile_committed(target, error.intent, deadline=deadline)
    _remaining(deadline)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--runtime-version", required=True)
    args = parser.parse_args(argv)
    try:
        deadline = time.monotonic() + 300.0
        target = invocation(args.context, args.payload_root, args.snapshot_id, args.runtime_version)
        print(json.dumps(_activate_until(target, deadline)))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Watch activation refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
