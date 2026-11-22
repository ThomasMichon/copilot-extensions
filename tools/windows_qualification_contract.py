"""Versioned, command-free Windows qualification requests and results."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from subprocess import CompletedProcess
from typing import Any, Callable, Protocol


class QualificationTransport(Protocol):
    """Typed boundary for the existing source-transport adapter."""

    def git(
        self, *args: str, data: str | None = None, check: bool = True,
        transport_push: bool = False,
    ) -> CompletedProcess[str]: ...

    def scoped_credentials(self, login: str) -> None: ...

    def validate_tip(self, tip: str) -> None: ...

    def observe(
        self, history: str | None, persist: Callable[[dict[str, Any]], None], *,
        observed_tip: str | None = None,
    ) -> str | None: ...

    def append(
        self, candidate: str, persist: Callable[[dict[str, Any]], None], *,
        required_history: str | None = None, observed_tip: str | None = None,
    ) -> str: ...

VERSION = 1
WORKFLOW = "windows-qualification.yml"
TRANSPORT_REF = "refs/qualification/windows-inputs"
TRANSPORT_MARKER = "windows-qualification-transport-v1"
SUPPORT_PATH = "tools/windows-qualification-support.json"
DRIVER_SERVICE_PACKET = "native-driver-service-caller-jobs"
DRIVER_SERVICE_CASES = (
    ("test_session_host_application", "test_installed_service_survives_all_installer_caller_jobs"),
    ("test_durable_wmi_job_survival",
     "test_tracked_cleanup_scope_refuses_a_child_outside_the_immediate_caller_job"),
)
DRIVER_SERVICE_FILES = (
    "tools/qualify_driver_service.py",
    "plugins/agent-remote-driver/tests/native/test_session_host_application.py",
    "plugins/agent-worktrees/tests/native/public_caller_jobs_fixture.py",
    "libs/agent-procutil/tests/test_durable_wmi_job_survival.py",
)
NATIVE_PACKETS = {
    DRIVER_SERVICE_PACKET: (
        "agent-remote-driver", *DRIVER_SERVICE_CASES[0], DRIVER_SERVICE_PACKET,
    ),
    "native-worktrees-embody": (
        "agent-worktrees", "test_native_seed_application", "test_public_embody",
        "native-worktrees-public-caller",
    ),
    "native-worktrees-copilot": (
        "agent-worktrees", "test_native_seed_application", "test_public_copilot",
        "native-worktrees-public-tty",
    ),
    "native-worktrees-caller-jobs": (
        "agent-worktrees", "test_native_seed_application", "test_public_caller_job_lifetime",
        "native-worktrees-caller-jobs",
    ),
    "native-worktrees-copilot-caller-jobs": (
        "agent-worktrees", "test_native_seed_application", "test_public_attached_caller_job_lifetime",
        "native-worktrees-copilot-caller-jobs",
    ),
    "native-worktrees-manager-no-mux": (
        "agent-worktrees", "test_manager_application_launch", "test_manager_no_mux_decision",
        "native-worktrees-manager-no-mux",
    ),
    "native-worktrees-manager-mux": (
        "agent-worktrees", "test_manager_application_launch", "test_manager_mux_decision",
        "native-worktrees-manager-mux",
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
    "native-driver-startup-seed": (
        "agent-remote-driver", "test_headed_extension",
        "test_native_headed_startup_seed_observation_precedes_controlled_send",
        "native-driver-startup-seed",
    ),
    "native-driver-startup-no-seed": (
        "agent-remote-driver", "test_headed_extension",
        "test_native_headed_startup_no_seed_is_not_an_idle_claim", "native-driver-startup-no-seed",
    ),
    "native-driver-startup-cold-resume": (
        "agent-remote-driver", "test_headed_extension",
        "test_native_headed_startup_cold_resume_preserves_history",
        "native-driver-startup-cold-resume",
    ),
}
NATIVE_LIFETIME_PACKETS = ("native-worktrees-caller-jobs", "native-worktrees-copilot-caller-jobs")
MCP_LIFETIME_PACKET = "mcp-caller-lifetime"
MCP_LIFETIME_CASE = "test_native_caller_jobs_and_session_owner_lifetime"
DESKTOP_PACKET = "desktop-capability"
DESKTOP_CASE = "test_hosted_desktop_capability"
SOURCE_PACKET = "manager-worktrees-source"
SOURCE_MODULE = "test_manager_source_replacement"
SOURCE_CASE = "test_installed_manager_updates_frozen_source"
SOURCE_FILES = (
    "tools/run-manager-suite-acceptance.py",
    "worktree-manager/tests/native/manager_source_fixture.py",
    "worktree-manager/tests/native/test_manager_source_replacement.py",
)
DESKTOP_FILES = (
    "tools/test_windows_qualification_desktop_native.py",
    "tools/windows_qualification_desktop.py",
    *(f"libs/agent-procutil/tests/{name}.py" for name in (
        "sdk_visibility", "sdk_desktop_observer", "sdk_desktop_win32",
        "sdk_native_win32", "sdk_visibility_sources", "sdk_visibility_cases",
    )),
)
PACKETS = ("bridge-http52", MCP_LIFETIME_PACKET, DESKTOP_PACKET, SOURCE_PACKET, *NATIVE_PACKETS)
NATIVE_VERSIONS = {"copilot": "1.0.105", "psmux": "3.4.0"}
SHA = re.compile(r"[0-9a-f]{40}")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
REQUEST_ID = re.compile(r"[0-9a-f]{32}")


def canonical(value: dict[str, Any]) -> str:
    """Encode the public identity without platform-dependent whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(value: str) -> str:
    """Require a full immutable Git commit identity."""
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("expected a full lowercase 40-hex commit")
    return value


