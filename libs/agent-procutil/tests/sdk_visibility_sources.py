"""Explicit source closure for the opt-in SDK visibility fixture."""

from __future__ import annotations

import importlib
import ast
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import uuid

from sdk_desktop_observer import ObservationBlocked


PACKAGE_FILES = (
    "__init__.py", "_async.py", "_caller_owned.py", "_daemon_instance_child.py",
    "_daemon_instance_cli.py", "_daemon_instance_clock.py", "_daemon_instance_identity.py",
    "_daemon_instance_linux.py", "_daemon_instance_native.py", "_daemon_instance_owner.py",
    "_daemon_instance_reconcile.py", "_daemon_instance_records.py",
    "_daemon_instance_shell.py", "_daemon_instance_shell_windows.py",
    "_daemon_instance_store.py", "_darwin_caller_owned.py", "_interactive_groups.py",
    "_interactive_session.py", "_interactive_terminal.py", "_interpreter.py",
    "_job_scopes.py", "_linux_pidfd.py", "_native_owned.py", "_native_query.py", "_output_relay.py",
    "_retirement_channel.py", "_retirement_custodian.py",
    "_shell_caller_owned.py", "_terminal_offer.py", "_windows_caller_owned.py",
    "_windows_parent_job.py", "_windows_job_types.py", "daemon.py", "daemon_broker.py", "daemon_instances.py",
    "diagnostics.py", "interactive.py", "job_activity.py", "linux_daemon_scope.py", "metrics.py", "metrics_accounting.py",
    "native_child.py", "owned_budget.py", "owned_process.py", "process_retirement.py", "python_cache.py", "retirement_custody.py",
    "scratch.py", "startup.py", "streaming.py",
    "windows_broker_cell.py", "windows_broker_entry.py", "windows_broker_installation.py",
    "windows_broker_native.py", "windows_broker_protocol.py", "windows_broker_retention.py",
    "windows_task_broker.py", "wsl.py",
)
PACKAGE_RESOURCES = ("windows_broker_task.ps1",)
FRAGMENT_FILES = (
    "base", "files", "source", "lock_recovery", "receipts", "snapshot",
    "runtime_slot_ownership", "runtime_slot_completion", "resolution", "guest_environment",
    "activation", "legacy_attribution", "legacy_transition", "legacy_retirement",
    "maintenance", "mode_cli", "deployment", "publication", "powershell",
)
RUNNER_FILES = (
    "sdk_visibility.py", "sdk_desktop_observer.py", "sdk_desktop_win32.py",
    "sdk_native_win32.py", "sdk_visibility_sources.py", "sdk_visibility_cases.py",
)
SDK_FILES = (
    *(f"libs/agent-procutil/tests/{name}" for name in RUNNER_FILES),
    *(f"libs/agent-procutil/src/agent_procutil/{name}" for name in PACKAGE_FILES),
    *(f"libs/agent-procutil/src/agent_procutil/{name}" for name in PACKAGE_RESOURCES),
    *(f"libs/installation-context/_installation_context_{name}.py" for name in FRAGMENT_FILES),
    "libs/installation-context/installation_context.py",
    "libs/installation-context/installation-context.ps1",
    "libs/installation-context/shell-caller-owned.ps1",
    "libs/installation-context/_installation_context_worker.py",
    "libs/installation-context/daemon_instance_runtime.pyz",
    "libs/agent-procutil/tests/test_shell_caller_owned_native.py",
    "libs/agent-procutil/tests/test_caller_owned_native.py",
    "libs/agent-procutil/tests/caller_owned_fixture.py",
)


def require_sdk_sources(sdk: Path, sources: list[dict], *, runner: Path) -> None:
    """Bind the required fixture, SDK fragments and import package to one root."""
    if not sdk.is_absolute() or sdk.resolve() != sdk:
        raise ObservationBlocked("SDK source root is missing or redirected")
    supplied = {Path(item["path"]) for item in sources}
    if len(supplied) != len(sources) or supplied != {sdk / name for name in SDK_FILES}:
        raise ObservationBlocked("SDK source closure is incomplete or bound to another root")
    helpers = sdk / "libs/agent-procutil/tests"
    if runner != helpers / "sdk_visibility.py" or runner.resolve() != runner:
        raise ObservationBlocked("running qualification runner belongs to another SDK root")
    if Path(__file__) != helpers / "sdk_visibility_sources.py":
        raise ObservationBlocked("loaded qualification helper belongs to another SDK root")
    for name in RUNNER_FILES[1:]:
        module = sys.modules.get(Path(name).stem)
        if module is not None:
            origin = getattr(module, "__file__", None)
            if not origin or Path(origin) != helpers / name or Path(origin).resolve() != Path(origin):
                raise ObservationBlocked("loaded qualification helper belongs to another SDK root")


