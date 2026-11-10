"""Versioned, command-free Windows qualification requests and results."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

VERSION = 1
WORKFLOW = "windows-qualification.yml"
TRANSPORT_REF = "refs/qualification/windows-inputs"
TRANSPORT_MARKER = "windows-qualification-transport-v1"
SUPPORT_PATH = "tools/windows-qualification-support.json"
NATIVE_PACKETS = {
    "native-worktrees-embody": (
        "agent-worktrees", "test_native_seed_application", "test_public_embody",
        "native-worktrees-public-caller",
    ),
    "native-driver-auth-wait": (
        "agent-remote-driver", "test_native_headed_auth_wait",
        "test_opt_in_receiver_auth_wait_and_same_session_resume", "native-driver-auth-wait",
    ),
    "native-driver-inflight": (
        "agent-remote-driver", "test_native_headed_auth_wait",
        "test_opt_in_receiver_inflight_expiry_preserves_hold", "native-driver-inflight",
    ),
    "native-driver-observation-loss": (
        "agent-remote-driver", "test_native_headed_auth_wait",
        "test_opt_in_receiver_sdk_observation_loss_preserves_hold", "native-driver-observation-loss",
    ),
}
PACKETS = ("bridge-http52", *NATIVE_PACKETS)
NATIVE_VERSIONS = {"copilot": "1.0.105", "psmux": "3.3.8"}
SHA = re.compile(r"[0-9a-f]{40}")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REQUEST_ID = re.compile(r"[0-9a-f]{32}")


def canonical(value: dict) -> str:
    """Encode the public identity without platform-dependent whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(value: str) -> str:
    """Require a full immutable Git commit identity."""
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("expected a full lowercase 40-hex commit")
    return value


def selection(value: dict) -> dict:
    """Reject omitted broad intent, shell commands and path/flag injection."""
    if not isinstance(value, dict):
        raise ValueError("selection must be an object")
    kind = value.get("kind")
    if kind == "packet":
        if set(value) != {"kind", "name"} or value["name"] not in PACKETS:
            raise ValueError("unsupported named packet")
        return value
    expected = {"kind", "files", "k", "full", "explicit_tiers"}
    if kind == "plugin":
        expected.add("plugin")
        if not isinstance(value.get("plugin"), str) or not NAME.fullmatch(value["plugin"]):
            raise ValueError("invalid plugin name")
    elif kind != "tools":
        raise ValueError("unsupported selection kind")
    if set(value) != expected:
        raise ValueError("unexpected selection fields")
    if type(value["full"]) is not bool or type(value["explicit_tiers"]) is not bool:
        raise ValueError("full and explicit_tiers must be booleans")
    files = value["files"]
    if not isinstance(files, list) or len(files) > 40 or len(set(map(str, files))) != len(files):
        raise ValueError("expected at most 40 unique test files")
    if bool(files) == value["full"]:
        raise ValueError("choose focused files OR explicit full suite")
    for file in files:
        if not isinstance(file, str) or "\\" in file or ":" in file:
            raise ValueError("test files use repository-relative POSIX paths")
        path = PurePosixPath(file)
        prefix = "tests/" if kind == "plugin" else "tools/"
        if (
            not file.startswith(prefix) or path.is_absolute()
            or any(part in {"", ".", ".."} for part in file.split("/"))
            or not path.name.startswith("test_") or path.suffix != ".py"
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in path.parts)
        ):
            raise ValueError("unsafe test file")
    if (
        not isinstance(value["k"], str) or len(value["k"]) > 500
        or any(ord(char) < 32 or ord(char) > 126 for char in value["k"])
    ):
        raise ValueError("invalid -k expression")
    return value


def request(value: dict) -> dict:
    """Validate a request identically at submission and protected admission."""
    if not isinstance(value, dict) or set(value) != {
        "version", "request_id", "source_sha", "selection", "envelope_sha",
    }:
        raise ValueError("unexpected request fields")
    if type(value["version"]) is not int or value["version"] != VERSION:
        raise ValueError("unsupported request contract")
    if not isinstance(value["request_id"], str) or not REQUEST_ID.fullmatch(value["request_id"]):
        raise ValueError("invalid request ID")
    sha(value["source_sha"])
    if value["envelope_sha"] is not None:
        sha(value["envelope_sha"])
    selection(value["selection"])
    if len(canonical(value)) > 12000:
        raise ValueError("request exceeds input bound")
    return value