def selection(value: dict[str, Any]) -> dict[str, Any]:
    """Reject omitted broad intent, shell commands and path/flag injection."""
    if not isinstance(value, dict):
        raise ValueError("selection must be an object")
    kind = value.get("kind")
    if kind == "packet":
        fields = {"kind", "name"}
        if value.get("name") == SOURCE_PACKET:
            fields.update(("main_sha", "dev_sha"))
        if set(value) != fields or value["name"] not in PACKETS:
            raise ValueError("unsupported named packet")
        if value["name"] == SOURCE_PACKET:
            sha(value["main_sha"])
            sha(value["dev_sha"])
            if value["main_sha"] == value["dev_sha"]:
                raise ValueError("source packet requires distinct Main and merged-Dev inputs")
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


def request(value: dict[str, Any]) -> dict[str, Any]:
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
    event: dict[str, Any], *, actor: str, triggering_actor: str, ref: str, ref_protected: bool,
) -> dict[str, Any]:
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


def validate_files(root: Path, selected: dict[str, Any]) -> None:
    """Resolve focused files beneath the actual candidate, never another root."""
    selection(selected)
    if selected["kind"] == "packet":
        if selected["name"] != SOURCE_PACKET:
            return
        for file in SOURCE_FILES:
            target = root / file
            if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"source packet file missing or redirected: {file}")
        return
    from plugin_test_selection import resolve_plugin_root
    base = resolve_plugin_root(root, selected["plugin"]) if selected["kind"] == "plugin" else root
    for file in selected["files"]:
        target = base / file
        if not target.is_file() or not target.resolve().is_relative_to(base.resolve()):
            raise ValueError(f"test file missing or redirected: {file}")


def validate_result(
    value: dict[str, Any], expected: dict[str, Any], *,
    run_id: int, control_sha: str, run_attempt: int,
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
    lifetime = expected["selection"].get("name") == MCP_LIFETIME_PACKET
    desktop = expected["selection"].get("name") == DESKTOP_PACKET
    source = expected["selection"].get("name") == SOURCE_PACKET
    if value["outcome"] == "qualified" and (
        expected["selection"]["kind"] != "packet" or native or lifetime or desktop or source
    ):
        if not cases or any(case.get("outcome") != "passed" for case in cases):
            raise ValueError("empty, failed or skipped selection cannot qualify")
        if native:
            identities = sorted(
                (case.get("class", "").split(".")[-1], case.get("name"))
                for case in cases
            )
            required_cases = (
                DRIVER_SERVICE_CASES if expected["selection"]["name"] == DRIVER_SERVICE_PACKET
                else ((native[1], native[2]),)
            )
            if identities != sorted(required_cases) or value.get("native_prerequisites") != NATIVE_VERSIONS:
                raise ValueError("native caller or prerequisite identity mismatch")
        if lifetime and (
            len(cases) != 1 or cases[0].get("name") != MCP_LIFETIME_CASE
            or cases[0].get("class", "").split(".")[-1] != "test_custody_native_lifetime"
        ):
            raise ValueError("MCP native lifetime caller identity mismatch")
        if desktop:
            if (
                len(cases) != 1 or cases[0].get("name") != DESKTOP_CASE
                or cases[0].get("class", "").split(".")[-1] != "test_windows_qualification_desktop_native"
            ):
                raise ValueError("desktop capability caller identity mismatch")
            from windows_qualification_desktop import validate_evidence as validate_desktop_evidence
            validate_desktop_evidence(value.get("desktop", {}), expected["request_id"])
        if source:
            if (
                len(cases) != 1 or cases[0].get("name") != SOURCE_CASE
                or cases[0].get("class", "").split(".")[-1] != SOURCE_MODULE
                or value.get("manager_source", {}).get("runner_sha") != expected["source_sha"]
            ):
                raise ValueError("source application caller identity mismatch")
            from windows_qualification_manager_source import validate_evidence as validate_source_evidence
            validate_source_evidence(value.get("manager_source", {}), expected["selection"])
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
        elif lifetime:
            required = {
                "source-and-selection", "prepare-mcp-test-runtime",
                MCP_LIFETIME_PACKET, "test-runtime",
            }
        elif desktop:
            required = {"source-and-selection", DESKTOP_PACKET, "test-runtime"}
        elif source:
            required = {"source-and-selection", "governed-feed", SOURCE_PACKET, "test-runtime"}
        phases = value.get("phases", [])
        if {phase.get("name") for phase in phases} != required or any(
            phase.get("status") != "passed" for phase in phases
        ):
            raise ValueError("required qualification phases are incomplete")
        if expected["selection"]["kind"] == "packet" and not native and not lifetime and not desktop and not source and (
            value.get("packet", {}).get("source_commit") != expected["source_sha"]
            or value.get("packet", {}).get("generation") != 52
            or not value.get("packet", {}).get("case_count")
        ):
            raise ValueError("canonical packet evidence is missing or foreign")
