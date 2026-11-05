"""Owning JSON CLI and bounded semantic SSE reader; no service-start fallback."""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.parse import quote, urlencode

from .client import BridgeClientError, BridgeConnectionError, SseStream
from .session_observation_contract import (
    HISTORY,
    CURRENT_REQUESTS,
    MAX_BODY,
    MAX_FRAME,
    OBSERVATIONS,
    TOOL_STATISTICS,
    ObservationError,
    ReadRequest,
    decode,
    encode,
    validate_frame,
)


class ObservationStream(SseStream):
    """Bound allocation before parsing; close the owned response on every failure."""

    schemas = frozenset({OBSERVATIONS, HISTORY})

    def __next__(self) -> dict[str, Any]:
        if self._response is None:
            raise StopIteration
        try:
            size = 0
            event = None
            data = None
            while True:
                line = self._response.readline(MAX_FRAME - size + 1)
                if not line:
                    if size:
                        raise ObservationError(
                            "source-unavailable", "observer transport ended within a frame",
                        )
                    self.close()
                    raise StopIteration
                size += len(line)
                if size > MAX_FRAME:
                    raise ObservationError("frame-too-large", "observation frame exceeds 35 KiB")
                if line in {b"\n", b"\r\n"}:
                    if event is None or data is None:
                        raise ObservationError("invalid-frame", "missing semantic frame fields")
                    body = decode(data)
                    if (
                        body.get("kind") != event
                        or body.get("schema") not in self.schemas
                        or type(body.get("version")) is not int
                        or body["version"] != 1
                    ):
                        raise ObservationError("invalid-frame", "wrong semantic frame contract")
                    return body
                if line.startswith(b"event: ") and event is None:
                    event = line[7:].rstrip(b"\r\n").decode("ascii")
                elif line.startswith(b"data: ") and data is None:
                    data = line[6:].rstrip(b"\r\n")
                    if len(data) > MAX_BODY:
                        raise ObservationError("body-too-large", "observation body exceeds 32 KiB")
                else:
                    raise ObservationError("invalid-frame", "unexpected semantic frame field")
        except UnicodeError as exc:
            self.close()
            raise ObservationError("invalid-frame", "invalid semantic frame encoding") from exc
        except BaseException:
            self.close()
            raise


def read(client: Any, request: ReadRequest) -> ObservationStream:
    """Negotiate with the actual daemon, then open only the dedicated read route."""
    capability = HISTORY if request.history else OBSERVATIONS
    capabilities = client.health().get("capabilities")
    for required in (
        capability, *([CURRENT_REQUESTS] if request.current_requests else []),
        *([TOOL_STATISTICS] if request.tool_statistics else []),
    ):
        if (
            not isinstance(capabilities, dict)
            or type(capabilities.get(required)) is not int
            or capabilities[required] != 1
        ):
            raise ObservationError("unsupported", f"selected daemon does not support {required}")
    query: dict[str, str] = {
        "contract_version": "1",
        "worktree_id": request.selection.worktree_id,
        "limit": str(request.limit),
    }
    if request.selection.project is not None:
        query["project"] = request.selection.project
    if request.selection.head_session_id is not None:
        query["head_session_id"] = request.selection.head_session_id
    if request.history:
        if request.position is not None:
            query["before"] = request.position
    else:
        query["follow"] = "true" if request.follow else "false"
        if request.current_requests:
            query["current_requests"] = "true"
        if request.tool_statistics:
            query["tool_statistics"] = "true"
        if request.position is not None:
            query["after"] = request.position
    route = "session-observation-history" if request.history else "session-observations"
    path = f"/api/v1/{route}/{quote(request.selection.session_id, safe='')}?{urlencode(query)}"
    return client._stream_sse(path, request_timeout=15, stream_factory=ObservationStream)


def _cmd(args: argparse.Namespace) -> None:
    from . import __main__ as core

    history = args.command == "session-observation-history"
    pairs = [("contract_version", str(args.contract_version)), ("worktree_id", args.worktree_id)]
    if args.project is not None:
        pairs.append(("project", args.project))
    if args.head_session_id is not None:
        pairs.append(("head_session_id", args.head_session_id))
    pairs.append(("limit", str(args.limit)))
    if args.position is not None:
        pairs.append(("before" if history else "after", args.position))
    if not history:
        pairs.append(("follow", "true" if args.follow else "false"))
        if getattr(args, "current_requests", False):
            pairs.append(("current_requests", "true"))
        if getattr(args, "tool_statistics", False):
            pairs.append(("tool_statistics", "true"))
    try:
        request = ReadRequest.parse(args.session_id, pairs, history=history)
        with read(core._get_client(ensure=False), request) as stream:
            for body in stream:
                validate_frame(body, request)
                print(encode(body).decode("ascii"), flush=True)
                if body["kind"] in {"error", "gap"}:
                    raise SystemExit(3)
                if history or not request.follow or body["kind"] == "going-away":
                    return
            raise ObservationError(
                "source-unavailable", "observer transport ended before completion",
            )
    except (ObservationError, BridgeClientError, BridgeConnectionError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema": HISTORY if history else OBSERVATIONS,
                    "version": 1,
                    "kind": "error",
                    "code": exc.code
                    if isinstance(exc, ObservationError)
                    else "source-unavailable",
                    "message": str(exc)
                    if isinstance(exc, ObservationError)
                    else "Bridge read failed",
                }
            ),
            flush=True,
        )
        raise SystemExit(3) from exc


def register_commands(sub: argparse._SubParsersAction) -> None:
    """Register distinct strict known-source reads; JSON is mandatory."""
    for command in ("observe-session", "session-observation-history"):
        parser = sub.add_parser(
            command, help="Read bounded semantic evidence for an exact known session",
            allow_abbrev=False,
        )
        parser.add_argument("session_id")
        parser.add_argument("--contract-version", type=int, required=True, choices=[1])
        parser.add_argument("--worktree-id", required=True)
        parser.add_argument("--head-session-id")
        parser.add_argument("--json", action="store_true", required=True)
        parser.add_argument("--limit", type=int, default=64)
        if command == "observe-session":
            parser.add_argument("--follow", action="store_true")
            parser.add_argument("--current-requests", action="store_true")
            parser.add_argument("--tool-statistics", action="store_true")
            parser.add_argument("--after", dest="position")
        else:
            parser.add_argument("--before", dest="position")
        parser.set_defaults(func=_cmd)
