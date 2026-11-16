#!/usr/bin/env python3
"""Capture additive H1 inspection without rewriting historical message vectors."""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import importlib
import json
from pathlib import Path
import re
import struct
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import agent_bridge_contract_git as git
import agent_bridge_contract_registry as codec
from agent_bridge_contract_paths import safe
from agent_bridge_contract_publication import publish, recover_pending_links

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "libs/copilot-driver-core/src/agent_remote_driver/session_host/"
SOURCES = tuple(PREFIX + name + ".py" for name in ("protocol", "host", "inspection", "client"))
INPUT = "plugins/agent-bridge/contract/inputs/session-host/inspection-v1.json"
CURRENT = "plugins/agent-bridge/contract/fixtures/session-host/current/"
SCRIPT = "tools/capture_bridge_host_inspection.py"
SCOPE = (
    "Committed-source H1 codec, Host first-message handler and client inspection; "
    "in-memory streams and synthetic child/controller, no listener, process, "
    "installed provider or native Copilot qualification. Normalize only Host PID, "
    "Host generation and nonnegative RTT; preserve historical message vectors."
)


class Wire:
    """A private in-memory stream; never a process or network endpoint."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass

    def get_extra_info(self, name: str) -> tuple[str, int]:
        if name != "peername":
            raise AssertionError("unexpected stream metadata")
        return "127.0.0.1", 1


async def observe(vector: dict[str, Any]) -> dict[str, Any]:
    """Execute real codecs and dispatch with all effectful boundaries refused."""
    from agent_remote_driver.session_host import protocol
    from agent_remote_driver.session_host.client import SessionHostClient
    from agent_remote_driver.session_host.host import SessionHost

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("inspection attempted a child/process effect")

    child = SimpleNamespace(
        pid=4242, returncode=None, stdout=None,
        stdin=SimpleNamespace(write=forbidden, drain=forbidden),
    )
    host: Any = SessionHost(child, nonce=vector["credential"])
    host._front = object()
    host._attachment_epoch = 7
    host._frames = {1: b"retained"}
    host._max_seq = 1
    host._observe_frame(b'{"id":7,"method":"session/prompt"}\n', to_child=True)
    host._reap_timer = object()
    host._active_reap_timer = object()
    original = (
        host._front, host._attachment_epoch, host.ack_cursor, host.buffered_seqs,
        host._last_reapable, host._graceful_detach, host._reap_timer,
        host._active_reap_timer, host._turn_in_flight, child.returncode,
    )
    requests: list[bytes] = []
    responses: list[bytes] = []

    async def exchange(reader: asyncio.StreamReader, writer: Wire) -> None:
        await host._handle_front(reader, writer)
        responses.append(bytes(writer.data))

    class RequestWire(Wire):
        async def drain(self) -> None:
            requests.append(bytes(self.data))
            reader = asyncio.StreamReader()
            reader.feed_data(bytes(self.data) + base64.b64decode(vector["pipeline_base64"]))
            reader.feed_eof()
            reply = Wire()
            await exchange(reader, reply)
            response_reader.feed_data(bytes(reply.data))
            response_reader.feed_eof()

    async def connect(*args: Any, **kwargs: Any) -> tuple[asyncio.StreamReader, Wire]:
        if args != ("127.0.0.1", 1) or kwargs != {"limit": 4101}:
            raise AssertionError("inspection changed its bounded connection contract")
        return response_reader, RequestWire()

    client = SessionHostClient(asyncio.StreamReader(), cast(asyncio.StreamWriter, Wire()))
    private = await client.inspect()
    client._inspection_credential = vector["credential"]
    client._inspection_child_pid = child.pid
    response_reader = asyncio.StreamReader()
    with (
        patch("agent_remote_driver.session_host.host._tree_kill", forbidden),
        patch.object(asyncio, "open_connection", side_effect=connect),
        patch("agent_remote_driver.session_host.inspection.secrets.token_hex",
              return_value=vector["nonce"]),
    ):
        result = await client.inspect()
        response_reader = asyncio.StreamReader()
        client._inspection_credential = "incorrect"
        refused = await client.inspect()
    current = (
        host._front, host._attachment_epoch, host.ack_cursor, host.buffered_seqs,
        host._last_reapable, host._graceful_detach, host._reap_timer,
        host._active_reap_timer, host._turn_in_flight, child.returncode,
    )
    if current != original or responses[-1] != b"":
        raise ValueError("inspection changed controller/child custody or admitted bad credentials")
    response = json.loads(responses[0][5:])
    if (
        responses[0][:5] != struct.pack(">I", len(responses[0]) - 4) + b"!"
        or response["host_pid"] <= 0
        or re.fullmatch("[0-9a-f]{32}", response["host_generation"]) is None
    ):
        raise ValueError("inspection reply lost its wire or Host identity")
    response["host_pid"] = "<positive-host-pid>"
    response["host_generation"] = "<32-hex-host-generation>"
    snapshot = result.get("snapshot", {})
    if snapshot != {
        key: value for key, value in json.loads(responses[0][5:]).items()
        if key not in {"contract", "version", "nonce"}
    } or not 0 <= result.get("rtt_s", -1):
        raise ValueError("client did not validate the actual Host reply")
    capped = []
    for kind in (protocol.MsgType.PING, protocol.MsgType.PONG):
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", 4098) + kind.value)
        # Deliberately no payload and no EOF: a late check would hang.
        try:
            await asyncio.wait_for(protocol.read_message(reader), .1)
        except protocol.ProtocolError:
            capped.append(kind.value.decode())
    historical = json.loads(safe(ROOT, CURRENT + "messages.json").read_bytes())
    replayed = []
    for name, message in historical["messages"].items():
        wire = base64.b64decode(message["frame_base64"])
        reader = asyncio.StreamReader()
        reader.feed_data(wire)
        reader.feed_eof()
        decoded = await protocol.read_message(reader)
        expected_kind = None if name == "unknown" else base64.b64decode(message["type_base64"])
        if decoded is None or (
            (decoded[0].value if decoded[0] is not None else None) != expected_kind
            or decoded[1] != base64.b64decode(message["payload_base64"])
        ):
            raise ValueError(f"historical H1 decoding changed: {name}")
        if decoded[0] is not None and protocol.encode(decoded[0], decoded[1]) != wire:
            raise ValueError(f"historical H1 encoding changed: {name}")
        replayed.append(name)
    return {
        "request_base64": base64.b64encode(requests[0]).decode(),
        "reply": response, "state": result["state"], "refused": refused,
        "private": private, "custody_unchanged": current == original,
        "early_caps": capped, "historical_messages": replayed,
    }


def source_bytes(commit: str, path: str) -> bytes:
    result = git.git("show", f"{commit}:{path}", "--")
    if result.returncode:
        raise ValueError(f"missing committed capture input: {path}")
    data = safe(ROOT, path).read_bytes()
    if git.sha256_bytes(data) != git.sha256_bytes(result.stdout.encode("utf-8")):
        raise ValueError(f"dirty capture input: {path}")
    return data


def validate_packets(root: Path, contract: dict[str, Any], errors: list[str]) -> None:
    """Keep the new source packets joined to their independent observations."""
    paths = {CURRENT + "inspection-" + Path(source).stem + "-v1.json": source for source in SOURCES}
    fixtures = contract.get("fixtures")
    if not isinstance(fixtures, list):
        return
    registered = {
        row["path"] for row in fixtures
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    if not paths.keys() & registered:
        return
    try:
        if not paths.keys() <= registered:
            raise ValueError("incomplete H1 inspection source portfolio")
        input_bytes = safe(root, INPUT).read_bytes()
        expected = json.loads(input_bytes)["expected"]
        common = None
        for path in sorted(paths):
            packet = json.loads(safe(root, path).read_bytes())
            captured = packet["captured_from"]
            inputs = packet["inputs"]
            if (
                set(packet) != {"captured_from", "scope", "inputs", "observed"}
                or packet["scope"] != SCOPE or packet["observed"] != expected
                or not {*SOURCES, INPUT, SCRIPT, CURRENT + "messages.json"} <= inputs.keys()
                or inputs[INPUT] != git.sha256_bytes(input_bytes)
                or captured["source_path"] != paths[path]
                or inputs[captured["source_path"]] != captured["source_sha256"]
            ):
                raise ValueError("H1 inspection packet scope, observations or input closure changed")
            joined = (captured["commit"], inputs)
            if common is not None and joined != common:
                raise ValueError("H1 inspection source packets do not share one capture")
            common = joined
            for source, sha256 in inputs.items():
                safe(root, source)
                if not re.fullmatch("[0-9a-f]{64}", sha256):
                    raise ValueError("invalid H1 captured input fingerprint")
                if git.ensure_commit_available(captured["commit"]) and (
                    git.git_file_sha256(captured["commit"], source) != sha256
                ):
                    raise ValueError(f"H1 captured input is not source-bound: {source}")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        errors.append(f"H1 inspection evidence: {exc}")


def capture(commit: str, *, write: bool = False) -> dict[str, bytes]:
    """Bind observations only after exact committed inputs have executed."""
    if not re.fullmatch("[0-9a-f]{40}", commit) or not git.ensure_commit_available(commit):
        raise ValueError("capture requires an available full source commit")
    if write:
        recover_pending_links(ROOT, commit)
    snapshot = codec.load(ROOT)
    # Import the canonical source, not a deployed installation.
    sys.path[:0] = [str(path) for path in sorted((ROOT / "libs").glob("*/src"))]
    for name, path in zip(("protocol", "host", "inspection", "client"), SOURCES, strict=True):
        module = importlib.import_module("agent_remote_driver.session_host." + name)
        origin = module.__file__
        if origin is None or Path(origin).resolve() != safe(ROOT, path).resolve():
            raise ValueError(f"capture imported foreign source: {name}")
    paths = {INPUT, SCRIPT, CURRENT + "messages.json", "plugins/agent-bridge/plugin.json"}
    for loaded in list(sys.modules.values()):
        origin = getattr(loaded, "__file__", None)
        if (
            isinstance(origin, str) and origin
            and Path(origin).resolve().is_relative_to(ROOT)
            and Path(origin).suffix == ".py"
        ):
            paths.add(Path(origin).resolve().relative_to(ROOT).as_posix())
    before = {path: git.sha256_bytes(source_bytes(commit, path)) for path in sorted(paths)}
    vector = json.loads(source_bytes(commit, INPUT))
    if vector.get("schema") != "h1-read-only-inspection-v1":
        raise ValueError("H1 inspection input schema changed")
    observed = asyncio.run(asyncio.wait_for(observe(vector), 5))
    if observed != vector["expected"]:
        raise ValueError("H1 inspection observations differ from independent input")
    updated = copy.deepcopy(snapshot.materialized)
    owner = next(row for row in updated["contracts"] if row["id"] == "agent-bridge.session-host-wire")
    version = git.plugin_version_at(commit)
    if version not in owner["evidence_window"]["runtimes"]:
        owner["evidence_window"]["runtimes"].append(version)
    outputs = {}
    for source in SOURCES:
        captured = {
            "commit": commit, "plugin_version": version, "protocol_generation": 1,
            "source_path": source, "source_git_blob": git.git_blob(commit, source),
            "source_sha256": before[source],
        }
        source_row = next((row for row in owner["source_paths"] if row["path"] == source), None)
        if source_row is None:
            source_row = {"path": source}
            owner["source_paths"].append(source_row)
        source_row.update(sha256=before[source], semantic=True, non_semantic_reason=None)
        path = CURRENT + "inspection-" + Path(source).stem + "-v1.json"
        data = (json.dumps({
            "captured_from": captured, "scope": SCOPE,
            "inputs": before, "observed": observed,
        }, indent=2) + "\n").encode()
        outputs[path] = data
        fixture = next((row for row in owner["fixtures"] if row["path"] == path), None)
        if fixture is None:
            fixture = {"path": path, "role": "additive read-only inspection", "generation": 1}
            owner["fixtures"].append(fixture)
        fixture["sha256"] = git.sha256_bytes(data)
        provenance = {key: value for key, value in captured.items()
                      if key not in {"source_sha256", "protocol_generation"}}
        provenance.update(generation=1, capture_method=SCOPE)
        if provenance not in owner["provenance"]:
            owner["provenance"].append(provenance)
    outputs.update(codec.plan(ROOT, snapshot, updated))
    history: dict[str, str | None] = {
        **{path: codec.digest(safe(ROOT, path).read_bytes()) for path in before},
        **{path: codec.digest(data) for path, data in snapshot.inputs.items()},
    }
    for contract in snapshot.materialized["contracts"]:
        for row in contract["fixtures"]:
            if row["path"] not in outputs:
                data = safe(ROOT, row["path"]).read_bytes()
                if git.sha256_bytes(data) != row["sha256"]:
                    raise ValueError(f"historical fixture drift: {row['path']}")
                history[row["path"]] = codec.digest(data)

    def validate() -> None:
        if {path: git.sha256_bytes(source_bytes(commit, path)) for path in before} != before:
            raise ValueError("H1 capture inputs changed")
        for path, digest in history.items():
            if path not in outputs and codec.digest(safe(ROOT, path).read_bytes()) != digest:
                raise ValueError(f"H1 capture history changed: {path}")

    validate()
    if write:
        publish(ROOT, commit, outputs, validate=validate, input_hashes=history)
    elif any(safe(ROOT, path).read_bytes() != data for path, data in outputs.items()):
        raise ValueError("H1 capture differs; review and use --write")
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = capture(args.source_commit, write=args.write)
    print(json.dumps({"outputs": len(result), "source_commit": args.source_commit, "scope": SCOPE}))
