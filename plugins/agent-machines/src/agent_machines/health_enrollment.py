"""Local opt-in and reversible Windows registration under installation custody."""

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
from xml.etree import ElementTree as ET

from . import _peer_launch, health_jobs, health_run, health_slots as slots

SCHEMA = "agent-machines.health.enrollment"
STATE = "health-job.json"
REFERENCE = "agent-machines/health-job"
MAX_STATE = 64 * 1024


@dataclass(frozen=True)
class Installation:
    root: Path
    slot: Path
    python: Path
    gui: str
    identity: str
    runtime: object

    def definition(self, desired: dict, owner: str, *, enabled: bool) -> str:
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

    if sys.platform != "win32":
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
        if getattr(launcher, "GUI_BOOTSTRAP_VERSION", None) != 1:
            raise slots.HealthError("installed-job-bootstrap-unsupported")
        require_collection_support(python)
        gui = windowless_python(python, require_windowless=True)
        require_gui_host(Path(gui))
    return Installation(root, slot, python, gui, identity, runtime)


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


def validate_desired(value: dict) -> dict:
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


def _record(value):
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


def _state(value):
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
            type(pending) is not dict or set(pending) != {"before", "candidate", "token", "references"}
            or re.fullmatch(r"[a-f0-9]{64}", str(pending["token"])) is None
            or type(pending["references"]) is not list or len(pending["references"]) > 8
        ):
            raise slots.HealthError("invalid-local-job-recovery")
        _record(pending["before"])
        _record(pending["candidate"])
    return value


def empty():
    return {"schema": SCHEMA, "version": 1, "desired": None, "confirmed": None, "pending": None}


def signature(xml: str) -> dict:
    """Compare controlled native semantics, allowing scheduler-added defaults."""
    if len(xml) > 16000 or "<!" in xml:
        raise slots.HealthError("invalid-native-job-definition")
    root = ET.fromstring(xml)
    ns = {"t": health_jobs.NS}
    for path in ("t:Actions/t:Exec", "t:Triggers/t:TimeTrigger", "t:Principals/t:Principal"):
        if len(root.findall(path, ns)) != 1:
            raise slots.HealthError("invalid-native-job-definition")
    for path in ("t:Actions", "t:Triggers", "t:Principals"):
        if len(list(root.find(path, ns))) != 1:
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
    result = {}
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
    node = settings.find(f"{{{health_jobs.NS}}}Enabled")
    if node is None:
        node = ET.SubElement(settings, f"{{{health_jobs.NS}}}Enabled")
    node.text = "false"
    return ET.tostring(root, encoding="unicode")


