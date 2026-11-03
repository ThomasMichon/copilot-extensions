"""Owning passive observe-owner parser and bounded authenticated transport."""

from __future__ import annotations

import argparse
from typing import Any
from urllib.parse import urlencode

from .client import BridgeClientError, BridgeConnectionError
from .owner_membership import CAPABILITY
from .owner_membership_contract import OwnerRead, validate
from .session_observation_cli import ObservationStream
from .session_observation_contract import ObservationError, encode


class MembershipStream(ObservationStream):
    schemas = frozenset({CAPABILITY})


def read(client: Any, request: OwnerRead) -> MembershipStream:
    capabilities = client.health().get("capabilities")
    if (
        not isinstance(capabilities, dict)
        or type(capabilities.get(CAPABILITY)) is not int
        or capabilities[CAPABILITY] != 1
    ):
        raise ObservationError("unsupported", "selected daemon does not support owner membership")
    query = {
        "contract_version": "1",
        "owner_ref": request.owner,
        "follow": "true" if request.follow else "false",
    }
    if request.after is not None:
        query["after"] = request.after
    return client._stream_sse(
        "/api/v1/owner-observations?" + urlencode(query),
        request_timeout=15,
        stream_factory=MembershipStream,
    )


def _cmd(args: argparse.Namespace) -> None:
    from . import __main__ as core

    request = None
    try:
        pairs = [
            ("contract_version", str(args.contract_version)),
            ("owner_ref", args.owner_ref),
            ("follow", "true" if args.follow else "false"),
        ]
        if args.after is not None:
            pairs.append(("after", args.after))
        request = OwnerRead.parse(pairs)
        if args.project is not None and request.owner.split("/")[1] != args.project:
            raise ObservationError(
                "selection-mismatch", "project does not match the selected owner"
            )
        with read(core._get_client(ensure=False), request) as frames:
            for body in frames:
                validate(body, request)
                print(encode(body).decode("ascii"), flush=True)
                if body["kind"] in {"error", "gap"}:
                    raise SystemExit(3)
                if not request.follow or body["kind"] == "going-away":
                    return
            raise ObservationError(
                "source-unavailable", "membership transport ended before completion"
            )
    except (ObservationError, BridgeClientError, BridgeConnectionError, OSError) as exc:
        code = exc.code if isinstance(exc, ObservationError) else "source-unavailable"
        message = (
            str(exc) if isinstance(exc, ObservationError) else "Bridge membership read failed"
        )
        print(
            encode(
                {
                    "schema": CAPABILITY,
                    "version": 1,
                    "kind": "error",
                    "code": code,
                    "message": message,
                    "source": {
                        "provider": "bridge",
                        "owner_ref": request.owner if request is not None else None,
                        "hosting_generation": None,
                        "journal_incarnation": None,
                        "head_session_id": None,
                    },
                }
            ).decode("ascii"),
            flush=True,
        )
        raise SystemExit(3) from exc


def register_commands(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "observe-owner", help="Passively observe an admitted owner's membership"
    )
    parser.add_argument("--owner-ref", required=True)
    parser.add_argument("--contract-version", type=int, required=True, choices=[1])
    parser.add_argument("--json", action="store_true", required=True)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--after")
    parser.set_defaults(func=_cmd)
