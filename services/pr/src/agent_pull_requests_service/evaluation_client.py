"""One bounded local comparison; reference acquisition remains with its existing owner."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import sys
import time
from typing import TYPE_CHECKING, Any

from .local_client import (
    MAX_BYTES, LocalClient, _invalid_constant, _unique,
)

if TYPE_CHECKING:
    from .evaluation import Reference

COMMAND = "compare"
REFERENCE_SCHEMA = "agent-pull-requests.comparison-reference"


def load_reference(path: Path, number: int) -> Reference:
    """Read bounded ordinary JSON, without treating its provenance fields as authority."""
    from agent_pull_requests.repository_contract import RepositoryIdentity
    from agent_pull_requests.repository_projection import validate_identity
    from agent_pull_requests.watch_provider import decode_snapshot

    from .evaluation import Reference

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("comparison reference requires an ordinary file")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("comparison reference exceeds its byte budget")
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_invalid_constant)
    if (
        not isinstance(value, dict) or set(value) != {
            "schema", "version", "identity", "observed_at", "snapshot",
        } or value["schema"] != REFERENCE_SCHEMA
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["observed_at"]) is not int or value["observed_at"] < 0
    ):
        raise ValueError("unsupported comparison reference")
    identity = RepositoryIdentity.parse(value["identity"])
    snapshot = decode_snapshot(value["snapshot"], identity.repo, number)
    validate_identity(snapshot, identity, number)
    return Reference(identity, snapshot, value["observed_at"])


def read_comparison(
    client: LocalClient, reference: Reference, *, consumer: str, reviewer: str,
    number: int, max_age_seconds: int = 1800, seconds: int = 60,
) -> dict[str, Any]:
    """Read a PR and first action page once; never subscribe, ACK, fetch or retry."""
    from agent_pull_requests.repository_projection import validate_identity

    from .evaluation import compare

    if (
        type(number) is not int or number < 1
        or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400
        or type(seconds) is not int or not 1 <= seconds <= 60
        or not isinstance(consumer, str) or not consumer.strip()
        or not isinstance(reviewer, str) or not reviewer.strip()
        or client.namespace != reference.identity.namespace
    ):
        raise ValueError("comparison selection differs")
    validate_identity(reference.snapshot, reference.identity, number)
    previous = client.deadline
    end = time.monotonic() + seconds
    client.deadline = min(previous, end) if previous is not None else end
    try:
        pulls = client.request("query", {"number": number})
        feed = client.request("next-actions", {
            "reviewer": reviewer, "after": 0, "limit": 16,
            "max_age_seconds": max_age_seconds,
        })
        now = int(time.time())
        result = compare(
            pulls, feed, reference, identity=reference.identity, consumer=consumer,
            reviewer=reviewer, number=number, now=now,
            max_age_seconds=max_age_seconds,
        )
        return {
            "schema": "agent-pull-requests.comparison", "version": 1,
            "identity": reference.identity.document(), "target": client.target,
            "consumer": consumer, "reviewer": reviewer,
            "reference_observed_at": reference.observed_at, "compared_at": now,
            "cache_cursor": pulls["cursor"], "action_event_cursor": feed["event_cursor"],
            "comparison": asdict(result),
        }
    finally:
        client.deadline = previous


def add_command(commands: Any) -> None:
    parser = commands.add_parser(COMMAND, allow_abbrev=False)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=int, default=1800)
    parser.add_argument("--timeout", type=int, default=60)


def run(args: argparse.Namespace) -> int:
    try:
        reference = load_reference(args.reference, args.number)
        client = LocalClient(args.socket, args.namespace, args.target)
        result = read_comparison(
            client, reference, consumer=args.consumer, reviewer=args.reviewer,
            number=args.number, max_age_seconds=args.max_age_seconds, seconds=args.timeout,
        )
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError, ImportError):
        print("local comparison unavailable; reference and original identities retained",
              file=sys.stderr)
        return 2
