"""Optional bulk representation of the existing immutable chunk operation."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
from typing import TYPE_CHECKING, Any

from fleet_manager.gateway_service_channel import BODY_BYTES, RESPONSE_BYTES

if TYPE_CHECKING:
    from fleet_manager.gateway_service_handler import BoundedServiceResponse, ServiceResponse
    from .workspace_operations import WorkspaceOperations

FIELD = "bulk_materialization"
PROTOCOL = "agent-pull-requests.bulk-materialization/v1"
MEDIA_TYPE = "application/vnd.agent-pull-requests.workspace-bulk.v1+json"
MAX_CHUNKS = 64
Document = dict[str, Any]


def contract(response_bytes: int = RESPONSE_BYTES) -> Document:
    """Describe only this executing receiver's optional representation."""
    from .workspace_operations import CHUNK_BYTES

    return {
        "protocol": PROTOCOL, "operation": "chunk", "accept": MEDIA_TYPE,
        "chunk_bytes": CHUNK_BYTES, "max_chunks": MAX_CHUNKS,
        "response_bytes": response_bytes, "legacy_response": "single-chunk",
    }


def supports(value: object) -> bool:
    """Validate the complete optional contract without guessing peer support."""
    return (
        isinstance(value, dict) and type(value.get("response_bytes")) is int
        and BODY_BYTES < value["response_bytes"] <= RESPONSE_BYTES
        and value == contract(value["response_bytes"])
        and all(type(value.get(name)) is int for name in (
            "chunk_bytes", "max_chunks", "response_bytes",
        ))
    )


def available(response_limit: int) -> bool:
    """Require the actual qualified serving binding, not a package version."""
    try:
        from fleet_manager.gateway_service_handler import BoundedServiceResponse
    except ImportError:
        return False
    return (
        callable(BoundedServiceResponse) and type(response_limit) is int
        and BODY_BYTES < response_limit <= RESPONSE_BYTES
    )


def response(data: bytes) -> ServiceResponse | BoundedServiceResponse:
    from fleet_manager.gateway_service_handler import ServiceResponse

    headers = (("content-type", "application/json"), ("cache-control", "no-store"))
    if len(data) <= BODY_BYTES:
        return ServiceResponse(200, headers, data)
    from fleet_manager.gateway_service_handler import BoundedServiceResponse

    return BoundedServiceResponse(200, headers, data)


def grouped_response(
    workspace: WorkspaceOperations, payload: Document, *, binding: Document,
    selection: object, expires_at: int, checkpoint: Callable[[], None],
    response_limit: int,
) -> bytes:
    """Read through the existing owner and split at the actual encoded bound."""
    from .owner_read import OwnerReadResponseTooLarge
    from .workspace_operations import CHUNK_BYTES
    from .workspace_terminal_read import serve

    frames: list[Document] = []
    selected = dict(payload)
    encoded: bytes | None = None
    descriptor: object = None
    scope: object = None
    protocol: object = None
    for _ in range(MAX_CHUNKS):
        checkpoint()
        value = serve(
            workspace, "chunk", selected, expires_at=expires_at,
            checkpoint=checkpoint, selection=selection,
        )
        frame = value["result"]
        if not frames:
            descriptor, scope, protocol = frame["descriptor"], value["scope"], value["protocol"]
        elif (
            frame["descriptor"] != descriptor or value["scope"] != scope
            or value["protocol"] != protocol
        ):
            raise ValueError("immutable bulk scope changed")
        frames.append(frame)
        candidate = json.dumps({
            "protocol": protocol, "scope": scope,
            "result": {FIELD: PROTOCOL, "chunks": frames}, "binding": binding,
        }, allow_nan=False).encode("utf-8")
        checkpoint()
        if len(candidate) > min(response_limit, RESPONSE_BYTES):
            if encoded is None:
                raise OwnerReadResponseTooLarge("immutable frame exceeds response limit")
            return encoded
        encoded = candidate
        next_offset = selected["offset"] + CHUNK_BYTES
        if next_offset >= frame["descriptor"]["bytes"]:
            break
        selected["offset"] = next_offset
    if encoded is None:
        raise ValueError("empty immutable bulk response")
    return encoded


def checked_frames(result: Document, descriptor: Document, offset: int) -> list[Document]:
    """Validate grouped and supported rollback replies before retaining frames."""
    from .workspace_operations import CHUNK_BYTES

    if FIELD in result or "chunks" in result:
        if (
            set(result) != {FIELD, "chunks"} or result[FIELD] != PROTOCOL
            or not isinstance(result["chunks"], list)
            or not 0 < len(result["chunks"]) <= MAX_CHUNKS
        ):
            raise ValueError("workspace_bulk_changed")
        frames: list[Document] = result["chunks"]
    else:
        frames = [result]
    for frame in frames:
        if (
            not isinstance(frame, dict) or frame.get("descriptor") != descriptor
            or type(frame.get("offset")) is not int or frame["offset"] != offset
            or not isinstance(frame.get("data"), str) or offset >= descriptor["bytes"]
        ):
            raise ValueError("workspace_chunk_changed")
        try:
            block = base64.b64decode(frame["data"], validate=True)
        except ValueError:
            raise ValueError("workspace_chunk_invalid") from None
        if len(block) != min(CHUNK_BYTES, descriptor["bytes"] - offset):
            raise ValueError("workspace_chunk_incomplete")
        offset += len(block)
    return frames
