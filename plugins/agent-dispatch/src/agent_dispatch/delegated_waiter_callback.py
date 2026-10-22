"""PR-owner callback: exit zero only for a provably settled exact generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# File-entry callbacks must expose the package parent, not shadow stdlib
# modules such as 'queue' with this package's own files.
if __name__ == "__main__" and not __package__:
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

import httpx
from agent_dispatch.delegated_waiter_contract import PROTOCOL, validate_delegation, validate_event


def _validated_envelope(envelope: dict) -> dict:
    identity = validate_delegation(envelope["delegation"])
    fields = {"task_id", "generation", "delegation"}
    if "legacy_root" in envelope and "installation" not in identity:
        fields.add("legacy_root")
    if (
        set(envelope) != fields
        or not isinstance(envelope["task_id"], str) or not envelope["task_id"]
        or type(envelope["generation"]) is not int or not 0 < envelope["generation"] < 2**63
    ):
        raise ValueError("invalid callback envelope")
    return identity


def complete(envelope: dict, event: dict, client_factory) -> int:
    identity = _validated_envelope(envelope)
    validate_event(event, identity)
    args = argparse.Namespace(shared=identity["shared"], url=None, token=None, control_token=None)
    with client_factory(args, ensure=False) as client:
        capability = client.health().get("delegated_run_waiters", {})
        if (
            not isinstance(capability, dict)
            or capability.get("protocol") != PROTOCOL
            or capability.get("coordinator_id") != identity["coordinator_id"]
        ):
            return 1
        result = client.finish_delegated_waiter(
            envelope["task_id"], generation=envelope["generation"],
            delegation=identity, event=event,
        )
        return 0 if result.get("accepted") is True else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1:
            raise ValueError("invalid callback arguments")
        raw_envelope = args[0]
        if raw_envelope.startswith("hex:"):
            raw_envelope = bytes.fromhex(raw_envelope[4:]).decode("utf-8")
        envelope = json.loads(raw_envelope)
        identity = _validated_envelope(envelope)
        if "installation" in identity:
            from agent_dispatch.delegated_waiter_context import enter
            result = enter(identity["installation"], envelope)
            if result is not None:
                return result
        else:
            from agent_dispatch.delegated_waiter_context import restore_legacy
            restore_legacy(envelope.get("legacy_root"))
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            raise ValueError("oversized callback")
        return complete(envelope, json.loads(raw), callback_client)
    except (OSError, RuntimeError, httpx.HTTPError, ValueError, KeyError, TypeError):
        print("agent-dispatch: delegated callback not acknowledged", file=sys.stderr)
        return 1


def callback_client(args, *, ensure: bool = False):
    """Resolve the supported ambient route without booting services or the CLI."""
    from agent_dispatch.client import DispatchClient
    from agent_dispatch.config import (
        client_url, client_token, client_control_token,
        shared_url, shared_token, shared_control_token,
    )
    if ensure:
        raise ValueError("callbacks must not autostart coordinators")
    if args.shared:
        url = shared_url()
        if not url:
            raise ValueError("shared callback route unavailable")
        return DispatchClient(url, token=shared_token(), control_token=shared_control_token(), timeout=3)
    return DispatchClient(client_url(), token=client_token(), control_token=client_control_token(), timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