def bind_sdk_import(sdk: Path) -> Path:
    """Select the verified source before pytest can import an ambient package."""
    if any(name == "agent_procutil" or name.startswith("agent_procutil.") for name in sys.modules):
        raise ObservationBlocked("SDK worker already imported an unbound procutil package")
    source = sdk / "libs/agent-procutil/src"
    sys.path.insert(0, str(source))
    importlib.invalidate_caches()
    package = importlib.import_module("agent_procutil")
    expected = source / "agent_procutil/__init__.py"
    if Path(package.__file__).resolve() != expected.resolve():
        raise ObservationBlocked("SDK worker imported procutil outside its verified source")
    return expected


SCOPED_PROFILE = "copilot-extensions.scoped-native-observation/v1"
SCOPED_CONSUMER = (
    "libs/agent-procutil/tests/test_caller_owned_native.py"
    "::test_scoped_observer_managed_root"
)
SCOPED_FILES = (*SDK_FILES, "libs/agent-procutil/tests/sdk_scoped_observer.py")


@dataclass(frozen=True)
class ScopedEnrollment:
    """Source integrity and exact case binding, not permission for native effects."""

    root: Path
    case: str
    epoch: str
    deadline: float
    sources: tuple[tuple[str, int, str], ...]

    def verify(self) -> str:
        if (type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
                or not 0 < self.deadline - time.monotonic() <= 180):
            raise ObservationBlocked("scoped source enrollment expired")
        filename, separator, function = self.case.partition("::")
        expected_file, _, expected_function = SCOPED_CONSUMER.partition("::")
        if (
            not separator or filename != str(self.root / expected_file)
            or function != expected_function
            or not isinstance(self.epoch, str) or len(self.epoch) != 32
            or any(c not in "0123456789abcdef" for c in self.epoch)
            or not self.root.is_absolute() or self.root.resolve() != self.root
            or Path(__file__) != self.root / "libs/agent-procutil/tests/sdk_visibility_sources.py"
        ):
            raise ObservationBlocked("scoped consumer escaped its exact source-bound case")
        expected = {str(self.root / name) for name in (*SCOPED_FILES, expected_file)}
        if len(self.sources) != len(expected) or {row[0] for row in self.sources} != expected:
            raise ObservationBlocked("scoped source closure is incomplete or rebound")
        for name, length, digest in self.sources:
            path = Path(name)
            if (
                path.resolve() != path or not path.is_file()
                or type(length) is not int or length < 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
            ):
                raise ObservationBlocked("scoped source entry is indirect or invalid")
            data = path.read_bytes()
            if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
                raise ObservationBlocked("scoped source changed after enrollment")
        try:
            declaration = ast.parse((self.root / expected_file).read_bytes())
        except (SyntaxError, ValueError) as error:
            raise ObservationBlocked("scoped SDK consumer source is invalid") from error
        if not any(isinstance(node, ast.FunctionDef) and node.name == expected_function
                   for node in declaration.body):
            raise ObservationBlocked("scoped SDK consumer case is not declared")
        for name in (Path(filename).stem for filename in (*RUNNER_FILES, "sdk_scoped_observer.py")):
            loaded = sys.modules.get(name)
            if loaded is not None and Path(getattr(loaded, "__file__", "")) != (
                self.root / "libs/agent-procutil/tests" / (name + ".py")
            ):
                raise ObservationBlocked("loaded scoped helper belongs to another source root")
        return hashlib.sha256(json.dumps(self.sources, separators=(",", ":")).encode()).hexdigest()


def enroll_scoped_consumer(
    sdk: Path, case: str, sources: list[dict], deadline: float,
) -> ScopedEnrollment:
    """Enroll only the literal SDK managed-root case through the source owner.

    The admitting native driver still owns provenance, consent and containment.
    A caller-computed file digest cannot grant any of those authorities.
    """
    if not isinstance(case, str) or not isinstance(sources, list) or any(
        not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}
        or not isinstance(row["path"], str) for row in sources
    ):
        raise ObservationBlocked("invalid scoped source enrollment")
    enrollment = ScopedEnrollment(
        sdk, case, uuid.uuid4().hex, deadline,
        tuple(sorted((row["path"], row["bytes"], row["sha256"]) for row in sources)),
    )
    enrollment.verify()
    return enrollment


