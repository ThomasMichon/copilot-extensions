"""Read-only registered claim callback; never provisions or starts a service."""

from __future__ import annotations

import json

from .relationship_authority import NAMESPACE


def claim_status(args) -> None:
    from .__main__ import _get_client

    subject = NAMESPACE + ":" + args.ref
    if not args.ref.startswith("l1.") or len(subject) > 4096:
        raise ValueError("Invalid retained relationship callback ref")
    result = _get_client(ensure=False)._request(
        "GET", "/api/v1/sessions/relationship-verification", params={"subject": subject},
    )
    print(json.dumps(result))


def register(sub) -> None:
    parser = sub.add_parser("claim-status", help="Verify an exact retained relationship subject")
    parser.add_argument("ref")
    parser.set_defaults(func=claim_status)
    inspect = sub.add_parser("relationship-inspect", help="Read active or retired external relationship evidence")
    inspect.add_argument("session_id")
    inspect.set_defaults(func=inspect_relationship)


def inspect_relationship(args) -> None:
    from urllib.parse import quote
    from .__main__ import _get_client

    result = _get_client(ensure=False)._request(
        "GET", f"/api/v1/sessions/{quote(args.session_id, safe='')}/relationship",
    )
    print(json.dumps(result))
