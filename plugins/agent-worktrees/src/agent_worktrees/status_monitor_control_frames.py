"""JSON-line request framing for the resident monitor control plane."""

from __future__ import annotations

import json
import secrets
import select
import sys

from . import status_monitor_recovery_contract as guarded_recovery


def handle_control_request(
    handler, *, read_timeout_s: float, receipt_action: str, frame_bytes: int, unique_object,
) -> None:
    """Handle one authenticated request using the server owner's dispatchers."""
    current_read = None
    action = None
    try:
        handler.request.settimeout(read_timeout_s)
        raw = handler.rfile.readline(256 * 1024)
        request = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(request, dict)
            or not secrets.compare_digest(
                str(request.get("token") or ""), handler.server.token
            )
        ):
            return
        action = str(request.get("action") or "health")
        if action in {"agenda-read", "agenda-publish", "agenda-feed"}:
            from . import agenda_contract

            try:
                request = agenda_contract.decode(raw)
                agenda_contract.exact(request, {"version", "token", "action", "payload"})
                if not raw.endswith(b"\n") or (
                    type(request["version"]) is not int or request["version"] != 1
                ):
                    raise agenda_contract.AgendaError("invalid agenda control frame")
            except agenda_contract.AgendaError as exc:
                error = agenda_contract.failure(
                    exc.code, str(exc), version=agenda_contract.error_version(request.get("payload")),
                )
                reply = {"type": "error", **error} if action == "agenda-feed" else {
                    "version": 1, "result": error,
                }
                handler.wfile.write(agenda_contract.encode(reply).encode("utf-8") + b"\n")
                return
        if action == receipt_action and (
            len(raw) > frame_bytes or not raw.endswith(b"\n")
            or set(request) != {"version", "token", "action", "payload"}
            or type(request.get("version")) is not int or request["version"] != 1
        ):
            handler.wfile.write(b'{"version":1,"result":{"contractVersion":1,'
                                b'"status":"unavailable","reason":"invalid-frame",'
                                b'"replayed":false,"subject":null,"owner":null}}\n')
            return
        if action == receipt_action:
            request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        if action == guarded_recovery.ACTION:
            request = guarded_recovery.decode_request(raw)
        payload = request.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        owner = handler.server.owner
        if action in {"worktrees-read", "repos-map-read", "picker-bootstrap",
                      "agenda-read", "agenda-publish"}:
            current_read = "worktrees-read" if action == "picker-bootstrap" else action
            owner._on_current_read_started(current_read)
        if owner.stream_handle is not None and action in {"worktrees-feed", "agenda-feed"}:
            def disconnected():
                readable, _, _ = select.select([handler.request], [], [], 0)
                return bool(readable)

            frames = owner.stream_handle(action, payload, disconnected)
            try:
                for frame in frames:
                    if action == "agenda-feed":
                        from .agenda_contract import encode

                        encoded = encode(frame).encode("utf-8") + b"\n"
                    else:
                        encoded = json.dumps(
                            frame, separators=(",", ":"), ensure_ascii=False,
                        ).encode("utf-8") + b"\n"
                    handler.wfile.write(
                        encoded
                    )
                    handler.wfile.flush()
            finally:
                frames.close()
            return
        result = handler.server.owner.handle(action, payload)
        if not isinstance(result, dict):
            result = {}
        encoded = json.dumps(
            {"version": 1, "result": result}, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        if action == receipt_action and len(encoded) > frame_bytes:
            encoded = (b'{"version":1,"result":{"contractVersion":1,'
                       b'"status":"unknown","reason":"response-too-large",'
                       b'"replayed":false,"subject":null,"owner":null}}\n')
        handler.wfile.write(encoded)
    except guarded_recovery.RecoveryUnavailable as exc:
        handler.wfile.write(guarded_recovery.encode_result(guarded_recovery.refused(str(exc))))
    except Exception as exc:
        if action == guarded_recovery.ACTION:
            print(f"guarded-undrain receiver failed: {type(exc).__name__}", file=sys.stderr)
            handler.wfile.write(guarded_recovery.encode_result(guarded_recovery.refused("receiver-error")))
        return
    finally:
        if current_read:
            handler.server.owner._on_current_read_finished(current_read)
