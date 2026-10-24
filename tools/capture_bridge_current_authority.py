#!/usr/bin/env python3
"""Capture current Bridge authority vectors from verified committed sources."""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import importlib
import importlib.util
import json
import os
import re
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import agent_bridge_contract_git as evidence
import agent_bridge_contract_registry as registry_codec
from agent_bridge_contract_publication import publish
from agent_bridge_contract_paths import safe

REPO = Path(__file__).resolve().parents[1]
CONTRACT = "plugins/agent-bridge/contract/"
CURRENT = CONTRACT + "fixtures/session-host/current/"
REGISTRY = CONTRACT + "registry.json"
FILES = ("wrapper-authority-v1.json", "wrapper-exec.json", "wrapper-consent.json")
SOURCES = (
    "plugins/agent-bridge/src/agent_bridge/preference_attestation.py",
    "plugins/agent-bridge/src/agent_bridge/preference_exec.py",
    "plugins/agent-bridge/src/agent_bridge/session_host/preference_spawn.py",
)


def committed(commit: str, path: str) -> str:
    result = evidence.git("show", f"{commit}:{path}")
    if result.returncode:
        raise ValueError(f"committed capture input unavailable: {path}")
    return result.stdout


def unchanged(commit: str, path: str) -> None:
    source = REPO / path
    if (not source.resolve().is_relative_to(REPO.resolve())
            or any(parent.is_symlink() for parent in (source, *source.parents))):
        raise ValueError(f"linked capture source refused: {path}")
    if evidence.sha256_bytes(source.read_bytes()) != evidence.sha256_bytes(
        committed(commit, path).encode("utf-8"),
    ):
        raise ValueError(f"dirty capture source refused: {path}")


def producer_probe(a: Any, terminal: Any, seed: dict, consent: bytes) -> tuple[dict, int, bool]:
    """Run the real terminal reader/emitter against deterministic external inputs."""
    nonce = "synthetic-capture-nonce"
    space = seed["receipt"]["authority"]["space"]
    settings = {"model": "target", "effortLevel": "medium", "contextTier": "long_context"}
    bindings = {
        a.FD_ENV: "99", a.NONCE_ENV: nonce, a.DIGEST_ENV: a.component_digest(),
        a.MODE_ENV: "defaults", a.TARGET_ENV: a.target_digest(seed["selected_target"]),
        "CAPTURE_UNRELATED": "preserved",
    }
    channel = MagicMock()
    channel.__enter__.return_value = channel
    channel.recv.return_value = consent
    executed = []
    read_text = Path.read_text
    synthetic_home = Path("C:/capture-home") if os.name == "nt" else Path("/capture-home")

    def read_settings(path: Path, *args: Any, **kwargs: Any) -> str:
        if path.name == "settings.json" and path.parent.name == ".copilot":
            return json.dumps(settings)
        return read_text(path, *args, **kwargs)

    def execute(program: str, argv: list[str], environment: dict) -> None:
        if any(key in environment for key in bindings if key != "CAPTURE_UNRELATED"):
            raise ValueError("binding secret escaped terminal capture")
        if environment.get("CAPTURE_UNRELATED") != "preserved":
            raise ValueError("terminal capture lost unrelated environment")
        executed.append((program, argv, os.getpid()))

    with (
        patch.object(Path, "home", return_value=synthetic_home),
        patch.dict(os.environ, bindings, clear=True),
        patch.object(Path, "read_text", read_settings),
        patch.object(terminal.os, "getpid", return_value=321),
        patch.object(terminal, "namespace_context", return_value={
            key: space[key] for key in ("platform", "uid", "namespace")
        }),
        patch.object(terminal, "path_digest", side_effect=lambda path: (
            space["home"] if path == Path.home() else space["cwd"]
        )),
        patch.object(terminal, "process_start", return_value="99"),
        patch.object(terminal.socket, "socket", return_value=channel),
        patch.object(terminal.os, "execvpe", side_effect=execute),
    ):
        status = terminal.main(["synthetic-agent"])
    frame = json.loads(channel.sendall.call_args.args[0])
    return frame, status, executed == [("synthetic-agent", ["synthetic-agent"], 321)]


async def host_probe(a: Any, host: Any, frame: dict, expected: dict) -> tuple[list[str], bool]:
    """Observe actual Host verify-before-consent ordering without spawning a child."""
    events = []
    child = SimpleNamespace(pid=321, returncode=None)
    reader, writer = MagicMock(), MagicMock()

    def receive(_size: int) -> bytes:
        events.append("read-frame")
        return json.dumps(frame).encode() + b"\n"

    def terminate() -> None:
        child.returncode = 0

    async def wait() -> int:
        return 0

    async def spawn(*_args: Any, **_kwargs: Any) -> Any:
        events.append("spawn-private-channel")
        return child

    verify = a.verify_frame

    def verify_recorded(*args: Any) -> dict:
        events.append("verify-frame")
        return verify(*args)

    child.terminate, child.wait = terminate, wait
    reader.recv.side_effect = receive
    reader.sendall.side_effect = lambda value: events.append(
        "consent" if value == b"\x01" else "invalid-consent"
    )
    with (
        patch.object(host, "prepare_launch", return_value=([], {}, expected, reader, writer)),
        patch.object(host, "verify_frame", side_effect=verify_recorded),
        patch.object(a, "process_start", return_value="99"),
    ):
        try:
            await host.spawn_attested([], None, {}, spawn)
        except ValueError:
            if "consent" in events or child.returncode is None:
                raise ValueError("Host capture failed to refuse and reap") from None
            return events, False
    return events, True


