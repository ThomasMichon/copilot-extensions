"""Local opt-in and reversible native registration under installation custody."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
from typing import Any, Callable, Literal, cast
from xml.etree import ElementTree as ET

from . import _peer_launch, health_jobs, health_run, health_slots as slots

SCHEMA = "agent-machines.health.enrollment"
STATE = health_jobs.ENROLLMENT_STATE
REFERENCE = "agent-machines/health-job"
MAX_STATE = 64 * 1024
Document = dict[str, Any]


def settle_native_cleanup(owner: Any, error: BaseException | None = None) -> bool:
    """Keep retained native handles through one bounded boundary settlement."""
    from agent_procutil.owned_process import OwnedCleanupError

    retained = getattr(owner, "native_cleanup_error", None) if owner is not None else None
    errors = [retained] if retained is not None else []
    if isinstance(error, OwnedCleanupError) and error not in errors:
        errors.append(error)
    if not errors:
        return False
    deadline = time.monotonic() + 10
    if owner is not None:
        owner.native_cleanup_deadline = deadline
    for pending in errors:
        pending.retry_cleanup(deadline=deadline)
    if retained is not None:
        owner.native_cleanup_error = None
    return True


@dataclass(frozen=True)
class Installation:
    root: Path
    slot: Path
    python: Path
    gui: str
    identity: str
    runtime: Any
    platform: Literal["win32", "linux"] = "win32"

    def definition(
        self, desired: Document, owner: str, *, enabled: bool,
    ) -> str | health_jobs.LinuxDefinition:
        if self.platform == "linux":
            return health_jobs.installed_linux_definition(
                slot=str(self.slot), python=str(self.python), identity=self.identity,
                store=desired["store"], policy_token=desired["policy_token"],
                account_uid=desired["account_uid"], owner=owner, enabled=enabled,
            )
        return health_jobs.installed_definition(
            slot=str(self.slot), python=str(self.python), gui_python=self.gui,
            identity=self.identity, store=desired["store"], policy_token=desired["policy_token"],
            user_id=desired["user_id"], windows_root=os.environ["SystemRoot"],
            owner=owner, enabled=enabled,
        )


def select(
    root: Path | None = None, version: str | None = None, *,
    executing: bool = False, for_collection: bool = True,
) -> Installation:
    """Resolve an already installed immutable slot; never install or search PATH."""
    from agent_procutil import windowless_python

    if sys.platform not in {"win32", "linux"}:
        raise slots.HealthError("native-health-job-unsupported")
    environment = Path(sys.prefix).resolve()
    executing_slot = environment.parent if environment.name == ".venv" else environment
    root = root or executing_slot.parent.parent
    if root != executing_slot.parent.parent or executing_slot.parent.name != "versions":
        raise slots.HealthError("installed-job-runtime-required")
    helper = executing_slot / ".runtime-bootstrap" / "versioned_runtime.py"
    runtime = _peer_launch._load_primitive(helper, "_health_job_retention")
    version = version or (executing_slot.name if executing else runtime.current_version(root))
    if not version or version == "dev" or Path(version).name != version:
        raise slots.HealthError("immutable-job-runtime-required")
    slot = root / "versions" / version
    python = runtime.slot_python(root, version)
    if python is None:
        raise slots.HealthError("installed-job-runtime-unavailable")
    identity = _peer_launch.retained_runtime_identity(root, python, "agent_machines")
    gui = ""
    if for_collection:
        launcher = _peer_launch._load_primitive(
            slot / ".runtime-bootstrap" / "runtime_launcher.py", "_health_job_bootstrap",
        )
        if sys.platform == "win32":
            if getattr(launcher, "GUI_BOOTSTRAP_VERSION", None) != 1:
                raise slots.HealthError("installed-job-bootstrap-unsupported")
            require_collection_support(python)
            gui = windowless_python(python, require_windowless=True)
            require_gui_host(Path(gui))
        else:
            from .health_job_support import require_collection_support as require_linux_support

            _peer_launch.retained_runtime_prefix(
                root, python, "agent_machines", runtime_identity=identity,
            )
            require_linux_support(python)
    return Installation(root, slot, python, gui, identity, runtime, cast(Literal["win32", "linux"], sys.platform))


def require_gui_host(path: Path) -> None:
    with path.open("rb") as stream:
        dos = stream.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            raise slots.HealthError("installed-job-gui-host-required")
        offset = struct.unpack_from("<I", dos, 60)[0]
        if not 0 < offset < 1024 * 1024:
            raise slots.HealthError("installed-job-gui-host-required")
        stream.seek(offset)
        header = stream.read(94)
    if (
        len(header) != 94 or header[:4] != b"PE\0\0"
        or struct.unpack_from("<H", header, 24)[0] not in {0x10B, 0x20B}
        or struct.unpack_from("<H", header, 92)[0] != 2
    ):
        raise slots.HealthError("installed-job-gui-host-required")


def require_collection_support(python: Path) -> None:
    declaration, _ = slots._read(
        python.parent.parent / "Lib" / "site-packages" / "agent_machines" / "health-collection-v1.json",
        lambda value: value, maximum=4096,
    )
    if (
        type(declaration) is not dict
        or declaration.get("schema") != "agent-machines.health.collection"
        or type(declaration.get("version")) is not int or declaration["version"] != 1
        or type(declaration.get("operations")) is not list
        or not all(type(item) is str for item in declaration["operations"])
        or not {"preview", "apply", "installed-job"}.issubset(declaration["operations"])
    ):
        raise slots.HealthError("installed-job-collection-unsupported")


def validate_desired(value: Document) -> Document:
    if (
        type(value) is not dict or set(value) != {"store", "policy_token", "user_id", "enabled"}
        or type(value["store"]) is not str or not Path(value["store"]).is_absolute()
        or ".." in Path(value["store"]).parts
        or type(value["policy_token"]) is not str
        or re.fullmatch(r"[a-f0-9]{64}", value["policy_token"]) is None
        or type(value["user_id"]) is not str
        or re.fullmatch(r"S-1-(?:5-21|12-1)(?:-[0-9]+){4}", value["user_id"]) is None
        or type(value["enabled"]) is not bool
    ):
        raise slots.HealthError("invalid-local-job-enrollment")
    return value


def _record(value: Document | None) -> None:
    if value is None:
        return
    if (
        type(value) is not dict or set(value) != {"desired", "version", "identity", "definition"}
        or type(value["version"]) is not str or Path(value["version"]).name != value["version"]
        or value["version"] in {"", ".", "..", "dev"}
        or re.fullmatch(r"[a-f0-9]{32}:[a-f0-9]{64}", str(value["identity"])) is None
        or type(value["definition"]) is not str or len(value["definition"]) > 16000
    ):
        raise slots.HealthError("invalid-local-job-state")
    validate_desired(value["desired"])
    signature(value["definition"])


def _state(value: Document) -> Document:
    if (
        type(value) is not dict or set(value) != {"schema", "version", "desired", "confirmed", "pending"}
        or value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != 1
    ):
        raise slots.HealthError("invalid-local-job-state")
    if value["desired"] is not None:
        validate_desired(value["desired"])
    _record(value["confirmed"])
    if value["confirmed"] is not None and value["confirmed"]["desired"] != value["desired"]:
        raise slots.HealthError("invalid-local-job-state")
    pending = value["pending"]
    if pending is not None:
        if (
            type(pending) is not dict or set(pending) != {
                "before", "candidate", "token", "references", "intent", "native_unsettled",
            }
            or pending["intent"] not in {"replace", "disable", "remove"}
            or type(pending["native_unsettled"]) is not bool
            or re.fullmatch(r"[a-f0-9]{64}", str(pending["token"])) is None
            or type(pending["references"]) is not list or len(pending["references"]) > 8
        ):
            raise slots.HealthError("invalid-local-job-recovery")
        _record(pending["before"])
        _record(pending["candidate"])
    return value


def empty() -> Document:
    return {"schema": SCHEMA, "version": 1, "desired": None, "confirmed": None, "pending": None}


def signature(xml: str) -> dict[str, str]:
    """Compare controlled native semantics, allowing scheduler-added defaults."""
    if len(xml) > 16000 or "<!" in xml:
        raise slots.HealthError("invalid-native-job-definition")
    root = ET.fromstring(xml)
    ns = {"t": health_jobs.NS}
    for path in ("t:Actions/t:Exec", "t:Triggers/t:TimeTrigger", "t:Principals/t:Principal"):
        if len(root.findall(path, ns)) != 1:
            raise slots.HealthError("invalid-native-job-definition")
    for path in ("t:Actions", "t:Triggers", "t:Principals"):
        parent = root.find(path, ns)
        if parent is None or len(list(parent)) != 1:
            raise slots.HealthError("invalid-native-job-definition")
    paths = (
        "RegistrationInfo/Description", "Principals/Principal/UserId",
        "Principals/Principal/LogonType", "Principals/Principal/RunLevel",
        "Triggers/TimeTrigger/Repetition/Interval", "Triggers/TimeTrigger/Repetition/StopAtDurationEnd",
        "Triggers/TimeTrigger/StartBoundary", "Triggers/TimeTrigger/Enabled",
        "Settings/MultipleInstancesPolicy", "Settings/DisallowStartIfOnBatteries",
        "Settings/StopIfGoingOnBatteries", "Settings/StartWhenAvailable",
        "Settings/RunOnlyIfNetworkAvailable", "Settings/Enabled", "Settings/WakeToRun",
        "Settings/ExecutionTimeLimit", "Actions/Exec/Command", "Actions/Exec/Arguments",
        "Actions/Exec/WorkingDirectory",
    )
    defaults = {
        "Principals/Principal/RunLevel": "LeastPrivilege",
        "Triggers/TimeTrigger/Enabled": "true",
        "Triggers/TimeTrigger/Repetition/StopAtDurationEnd": "false",
        "Settings/MultipleInstancesPolicy": "IgnoreNew",
        "Settings/RunOnlyIfNetworkAvailable": "false",
        "Settings/Enabled": "true", "Settings/WakeToRun": "false",
        "Settings/RunOnlyIfIdle": "false", "Settings/AllowHardTerminate": "true",
        "Settings/AllowStartOnDemand": "true", "Settings/Hidden": "false",
        "Settings/UseUnifiedSchedulingEngine": "false",
        "Settings/DisallowStartOnRemoteAppSession": "false", "Settings/Volatile": "false",
        "Settings/Priority": "7", "Triggers/TimeTrigger/EndBoundary": "",
        "Triggers/TimeTrigger/RandomDelay": "PT0S",
        "Triggers/TimeTrigger/Repetition/Duration": "",
        "Settings/DeleteExpiredTaskAfter": "",
    }
    for path in (
        "Settings/RestartOnFailure", "Settings/MaintenanceSettings",
        "Principals/Principal/RequiredPrivileges", "Triggers/TimeTrigger/ExecutionTimeLimit",
    ):
        if root.find("/".join("t:" + part for part in path.split("/")), ns) is not None:
            raise slots.HealthError("invalid-native-job-definition")
    result: dict[str, str] = {}
    for path in dict.fromkeys((*paths, *defaults)):
        nodes = root.findall("/".join("t:" + part for part in path.split("/")), ns)
        if not nodes and path in defaults:
            result[path] = defaults[path]
            continue
        if len(nodes) != 1 or nodes[0].text is None:
            raise slots.HealthError("invalid-native-job-definition")
        result[path] = nodes[0].text
        if path in {"Triggers/TimeTrigger/StartBoundary", "Triggers/TimeTrigger/EndBoundary"} and result[path]:
            value = datetime.fromisoformat(result[path].replace("Z", "+00:00"))
            if value.tzinfo is None or value.utcoffset() is None:
                raise slots.HealthError("invalid-native-job-boundary")
            result[path] = value.astimezone(timezone.utc).isoformat()
    return result


def disabled(xml: str) -> str:
    root = ET.fromstring(xml)
    settings = root.find(f"{{{health_jobs.NS}}}Settings")
    if settings is None:
        raise slots.HealthError("invalid-native-job-definition")
    node = settings.find(f"{{{health_jobs.NS}}}Enabled")
    if node is None:
        node = ET.SubElement(settings, f"{{{health_jobs.NS}}}Enabled")
    node.text = "false"
    return ET.tostring(root, encoding="unicode")


class Enrollment:
    platform: str = "win32"

    def __init__(
        self, installation: Installation, *, backend: Any = None,
        selector: Callable[..., Any] = select, qualifier: Callable[..., None] | None = None,
    ) -> None:
        from .health_task_native import NativeTask

        self.installation = installation
        self.root = installation.root
        self.runtime = installation.runtime
        self.platform = getattr(installation, "platform", "win32")
        if self.platform not in {"win32", "linux"}:
            raise slots.HealthError("native-health-job-unsupported")
        self.account_uid = local_uid() if self.platform == "linux" else None
        self.leases = self.runtime._runtime_component("versioned_runtime_leases")
        self.path = self.root / STATE
        info = self.root.stat()
        if not info.st_ino:
            raise slots.HealthError("installed-job-root-identity-unavailable")
        identity = hashlib.sha256(f"{info.st_dev}:{info.st_ino}".encode("ascii")).hexdigest()
        self.owner = "urn:copilot-extensions:health-job:" + identity
        if backend is None and self.platform == "linux":
            from .health_systemd_native import NativePair

            backend = NativePair("ce-health-" + identity[:32], self.owner)
        self.backend = backend or NativeTask("ce-health-" + identity[:32], self.owner)
        self.selector = selector
        self.qualifier = qualifier or self._qualify
        self.native_cleanup_error: Any = None
        self.native_cleanup_deadline: float | None = None

    def _validate_desired(self, desired: Document) -> Document:
        if self.platform == "win32":
            return validate_desired(desired)
        from .health_enrollment_state import linux_desired

        return cast(Document, linux_desired(desired, account_uid=cast(int, self.account_uid)))

    def _validate_record(self, record: Document | None) -> None:
        if self.platform == "win32":
            _record(record)
        elif record is not None:
            from .health_enrollment_state import linux_record

            linux_record(record, account_uid=cast(int, self.account_uid))

    def _validate_state(self, value: Document) -> Document:
        if self.platform == "win32":
            return _state(value)
        from .health_enrollment_state import validate_linux

        return cast(Document, validate_linux(value, account_uid=cast(int, self.account_uid)))

    def _signature(self, definition: Any) -> Document:
        if self.platform == "win32":
            return cast(Document, signature(definition))
        if type(definition) is not dict:
            raise slots.HealthError("invalid-native-job-definition")
        return cast(Document, definition)

    def _disabled(self, definition: Any) -> Any:
        if self.platform == "win32":
            return disabled(definition)
        return {**self._signature(definition), "enabled": False}

    def _definition_owner(self, definition: Any) -> str:
        key = "RegistrationInfo/Description" if self.platform == "win32" else "owner"
        return cast(str, self._signature(definition)[key])

    def _native_read(self) -> Document:
        return self._normalize(self.backend.call("read"))

    def _normalize(self, native: Document) -> Document:
        return {**native, "definition": native.get("xml")} if self.platform == "win32" else native

    def read(self) -> Document:
        reader = cast(Callable[..., tuple[Document | None, Any]], slots._read)
        state, _ = reader(self.path, self._validate_state, optional=True, maximum=MAX_STATE)
        if state is not None:
            return state
        if self.platform == "linux":
            from .health_enrollment_state import empty_linux

            return cast(Document, empty_linux())
        return empty()

    def status(self) -> Document:
        state = self.read()
        return self._result("observed", self._native_read(), state, changed=False)

    def disable(self) -> Document:
        with self.leases.reference_transaction_custody(self.root, timeout=5):
            state = self.read()
            native = self._native_read()
            if state["pending"] is not None:
                return self._recover(state, native, disable=True)
            self._owned(native, state["confirmed"])
            self._recover_reference_fence(state)
            if state["desired"] is None:
                return self._result("not-adopted", native, state, changed=False)
            if not native["configured"]:
                raise slots.HealthError("owned-health-task-missing")
            if not native["enabled"] and not state["desired"]["enabled"] and not native["instances"]:
                return self._result("disabled", native, state, changed=False)
            record = {
                **state["confirmed"], "desired": {**state["desired"], "enabled": False},
                "definition": self._disabled(state["confirmed"]["definition"]),
            }
            transaction = self.leases.begin_runtime_reference_transaction(self.root, REFERENCE)
            working = {**state, "pending": {
                "before": state["confirmed"], "candidate": record,
                "token": transaction["token"], "references": transaction["owners"].get(REFERENCE, []),
                "intent": "disable",
                "native_unsettled": False,
            }}
            slots._write(self.path, working)
            try:
                native = self._native_call("disable", expected=native["digest"])
                if (
                    native["enabled"] or native["instances"]
                    or self._signature(native["definition"]) != self._signature(record["definition"])
                ):
                    raise slots.HealthError("native-health-task-settlement-unconfirmed")
                return self._settle(working, native, record, status="disabled")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return self._result("unknown", native, self.read(), changed=None, rollback="unconfirmed")

    def _result(
        self, status: str, native: Document, state: Document, *,
        changed: bool | None, rollback: str = "not-needed",
    ) -> Document:
        return {
            "schema": SCHEMA, "version": state["version"], "status": status, "changed": changed,
            **({"platform": "linux"} if self.platform == "linux" else {}),
            "adopted": state["desired"] is not None, "configured": native["configured"],
            "enabled": native["enabled"], "instances": native["instances"],
            "state": native.get("state"), "last_result": native.get("last_result"),
            "last_run": native.get("last_run"), "next_run": native.get("next_run"),
            "recovery_required": state["pending"] is not None, "rollback": rollback,
            "native_cleanup_unconfirmed": bool(
                state["pending"] and state["pending"]["native_unsettled"]
            ),
            "candidate_readiness": (
                "ready" if status in {"converged", "unchanged", "recovered-candidate"}
                else "rejected" if status == "rolled-back"
                else "unknown" if status == "unknown" else "not-requested"
            ),
            "carrier_publication": "not-requested",
        }

    def _qualify(self, installation: Installation, desired: Document) -> None:
        from agent_procutil.owned_process import run_caller_owned

        prefix = _peer_launch.retained_runtime_prefix(
            installation.root, installation.python, "agent_machines", runtime_identity=installation.identity,
        )
        response = run_caller_owned(
            [*prefix, "health", "run", "--store-dir", desired["store"]],
            timeout=10, max_stdout_bytes=64 * 1024, max_stderr_bytes=4096,
        )
        if response.returncode != 0:
            raise slots.HealthError("installed-job-readiness-failed")
        value = json.loads(response.stdout)
        if value.get("status") != "preview" or value.get("policy_token") != desired["policy_token"]:
            raise slots.HealthError("installed-job-policy-changed")

    def _qualify_record(self, record: Document, native: Document) -> None:
        installation = self.selector(self.root, record["version"])
        if installation.identity != record["identity"]:
            raise slots.HealthError("health-task-recovery-build-changed")
        expected = installation.definition(
            record["desired"], self.owner, enabled=record["desired"]["enabled"],
        )
        if self._signature(record["definition"]) != self._signature(expected):
            raise slots.HealthError("health-task-recovery-closure-changed")
        self._policy(record["desired"], native)
        self.qualifier(installation, record["desired"])

    def _policy(self, desired: Document, native: Document) -> None:
        self._validate_desired(desired)
        account = "account_sid" if self.platform == "win32" else "account_uid"
        wanted = "user_id" if self.platform == "win32" else "account_uid"
        if native[account] != desired[wanted]:
            raise slots.HealthError("interactive-job-account-mismatch")
        if health_run.policy_preview(Path(desired["store"]))[2] != desired["policy_token"]:
            raise slots.HealthError("installed-job-policy-changed")

    def _owned(self, native: Document, record: Document | None) -> None:
        native = self._normalize(native)
        if record is not None and self._definition_owner(record["definition"]) != self.owner:
            raise slots.HealthError("foreign-or-drifted-health-task")
        if not native["configured"]:
            return
        if record is None or self._signature(native["definition"]) != self._signature(record["definition"]):
            raise slots.HealthError("foreign-or-drifted-health-task")
        if self._definition_owner(native["definition"]) != self.owner:
            raise slots.HealthError("foreign-or-drifted-health-task")

    def _pending_owner(self, native: Document, pending: Document) -> Document | None:
        native = self._normalize(native)
        if not native["configured"]:
            return None
        actual = self._signature(native["definition"])
        for record in (pending["candidate"], pending["before"]):
            if record and actual in (
                self._signature(record["definition"]), self._signature(self._disabled(record["definition"])),
            ):
                if self._definition_owner(actual if self.platform == "linux" else native["definition"]) != self.owner:
                    break
                return cast(Document, record)
        if self.platform == "linux":
            return self._partial_pair_owner(actual, pending)
        raise slots.HealthError("health-task-recovery-conflict")

    def _partial_pair_owner(self, actual: Document, pending: Document) -> Document | None:
        if actual.get("owner") != self.owner or actual.get("account_uid") != self.account_uid:
            raise slots.HealthError("health-task-recovery-conflict")
        records = [record for record in (pending["candidate"], pending["before"]) if record]
        artifacts = actual.get("artifacts")
        if type(artifacts) is not dict or len(artifacts) > 2:
            raise slots.HealthError("health-task-recovery-conflict")
        for name, text in artifacts.items():
            if not any(record["definition"]["artifacts"].get(name) == text for record in records):
                raise slots.HealthError("health-task-recovery-conflict")
        return None

    def configure(self, desired: Document, *, apply: bool = False, expected: str = "") -> Document:
        self._validate_desired(desired)
        if expected and not apply:
            raise slots.HealthError("confirmation-requires-apply")
        state = self.read()
        native = self._native_read()
        if state["pending"] is not None:
            self._pending_owner(native, state["pending"])
        else:
            self._owned(native, state["confirmed"])
        self._policy(desired, native)
        self.selector(self.root)
        token = slots._digest({
            "installation": self.owner, "desired": desired, "state": state, "task": native["digest"],
        })
        if not apply:
            return {**self._result("preview", native, state, changed=False), "preview_token": token}
        if not expected or not hmac.compare_digest(token, expected):
            raise slots.HealthError("enrollment-preview-changed")
        result = self.converge(desired=desired, expected_state=state, expected_native=native["digest"])
        result["configuration_applied"] = (
            None if result["recovery_required"] else self.read()["desired"] == desired
        )
        if result["configuration_applied"] is False and result["status"] not in {"unknown", "rolled-back"}:
            result["status"] = "configuration-not-applied"
            result["candidate_readiness"] = "rejected"
        return result

    def converge(
        self, *, desired: Document | None = None, expected_state: Document | None = None,
        expected_native: str | None = None, remove: bool = False,
    ) -> Document:
        from agent_procutil.owned_process import OwnedCleanupError

        with self.leases.reference_transaction_custody(self.root, timeout=5), ExitStack() as retained:
            state = self.read()
            native = self._native_read()
            if expected_state is not None and (state != expected_state or native["digest"] != expected_native):
                raise slots.HealthError("enrollment-preview-changed")
            if state["pending"] is not None:
                return self._recover(
                    state, native, remove=remove,
                    disable=desired is not None and not desired["enabled"],
                )
            self._owned(native, state["confirmed"])
            self._recover_reference_fence(state)
            desired = desired if desired is not None else state["desired"]
            if desired is None and not remove:
                return self._result("not-adopted", native, state, changed=False)
            if (
                remove and desired is None and state["confirmed"] is None
                and not native["configured"]
                and not self.leases.read_runtime_references(self.root).get(REFERENCE, [])
            ):
                return self._result("removed", native, state, changed=False)
            candidate = None
            if not remove:
                self._policy(desired, native)
                installation = self.selector(self.root)
                retained.enter_context(self.runtime.retention_lease(self.root, installation.slot.name))
                self.qualifier(installation, desired)
                candidate = {
                    "desired": desired, "version": installation.slot.name, "identity": installation.identity,
                    "definition": installation.definition(desired, self.owner, enabled=desired["enabled"]),
                }
                self._validate_record(candidate)
                if (
                    native["configured"]
                    and self._signature(native["definition"]) == self._signature(candidate["definition"])
                    and (self.platform == "win32" or native["loaded"])
                ):
                    references = self.leases.read_runtime_references(self.root).get(REFERENCE, [])
                    if references != [candidate["version"]]:
                        self.leases.replace_runtime_references(
                            self.root, REFERENCE, [candidate["version"]], expected=references,
                        )
                    return self._result("unchanged", native, state, changed=False)
            before = state["confirmed"] if native["configured"] else None
            transaction = self.leases.begin_runtime_reference_transaction(self.root, REFERENCE)
            references = transaction["owners"].get(REFERENCE, [])
            pending = {
                "before": before, "candidate": candidate, "token": transaction["token"],
                "references": references, "intent": (
                    "remove" if remove else "replace" if desired["enabled"] else "disable"
                ),
                "native_unsettled": False,
            }
            working = {**state, "pending": pending}
            self._validate_state(working)
            slots._write(self.path, working)
            try:
                native = self._switch(native, candidate)
            except OwnedCleanupError:
                return self._result("unknown", native, self.read(), changed=None, rollback="unconfirmed")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                # Read back the exact task before deciding whether rollback is safe.
                try:
                    native = self._native_read()
                    if remove:
                        return self._result("unknown", native, working, changed=None, rollback="unconfirmed")
                    return self._recover(working, native, rollback=True)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    return self._result("unknown", native, working, changed=None, rollback="unconfirmed")
            try:
                return self._settle(working, native, candidate, status="removed" if remove else "converged")
            except (OSError, ValueError, RuntimeError):
                return self._result("unknown", native, working, changed=None, rollback="unconfirmed")

    def _recover_reference_fence(self, state: Document) -> None:
        owners, transaction = self.leases._read_runtime_reference_state(self.root, allow_transaction=True)
        if transaction is None:
            return
        if transaction["owner"] != REFERENCE:
            raise slots.HealthError("foreign-runtime-reference-transaction")
        # A fence without a pending native change precedes effects, or follows
        # a fully read-back state commit. Both are bound to confirmed state.
        confirmed = state["confirmed"]
        self.leases.commit_runtime_reference_transaction(
            self.root, REFERENCE, transaction["token"],
            {REFERENCE: [confirmed["version"]] if confirmed else []},
            expected={REFERENCE: owners.get(REFERENCE, [])},
        )

    def _native_call(self, operation: str, **fields: Any) -> Document:
        from agent_procutil.owned_process import OwnedCleanupError

        state = self.read()
        pending = state["pending"]
        if pending is None or pending["native_unsettled"]:
            raise slots.HealthError("native-health-task-cleanup-unconfirmed")
        token = pending["token"]
        slots._write(self.path, {**state, "pending": {**pending, "native_unsettled": True}})

        def settled() -> None:
            current = self.read()
            if current["pending"] is not None and current["pending"]["token"] == token:
                slots._write(self.path, {
                    **current, "pending": {**current["pending"], "native_unsettled": False},
                })

        def cleanup_settled() -> None:
            remaining = max(0, (self.native_cleanup_deadline or time.monotonic() + 5) - time.monotonic())
            with self.leases.reference_transaction_custody(self.root, timeout=min(5, remaining)):
                settled()

        try:
            result = self.backend.call(operation, **fields)
        except OwnedCleanupError as error:
            self.native_cleanup_error = error
            error._retain_on_settled(cleanup_settled)
            raise
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            settled()
            raise
        settled()
        return self._normalize(result)

    def _switch(self, native: Document, candidate: Document | None) -> Document:
        target = candidate["definition"] if candidate else None
        if native["configured"]:
            native = self._native_call("disable", expected=native["digest"])
            if native["enabled"] or native["instances"]:
                raise slots.HealthError("native-health-task-settlement-unconfirmed")
        if candidate is None:
            native = self._native_call("remove", expected=native["digest"])
            if native["configured"]:
                raise slots.HealthError("native-health-task-removal-unconfirmed")
            return native
        native = self._native_call(
            "replace" if native["configured"] else "create",
            expected=native["digest"], definition=self._disabled(target),
        )
        if (
            self._signature(native["definition"]) != self._signature(self._disabled(target))
            or native["instances"] or (self.platform == "linux" and not native["loaded"])
        ):
            raise slots.HealthError("native-health-task-readback-failed")
        if candidate["desired"]["enabled"]:
            # Withdrawal must not depend on collection readiness.
            self._policy(candidate["desired"], native)
            native = self._native_call("enable", expected=native["digest"])
        if self._signature(native["definition"]) != self._signature(target):
            raise slots.HealthError("native-health-task-readback-failed")
        return native

    def _settle(
        self, state: Document, native: Document, record: Document | None, *,
        status: str, rollback: str = "not-needed",
    ) -> Document:
        current = self.read()
        if current["pending"] is None or current["pending"]["native_unsettled"]:
            raise slots.HealthError("native-health-task-cleanup-unconfirmed")
        if record is not None and not record["desired"]["enabled"] and (
            native["enabled"] or native["instances"]
        ):
            raise slots.HealthError("native-health-task-settlement-unconfirmed")
        pending = state["pending"]
        updated = {
            **state, "desired": record["desired"] if record else None,
            "confirmed": {**record, "definition": native["definition"]} if record else None, "pending": None,
        }
        retained = sorted(set(pending["references"]) | ({record["version"]} if record else set()))
        self.leases.commit_runtime_reference_transaction(
            self.root, REFERENCE, pending["token"],
            {REFERENCE: retained},
            expected={REFERENCE: pending["references"]},
        )
        slots._write(self.path, updated)
        self.leases.replace_runtime_references(
            self.root, REFERENCE, [record["version"]] if record else [], expected=retained,
        )
        return self._result(status, native, updated, changed=True, rollback=rollback)

    def _recover(
        self, state: Document, native: Document, *,
        rollback: bool = False, remove: bool = False, disable: bool = False,
    ) -> Document:
        pending = state["pending"]
        if pending["native_unsettled"]:
            intent = (
                "remove" if remove or pending["intent"] == "remove"
                else "disable" if disable or pending["intent"] == "disable"
                else pending["intent"]
            )
            if intent != pending["intent"]:
                slots._write(self.path, {**state, "pending": {**pending, "intent": intent}})
            raise slots.HealthError("native-health-task-cleanup-unconfirmed")
        before, candidate = pending["before"], pending["candidate"]
        matched = self._pending_owner(native, pending)
        transaction = self.leases.begin_runtime_reference_transaction(self.root, REFERENCE)
        intent = (
            "remove" if remove or pending["intent"] == "remove"
            else "disable" if disable or pending["intent"] == "disable"
            else pending["intent"]
        )
        state = {**state, "pending": {
            **pending, "token": transaction["token"],
            "references": transaction["owners"].get(REFERENCE, []), "intent": intent,
        }}
        slots._write(self.path, state)
        if intent == "remove" or candidate is None:
            return self._settle(state, self._switch(native, None), None, status="removed")
        if intent == "disable":
            if not native["configured"]:
                return self._settle(state, native, None, status="rolled-back", rollback="confirmed")
            native = self._native_call("disable", expected=native["digest"])
            if self.platform == "linux" and matched is None:
                compatible = before or candidate
                if compatible is None:
                    raise slots.HealthError("health-task-recovery-conflict")
                matched = {
                    **compatible, "desired": {**compatible["desired"], "enabled": False},
                    "definition": self._disabled(compatible["definition"]),
                }
                native = self._switch(native, matched)
            if (
                native["enabled"] or native["instances"]
                or matched is None
                or self._signature(native["definition"]) != self._signature(self._disabled(matched["definition"]))
            ):
                raise slots.HealthError("native-health-task-settlement-unconfirmed")
            record = {
                **matched, "desired": {**matched["desired"], "enabled": False},
                "definition": native["definition"],
            }
            return self._settle(
                state, native, record, status="rolled-back" if rollback else "disabled",
                rollback="confirmed" if rollback else "not-needed",
            )
        if not rollback and candidate and matched == candidate and (
            self._signature(native["definition"]) == self._signature(candidate["definition"])
            and (self.platform == "win32" or native["loaded"])
        ):
            self._qualify_record(candidate, native)
            return self._settle(state, native, candidate, status="recovered-candidate")
        if before is not None:
            self._qualify_record(before, native)
        native = self._switch(native, before)
        return self._settle(state, native, before, status="rolled-back", rollback="confirmed")


def local_uid() -> int:
    if sys.platform != "linux" or os.getuid() != os.geteuid():
        raise slots.HealthError("local-job-account-mismatch")
    return os.getuid()


def local_desired(*, store: str, policy_token: str, user_id: str, enabled: bool) -> Document:
    if sys.platform == "linux":
        if user_id:
            raise slots.HealthError("platform-job-options-mismatch")
        return {
            "store": store, "policy_token": policy_token,
            "account_uid": local_uid(), "enabled": enabled,
        }
    if sys.platform != "win32":
        raise slots.HealthError("native-health-job-unsupported")
    return {"store": store, "policy_token": policy_token, "user_id": user_id, "enabled": enabled}