def admit(
    event: dict, *, actor: str, triggering_actor: str, ref: str, ref_protected: bool,
) -> dict:
    """Admit only the individual repository owner on the protected controller."""
    repo = event["repository"]
    owner = repo["owner"]
    if (
        owner.get("type") != "User" or not owner.get("login")
        or actor.lower() != owner["login"].lower()
        or triggering_actor.lower() != owner["login"].lower()
        or ref != "refs/heads/" + repo["default_branch"]
        or not ref_protected
    ):
        raise ValueError("owner-only protected-control admission denied")
    inputs = event["inputs"]
    value = request(json.loads(inputs["request"]))
    if inputs["request_id"] != value["request_id"]:
        raise ValueError("request title identity mismatch")
    return value


def validate_files(root: Path, selected: dict) -> None:
    """Resolve focused files beneath the actual candidate, never another root."""
    selection(selected)
    if selected["kind"] == "packet":
        return
    from plugin_test_selection import resolve_plugin_root
    base = resolve_plugin_root(root, selected["plugin"]) if selected["kind"] == "plugin" else root
    for file in selected["files"]:
        target = base / file
        if not target.is_file() or not target.resolve().is_relative_to(base.resolve()):
            raise ValueError(f"test file missing or redirected: {file}")


def validate_result(
    value: dict, expected: dict, *, run_id: int, control_sha: str, run_attempt: int,
) -> None:
    """Bind downloaded evidence to this request, source, control and run."""
    if (
        value.get("version") != VERSION or value.get("request") != expected
        or value.get("run_id") != run_id or value.get("control_sha") != control_sha
        or value.get("run_attempt") != run_attempt
        or value.get("actual_source_sha") != expected["source_sha"]
        or value.get("platform", {}).get("system") != "Windows"
        or value.get("outcome") not in {"qualified", "failed", "incomplete"}
    ):
        raise ValueError("result scope or identity mismatch")
    cases = value.get("tests")
    if not isinstance(cases, list):
        raise ValueError("missing actual test outcomes")
    native = NATIVE_PACKETS.get(expected["selection"].get("name"))
    if value["outcome"] == "qualified" and (expected["selection"]["kind"] != "packet" or native):
        if not cases or any(case.get("outcome") != "passed" for case in cases):
            raise ValueError("empty, failed or skipped selection cannot qualify")
        if native and (
            len(cases) != 1 or cases[0].get("name") != native[2]
            or cases[0].get("class", "").split(".")[-1] != native[1]
            or value.get("native_prerequisites") != NATIVE_VERSIONS
        ):
            raise ValueError("native caller or prerequisite identity mismatch")
    if value["outcome"] == "qualified":
        required = {
            "plugin": {"source-and-selection", "canonical-plugin-tests", "test-runtime"},
            "tools": {"source-and-selection", "contained-tool-tests"},
            "packet": {
                "source-and-selection", "bridge-http52-canonical-capture",
                "bridge-http52-independent-no-write",
                "bridge-contract-independent-verification", "bridge-http52-artifact-export",
                "test-runtime",
            },
        }[expected["selection"]["kind"]]
        if native:
            required = {
                "source-and-selection", "native-prerequisites",
                native[3], "test-runtime",
            }
        phases = value.get("phases", [])
        if {phase.get("name") for phase in phases} != required or any(
            phase.get("status") != "passed" for phase in phases
        ):
            raise ValueError("required qualification phases are incomplete")
        if expected["selection"]["kind"] == "packet" and not native and (
            value.get("packet", {}).get("source_commit") != expected["source_sha"]
            or value.get("packet", {}).get("generation") != 52
            or not value.get("packet", {}).get("case_count")
        ):
            raise ValueError("canonical packet evidence is missing or foreign")