def capture(commit: str) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not evidence.ensure_commit_available(commit):
        raise ValueError("capture requires a real locally available source commit")
    for path in (*SOURCES, "plugins/agent-bridge/plugin.json"):
        unchanged(commit, path)
    sys.path[:0] = [
        str(REPO / "plugins/agent-bridge/src"), str(REPO / "libs/agent-procutil/src"),
    ]
    a = importlib.import_module("agent_bridge.preference_attestation")
    input_paths = list(SOURCES)
    for name in a.AUTHORITY_MODULES:
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None:
            raise ValueError(f"capture module has no source: {name}")
        path = Path(spec.origin).resolve().relative_to(REPO.resolve()).as_posix()
        unchanged(commit, path)
        input_paths.append(path)
    terminal = importlib.import_module("agent_bridge.preference_exec")
    host = importlib.import_module("agent_bridge.session_host.preference_spawn")
    snapshot = registry_codec.load(
        REPO, read=lambda path: committed(commit, path).encode("utf-8"),
    )
    registry = copy.deepcopy(snapshot.materialized)
    vectors = [json.loads(committed(commit, CURRENT + name)) for name in FILES]
    authority, execution, consent = vectors
    frame, status, executed = producer_probe(a, terminal, authority, b"\x01")
    refused, refused_status, refused_exec = producer_probe(a, terminal, authority, b"")
    if status != 0 or not executed or refused_status != 78 or refused_exec:
        raise ValueError("terminal capture did not preserve consent/refusal")
    if refused != frame:
        raise ValueError("terminal refusal changed attested inputs")
    expected = {
        **frame["receipt"]["authority"]["space"], "nonce": frame["proof"]["nonce"],
        **{key: frame["receipt"]["authority"][key] for key in ("digest", "mode", "target")},
    }
    with patch.object(a, "process_start", return_value="99"):
        receipt = a.verify_frame(frame, expected, 321)
    prior_receipt = copy.deepcopy(authority["receipt"])
    prior_receipt["authority"]["digest"] = a.component_digest()
    if receipt != prior_receipt:
        raise ValueError("non-digest authority change requires separately reviewed vectors")
    environment = [a.FD_ENV, a.NONCE_ENV, a.DIGEST_ENV, a.MODE_ENV, a.TARGET_ENV]
    if environment != execution["binding_environment"] or list(a.AUTHORITY_MODULES) != consent["authority_modules"]:
        raise ValueError("authority closure/environment change requires separately reviewed vectors")
    for vector in authority["invalid_vectors"]:
        altered = copy.deepcopy(receipt)
        cursor = altered
        for key in vector["path"][:-1]:
            cursor = cursor[key]
        cursor[vector["path"][-1]] = vector["value"]
        if a.valid_receipt(altered, 321):
            raise ValueError(f"invalid authority vector admitted: {vector['path']}")
    order, accepted = asyncio.run(host_probe(a, host, frame, expected))
    bad = copy.deepcopy(frame)
    bad["proof"]["nonce"] = "incorrect"
    _, bad_accepted = asyncio.run(host_probe(a, host, bad, expected))
    if not accepted or bad_accepted or order != consent["ordering"]:
        raise ValueError("Host capture changed consent ordering")
    protocol = importlib.import_module("agent_bridge.session_host.protocol")
    generation = protocol.PROTOCOL_VERSION
    if generation != authority["captured_from"]["protocol_generation"]:
        raise ValueError("wire generation change requires separately reviewed vectors")
    if not a.SHELL_MARKER.endswith(f"wrapper-v{authority['wrapper_version']}"):
        raise ValueError("wrapper generation change requires separately reviewed vectors")
    authority.update(
        token=a.LAUNCH_TOKEN, maximum_private_frame_bytes=a.MAX_FRAME, receipt=receipt,
    )
    execution.update(
        component_digest=a.component_digest(),
        binding_environment=environment,
        consent_base64=base64.b64encode(b"\x01").decode(),
        refused_exit_code=refused_status, exec_requires_host_consent=not refused_exec,
        exec_preserves_pid=executed,
    )
    consent.update(
        component_digest=a.component_digest(), authority_modules=list(a.AUTHORITY_MODULES),
        ordering=order,
        release_metadata_normalized="agent_bridge._FALLBACK_VERSION and legacy agent_bridge.__version__",
    )
    contract = next(c for c in registry["contracts"] if c["id"] == "agent-bridge.session-host-wire")
    version = evidence.plugin_version_at(commit)
    output = {}
    for name, vector, source in zip(FILES, vectors, SOURCES, strict=True):
        metadata = {
            "commit": commit, "plugin_version": version, "protocol_generation": generation,
            "source_path": source, "source_git_blob": evidence.git_blob(commit, source),
            "source_sha256": evidence.git_file_sha256(commit, source),
        }
        vector["captured_from"] = metadata
        path = CURRENT + name
        output[path] = json.dumps(vector, indent=2) + "\n"
        next(f for f in contract["fixtures"] if f["path"] == path)["sha256"] = (
            evidence.sha256_bytes(output[path].encode())
        )
        next(s for s in contract["source_paths"] if s["path"] == source)["sha256"] = metadata["source_sha256"]
        provenance = {
            key: value for key, value in metadata.items()
            if key not in {"source_sha256", "protocol_generation"}
        }
        provenance.update(
            generation=generation,
            capture_method=(
                "Committed-source capture_bridge_current_authority.py: real terminal "
                "settings reader, receipt emitter/verifier and Host consent/refusal "
                "ordering with deterministic synthetic execution-space inputs; "
                "normalize only standalone root-module release labels."
            ),
        )
        if provenance not in contract["provenance"]:
            contract["provenance"].append(provenance)
    output.update({
        path: data.decode("utf-8")
        for path, data in registry_codec.plan(REPO, snapshot, registry).items()
    })
    for path in set(input_paths):
        unchanged(commit, path)
    return output