def verify_scoped_root_packet(
    packet: dict, enrollment: ScopedEnrollment, identity: dict,
) -> bool:
    """Validate root-only evidence; never promote it to full consumer coverage."""
    expected = {
        "schema", "case", "epoch", "source_digest", "scope", "descendants", "console_hosts",
        "consumer_qualified", "identity", "started", "barrier", "ended", "sealed",
        "root_exit_observed", "delivery_tail",
        "root_and_job_settled", "hooks_closed", "pins_closed", "hook_count", "pumps",
        "maximum_gap", "overflow", "errors", "raw_events", "qualified", "passed",
    }
    if not isinstance(packet, dict) or set(packet) != expected or (
        packet["schema"] != SCOPED_PROFILE or packet["case"] != enrollment.case
        or packet["epoch"] != enrollment.epoch
        or packet["source_digest"] != enrollment.verify()
        or packet["scope"] != "sdk-pre-resume-root-only"
        or packet["descendants"] != "unmanaged-unsupported"
        or packet["console_hosts"] != "unverified-unsupported"
        or packet["consumer_qualified"] is not False
        or packet["identity"] != identity or not isinstance(identity, dict)
        or set(identity) != {"pid", "birth", "initial_thread"}
    ):
        raise ObservationBlocked("scoped evidence schema, source, epoch or identity differs")
    if any(type(identity[key]) is not int or not 0 < identity[key] < maximum for key, maximum in (
        ("pid", 1 << 32), ("birth", 1 << 64), ("initial_thread", 1 << 32),
    )):
        raise ObservationBlocked("scoped evidence lacks its exact native identity")
    if (
        any(packet[key] is not True for key in (
            "sealed", "root_and_job_settled", "hooks_closed", "pins_closed", "qualified",
        ))
        or packet["overflow"] is not False or packet["errors"] != []
        or type(packet["hook_count"]) is not int or packet["hook_count"] != 2
        or type(packet["pumps"]) is not int or packet["pumps"] <= 0
        or any(type(packet[key]) not in (int, float) or not math.isfinite(packet[key])
               for key in ("started", "barrier", "ended", "maximum_gap",
                           "root_exit_observed", "delivery_tail"))
        or not packet["started"] <= packet["barrier"] <= packet["root_exit_observed"]
        <= packet["ended"] < enrollment.deadline
        or not .5 <= packet["delivery_tail"] <= packet["ended"] - packet["root_exit_observed"]
        or not 0 <= packet["maximum_gap"] <= .5
    ):
        raise ObservationBlocked("scoped evidence has incomplete startup, coverage or cleanup")
    events = packet["raw_events"]
    if not isinstance(events, list) or len(events) > 4096:
        raise ObservationBlocked("scoped evidence exceeded its event bound")
    event_fields = {
        "kind", "hwnd", "pid", "birth", "thread", "generated_ms", "delay_ms", "received", "violation",
    }
    previous = packet["started"]
    for event in events:
        if (
            not isinstance(event, dict) or set(event) != event_fields
            or event["kind"] not in {"create", "destroy", "show", "hide", "foreground"}
            or event["pid"] != identity["pid"] or event["birth"] != identity["birth"]
            or any(type(event[key]) is not int or not 0 < event[key] < maximum for key, maximum in (
                ("hwnd", 1 << 64), ("thread", 1 << 32),
            ))
            or type(event["generated_ms"]) is not int or not 0 <= event["generated_ms"] < 1 << 32
            or type(event["delay_ms"]) is not int or not 0 <= event["delay_ms"] <= 500
            or type(event["received"]) not in (int, float) or not math.isfinite(event["received"])
            or not previous <= event["received"] <= packet["ended"]
            or type(event["violation"]) is not bool
            or event["kind"] in {"foreground", "show"} and event["violation"] is not True
        ):
            raise ObservationBlocked("scoped raw event escaped its identity, epoch or coverage")
        previous = event["received"]
    passed = not any(event["violation"] for event in events)
    if packet["passed"] is not passed:
        raise ObservationBlocked("scoped verdict differs from its raw root evidence")
    return passed