class Enrollment:
    def __init__(self, installation: Installation, *, backend=None, selector=select, qualifier=None):
        from .health_task_native import NativeTask

        self.installation = installation
        self.root = installation.root
        self.runtime = installation.runtime
        self.leases = self.runtime._runtime_component("versioned_runtime_leases")
        self.path = self.root / STATE
        info = self.root.stat()
        if not info.st_ino:
            raise slots.HealthError("installed-job-root-identity-unavailable")
        identity = hashlib.sha256(f"{info.st_dev}:{info.st_ino}".encode("ascii")).hexdigest()
        self.owner = "urn:copilot-extensions:health-job:" + identity
        self.backend = backend or NativeTask("ce-health-" + identity[:32], self.owner)
        self.selector = selector
        self.qualifier = qualifier or self._qualify

    def read(self):
        state, _ = slots._read(self.path, _state, optional=True, maximum=MAX_STATE)
        return state or empty()

    def status(self):
        return self._result("observed", self.backend.call("read"), self.read(), changed=False)

    def disable(self):
        with self.leases.reference_transaction_custody(self.root, timeout=5):
            state = self.read()
            native = self.backend.call("read")
            if state["pending"] is not None:
                raise slots.HealthError("health-task-recovery-required")
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
                "definition": disabled(state["confirmed"]["definition"]),
            }
            transaction = self.leases.begin_runtime_reference_transaction(self.root, REFERENCE)
            working = {**state, "pending": {
                "before": state["confirmed"], "candidate": record,
                "token": transaction["token"], "references": transaction["owners"].get(REFERENCE, []),
            }}
            slots._write(self.path, working)
            try:
                native = self.backend.call("disable", expected=native["digest"])
                if (
                    native["enabled"] or native["instances"]
                    or signature(native["xml"]) != signature(record["definition"])
                ):
                    raise slots.HealthError("native-health-task-settlement-unconfirmed")
                return self._settle(working, native, record, status="disabled")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                return self._result("unknown", native, working, changed=None, rollback="unconfirmed")

    def _result(self, status, native, state, *, changed, rollback="not-needed"):
        return {
            "schema": SCHEMA, "version": 1, "status": status, "changed": changed,
            "adopted": state["desired"] is not None, "configured": native["configured"],
            "enabled": native["enabled"], "instances": native["instances"],
            "state": native.get("state"), "last_result": native.get("last_result"),
            "last_run": native.get("last_run"), "next_run": native.get("next_run"),
            "recovery_required": state["pending"] is not None, "rollback": rollback,
            "candidate_readiness": (
                "ready" if status in {"converged", "unchanged", "recovered-candidate"}
                else "rejected" if status == "rolled-back"
                else "unknown" if status == "unknown" else "not-requested"
            ),
            "carrier_publication": "not-requested",
        }

    def _qualify(self, installation, desired):
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

    def _qualify_record(self, record, native):
        installation = self.selector(self.root, record["version"])
        if installation.identity != record["identity"]:
            raise slots.HealthError("health-task-recovery-build-changed")
        expected = installation.definition(
            record["desired"], self.owner, enabled=record["desired"]["enabled"],
        )
        if signature(record["definition"]) != signature(expected):
            raise slots.HealthError("health-task-recovery-closure-changed")
        self._policy(record["desired"], native)
        self.qualifier(installation, record["desired"])

    def _policy(self, desired, native):
        validate_desired(desired)
        if native["account_sid"] != desired["user_id"]:
            raise slots.HealthError("interactive-job-account-mismatch")
        if health_run.policy_preview(Path(desired["store"]))[2] != desired["policy_token"]:
            raise slots.HealthError("installed-job-policy-changed")

    def _owned(self, native, record):
        if record is not None and signature(record["definition"])["RegistrationInfo/Description"] != self.owner:
            raise slots.HealthError("foreign-or-drifted-health-task")
        if not native["configured"]:
            return
        if record is None or signature(native["xml"]) != signature(record["definition"]):
            raise slots.HealthError("foreign-or-drifted-health-task")
        if signature(native["xml"])["RegistrationInfo/Description"] != self.owner:
            raise slots.HealthError("foreign-or-drifted-health-task")

    def configure(self, desired, *, apply=False, expected=""):
        validate_desired(desired)
        if expected and not apply:
            raise slots.HealthError("confirmation-requires-apply")
        state = self.read()
        native = self.backend.call("read")
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
        return self.converge(desired=desired, expected_state=state, expected_native=native["digest"])

    def converge(self, *, desired=None, expected_state=None, expected_native=None, remove=False):
        from agent_procutil.owned_process import OwnedCleanupError

        with self.leases.reference_transaction_custody(self.root, timeout=5), ExitStack() as retained:
            state = self.read()
            native = self.backend.call("read")
            if expected_state is not None and (state != expected_state or native["digest"] != expected_native):
                raise slots.HealthError("enrollment-preview-changed")
            if state["pending"] is not None:
                return self._recover(state, native, remove=remove)
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
                _record(candidate)
                if native["configured"] and signature(native["xml"]) == signature(candidate["definition"]):
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
                "references": references,
            }
            working = {**state, "pending": pending}
            _state(working)
            slots._write(self.path, working)
            try:
                native = self._switch(native, candidate)
            except OwnedCleanupError:
                return self._result("unknown", native, working, changed=None, rollback="unconfirmed")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                # Read back the exact task before deciding whether rollback is safe.
                try:
                    native = self.backend.call("read")
                    if remove:
                        return self._result("unknown", native, working, changed=None, rollback="unconfirmed")
                    return self._recover(working, native, rollback=True)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    return self._result("unknown", native, working, changed=None, rollback="unconfirmed")
            try:
                return self._settle(working, native, candidate, status="removed" if remove else "converged")
            except (OSError, ValueError, RuntimeError):
                return self._result("unknown", native, working, changed=None, rollback="unconfirmed")

    def _recover_reference_fence(self, state):
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

    def _switch(self, native, candidate):
        target = candidate["definition"] if candidate else None
        if native["configured"]:
            native = self.backend.call("disable", expected=native["digest"])
            if native["enabled"] or native["instances"]:
                raise slots.HealthError("native-health-task-settlement-unconfirmed")
        if candidate is None:
            native = self.backend.call("remove", expected=native["digest"])
            if native["configured"]:
                raise slots.HealthError("native-health-task-removal-unconfirmed")
            return native
        native = self.backend.call(
            "replace" if native["configured"] else "create",
            expected=native["digest"], definition=disabled(target),
        )
        if signature(native["xml"]) != signature(disabled(target)) or native["instances"]:
            raise slots.HealthError("native-health-task-readback-failed")
        # Revalidate policy at the enable boundary, including replacement/rollback.
        self._policy(candidate["desired"], native)
        if candidate["desired"]["enabled"]:
            native = self.backend.call("enable", expected=native["digest"])
        if signature(native["xml"]) != signature(target):
            raise slots.HealthError("native-health-task-readback-failed")
        return native

    def _settle(self, state, native, record, *, status, rollback="not-needed"):
        pending = state["pending"]
        updated = {
            **state, "desired": record["desired"] if record else None,
            "confirmed": {**record, "definition": native["xml"]} if record else None, "pending": None,
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

    def _recover(self, state, native, *, rollback=False, remove=False):
        pending = state["pending"]
        before, candidate = pending["before"], pending["candidate"]
        matched = None
        if native["configured"]:
            actual = signature(native["xml"])
            for record in (candidate, before):
                if record and actual in (signature(record["definition"]), signature(disabled(record["definition"]))):
                    matched = record
                    break
            if matched is None:
                raise slots.HealthError("health-task-recovery-conflict")
        transaction = self.leases.begin_runtime_reference_transaction(self.root, REFERENCE)
        state = {**state, "pending": {
            **pending, "token": transaction["token"],
            "references": transaction["owners"].get(REFERENCE, []),
        }}
        slots._write(self.path, state)
        if remove or candidate is None:
            return self._settle(state, self._switch(native, None), None, status="removed")
        if not rollback and candidate and matched == candidate and (
            signature(native["xml"]) == signature(candidate["definition"])
        ):
            self._qualify_record(candidate, native)
            return self._settle(state, native, candidate, status="recovered-candidate")
        if before is not None:
            self._qualify_record(before, native)
        native = self._switch(native, before)
        return self._settle(state, native, before, status="rolled-back", rollback="confirmed")