def output_paths(output: dict[str, str]) -> dict[str, Path]:
    """Preflight every output and ancestor before reading or writing payloads."""
    allowed = {REGISTRY, *(CURRENT + name for name in FILES)}
    if not allowed.issubset(output):
        raise ValueError("capture output set must contain only the four approved files")
    # Fixed packet ownership is unchanged; additional outputs must be verified
    # provenance members of the proposed complete index, never caller paths.
    chunks = set()
    if set(output) != allowed:
        metadata = registry_codec.load(
            REPO, read=lambda path: output[path].encode("utf-8") if path in output
            else registry_codec.read_file(REPO, path),
        )
        chunks = set(metadata.inputs) - {REGISTRY}
        if set(output) - chunks != allowed:
            raise ValueError("capture output set must contain only the four approved files and provenance")
    root = REPO.resolve(strict=True)
    paths = {}
    identities = {}
    for relative in output:
        if relative in chunks:
            paths[relative] = safe(root, relative)
            if paths[relative].exists() and paths[relative].read_bytes() != output[relative].encode():
                raise ValueError(f"immutable provenance output changed: {relative}")
            continue
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise ValueError(f"capture output escapes repository: {relative}")
        node = root
        for index, part in enumerate(("", *parts)):
            if part:
                node = node / part
            info = node.lstat()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            ):
                raise ValueError(f"linked capture output refused: {relative}")
            leaf = index == len(parts)
            if not (stat.S_ISREG(info.st_mode) if leaf else stat.S_ISDIR(info.st_mode)):
                raise ValueError(f"invalid capture output type: {relative}")
            if leaf and info.st_nlink != 1:
                raise ValueError(f"aliased capture output refused: {relative}")
            resolved = node.resolve(strict=True)
            if resolved != node or not resolved.is_relative_to(root):
                raise ValueError(f"capture output escapes repository: {relative}")
            identity = (info.st_dev, info.st_ino)
            if identity in identities and identities[identity] != node:
                raise ValueError(f"aliased capture output path refused: {relative}")
            identities[identity] = node
        paths[relative] = node
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    try:
        if args.write:
            from agent_bridge_contract_publication import recover_pending_links
            recover_pending_links(REPO, args.source_commit)
        output = capture(args.source_commit)
        def validate() -> None:
            paths = output_paths(output)
            for path, text in output.items():
                if path.startswith(registry_codec.PROVENANCE):
                    continue
                actual = paths[path].read_text(encoding="utf-8")
                if actual not in (committed(args.source_commit, path), text):
                    raise ValueError(f"dirty capture output refused: {path}")

        validate()
        if args.write:
            publish(
                REPO, args.source_commit,
                {path: text.encode("utf-8") for path, text in output.items()},
                validate=validate,
            )
        elif any(
            safe(REPO, path).read_text(encoding="utf-8") != text
            for path, text in output.items()
        ):
            raise ValueError("current capture differs; review and use --write")
    except (ValueError, OSError) as exc:
        print(f"capture-bridge-current-authority: {exc}", file=sys.stderr)
        return 1
    print(f"capture-bridge-current-authority: OK (3 current vectors; source {args.source_commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
