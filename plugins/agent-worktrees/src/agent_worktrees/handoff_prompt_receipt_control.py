"""Closed, per-user-authenticated receipt operations on the existing resident."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
import importlib
import importlib.util
import json
import math
from pathlib import Path
import re
import stat
import threading
import time

ACTION = "handoff-prompt-receipt"
CAPABILITY = "handoff-prompt-receipt-v1"
FRAME_BYTES = 8192
INFLIGHT_LIMIT = 4
SUBJECT_FIELDS = frozenset(("project", "worktreeId", "creationNonce", "sessionId", "locator"))
IDENTITY_FIELDS = (
    "installationId", "sourceFingerprint", "marketplaceId", "pluginId",
    "installGeneration", "activationGeneration",
)
OWNER_FIELDS = frozenset((*IDENTITY_FIELDS, "routeGeneration", "pid",
                          "processStartTime", "runtimeGeneration"))
_COMPONENT = re.compile(r"[A-Za-z0-9_@][A-Za-z0-9_.@-]{0,255}\Z")
_REASON = re.compile(r"[a-z][a-z0-9-]{0,79}\Z")


class AdmissionUnavailable(ValueError):
    """A bounded pre-effect refusal; never include request content."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdmissionUnavailable("duplicate-field")
        result[key] = value
    return result


def _regular(path: Path) -> None:
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AdmissionUnavailable("linked-owner-state")


def _json_file(path: Path, limit: int = 65536) -> dict:
    _regular(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise AdmissionUnavailable("oversized-owner-state")
    value = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise AdmissionUnavailable("invalid-owner-state")
    return value


def _subject(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != SUBJECT_FIELDS:
        raise AdmissionUnavailable("invalid-subject")
    for key, text in value.items():
        limit = 512 if key == "locator" else 256
        if (
            not isinstance(text, str) or not text or len(text) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in text)
        ):
            raise AdmissionUnavailable("invalid-subject")
    if any(not _COMPONENT.fullmatch(value[key]) for key in ("project", "worktreeId")):
        raise AdmissionUnavailable("invalid-subject")
    locator = value["locator"]
    if (
        not locator.startswith(("task:", "file:")) or not locator.split(":", 1)[1]
        or not locator.isascii() or any(ord(c) <= 32 for c in locator)
    ):
        raise AdmissionUnavailable("invalid-subject")
    return dict(value)


def _backend():
    try:
        module = importlib.import_module(".handoff_prompt_receipt", __package__)
    except (ImportError, OSError, ValueError, SyntaxError, RuntimeError):
        return None
    if (
        type(getattr(module, "RESIDENT_RECEIPT_CONTRACT", None)) is int
        and module.RESIDENT_RECEIPT_CONTRACT == 1
        and callable(getattr(module, "resident_prompt_receipt", None))
    ):
        return module.resident_prompt_receipt
    return None


@contextmanager
def subject_admission(subject: dict, revalidate_owner, deadline: float):
    from . import config as cfg, installer, tracking

    def path_for_subject():
        try:
            projects = installer.read_projects_registry(strict=True).get("projects", {})
            if subject["project"] not in projects:
                raise AdmissionUnavailable("project-unavailable")
            path = cfg.tracking_dir(subject["project"]) / (subject["worktreeId"] + ".yaml")
            _regular(path)
            if path.stat().st_size > 1024 * 1024:
                raise AdmissionUnavailable("subject-record-too-large")
            return path
        except AdmissionUnavailable:
            raise
        except (OSError, ValueError, TypeError):
            raise AdmissionUnavailable("subject-unavailable") from None

    path = path_for_subject()
    with ExitStack() as locks:
        try:
            lock = locks.enter_context(tracking._RecordLock(
                path, blocking=False, require_sidecar=True,
            ))
        except (OSError, RuntimeError):
            raise AdmissionUnavailable("subject-lock-unavailable") from None
        if not lock.acquired:
            raise AdmissionUnavailable("subject-busy")

        def revalidate():
            if time.monotonic() >= deadline:
                raise AdmissionUnavailable("admission-timeout")
            revalidate_owner()
            if path_for_subject() != path:
                raise AdmissionUnavailable("subject-owner-changed")
            try:
                record = tracking.load_record(path)
            except (OSError, ValueError, TypeError):
                raise AdmissionUnavailable("subject-unavailable") from None
            if (
                record.worktree_id != subject["worktreeId"]
                or record.repo != subject["project"]
                or record.creation_nonce != subject["creationNonce"]
            ):
                raise AdmissionUnavailable("subject-incarnation-changed")
            if (
                record.session_entry(subject["sessionId"]) is None
                and not any(h.candidate == subject["sessionId"] for h in record.pending_handoffs)
            ):
                raise AdmissionUnavailable("subject-association-unavailable")
            if time.monotonic() >= deadline:
                raise AdmissionUnavailable("admission-timeout")

        revalidate()
        yield revalidate


class ReceiptControl:
    def __init__(self, owner, *, backend=None, admission=subject_admission):
        self._owner = owner
        self._backend = backend
        self._admission = admission
        self._condition = threading.Condition()
        self._closed = False
        self._active = 0
        self._mutations: set[tuple] = set()

    def active_count(self) -> int:
        with self._condition:
            return self._active

    def close_admission(self) -> None:
        with self._condition:
            self._closed = True

    def open_admission(self) -> None:
        with self._condition:
            self._closed = False

    def wait_idle(self) -> None:
        # A disconnected client cannot release an admitted fsync's ownership.
        with self._condition:
            self._condition.wait_for(lambda: self._active == 0)

    def _current_owner(self) -> dict:
        try:
            owner = self._owner()
        except AdmissionUnavailable:
            raise
        except (OSError, ValueError, RuntimeError):
            raise AdmissionUnavailable("owner-unavailable") from None
        required = OWNER_FIELDS - {"installGeneration", "activationGeneration"}
        if not isinstance(owner, dict) or set(owner) - OWNER_FIELDS or not required <= owner.keys():
            raise AdmissionUnavailable("owner-unavailable")
        for key, value in owner.items():
            if key in {"installGeneration", "activationGeneration", "routeGeneration", "pid"}:
                if type(value) is not int or value < 1:
                    raise AdmissionUnavailable("owner-unavailable")
            elif not isinstance(value, str) or not value or len(value) > 4096:
                raise AdmissionUnavailable("owner-unavailable")
        return dict(owner)

    def health(self) -> dict:
        with self._condition:
            closed = self._closed
        result = {"contractVersion": 1, "status": "unavailable"}
        if not closed and self._backend is not None:
            try:
                result.update(status="ready", owner=self._current_owner())
            except (OSError, ValueError, RuntimeError):
                pass
        return {
            "capabilities": [CAPABILITY] if result["status"] == "ready" else [],
            "promptReceipt": result,
        }

    @staticmethod
    def _result(status, reason, *, subject=None, owner=None, replayed=False):
        return {
            "contractVersion": 1, "status": status, "reason": reason,
            "replayed": replayed, "subject": subject, "owner": owner,
        }

    def handle(self, payload: dict) -> dict:
        subject = owner = None
        try:
            if (
                not isinstance(payload, dict)
                or set(payload) != {"contractVersion", "operation", "subject",
                                    "expectedOwner", "remainingMs"}
                or type(payload["contractVersion"]) is not int
                or payload["contractVersion"] != 1
                or payload["operation"] not in ("query", "record")
            ):
                raise AdmissionUnavailable("unsupported-request")
            subject = _subject(payload["subject"])
            remaining = payload["remainingMs"]
            if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
                raise AdmissionUnavailable("invalid-deadline")
            deadline = time.monotonic() + min(remaining, 2000) / 1000
            expected = payload["expectedOwner"]
            if not isinstance(expected, dict) or set(expected) - OWNER_FIELDS:
                raise AdmissionUnavailable("invalid-owner")
        except (KeyError, TypeError, AdmissionUnavailable) as error:
            reason = str(error) if isinstance(error, AdmissionUnavailable) else "invalid-request"
            return self._result("unavailable", reason)
        identity = tuple(subject[key] for key in sorted(SUBJECT_FIELDS))
        record = payload["operation"] == "record"
        with self._condition:
            if self._closed or self._backend is None:
                return self._result("unavailable", "backend-unavailable", subject=subject)
            if identity in self._mutations:
                return self._result("unknown", "record-in-flight", subject=subject)
            if self._active >= INFLIGHT_LIMIT:
                return self._result("unavailable", "receipt-busy", subject=subject)
            self._active += 1
            if record:
                self._mutations.add(identity)
        try:
            owner = self._current_owner()
            if owner != expected or any(type(expected[key]) is not type(value) for key, value in owner.items()):
                raise AdmissionUnavailable("owner-changed")

            def revalidate_owner():
                if self._current_owner() != owner:
                    raise AdmissionUnavailable("owner-changed")

            result = self._backend(
                subject["project"], subject["worktreeId"], subject["locator"], subject["sessionId"],
                creation_nonce=subject["creationNonce"], record=record, deadline=deadline,
                admission=lambda: self._admission(subject, revalidate_owner, deadline),
            )
            if (
                not isinstance(result, dict)
                or result.get("status") not in ("received", "absent", "unavailable", "unknown")
                or type(result.get("replayed")) is not bool
                or not isinstance(result.get("reason"), str)
                or not _REASON.fullmatch(result["reason"])
                or (record and result["status"] == "absent")
            ):
                return self._result("unknown", "invalid-backend-result", subject=subject, owner=owner)
            if time.monotonic() >= deadline:
                return self._result("unknown", "completion-after-deadline", subject=subject, owner=owner)
            try:
                same_owner = self._current_owner() == owner
            except (OSError, ValueError, RuntimeError):
                same_owner = False
            if not same_owner:
                return self._result("unknown", "owner-changed-after-admission", subject=subject, owner=owner)
            if time.monotonic() >= deadline:
                return self._result("unknown", "completion-after-deadline", subject=subject, owner=owner)
            return self._result(
                result["status"], result["reason"], subject=subject, owner=owner,
                replayed=result["replayed"],
            )
        except AdmissionUnavailable as error:
            return self._result("unavailable", str(error), subject=subject, owner=owner)
        except Exception:
            return self._result("unknown", "backend-unavailable", subject=subject, owner=owner)
        finally:
            with self._condition:
                self._active -= 1
                if record:
                    self._mutations.remove(identity)
                self._condition.notify_all()


def _selected_payload_binding(runtime_home: Path):
    from . import _command_binding, _installation_context, peer_launch_adapter

    context = peer_launch_adapter.validate_context()
    if context is not None:
        if Path(context["pluginRoot"]) != runtime_home:
            raise AdmissionUnavailable("installation-unavailable")
        raw_payload = context.get("payloadRoot")
    else:
        manifest = _json_file(runtime_home / "deploy-manifest.json")
        if manifest.get("schema_version") != 3 or manifest.get("service") != "agent-worktrees":
            raise AdmissionUnavailable("installation-unavailable")
        declared = manifest.get("commandBinding")
        raw_payload = declared.get("payloadRoot") if isinstance(declared, dict) else None
    if not isinstance(raw_payload, str) or not raw_payload:
        raise AdmissionUnavailable("payload-unavailable")
    payload = Path(raw_payload)
    if not payload.is_absolute() or ".." in payload.parts:
        raise AdmissionUnavailable("payload-unavailable")
    _regular(payload)
    binding = _command_binding.resolve_command_binding(
        _installation_context, payload_root=payload, plugin_id="agent-worktrees",
        legacy_root=runtime_home,
    )
    if binding.get("status") != "selected":
        raise AdmissionUnavailable("installation-unavailable")
    return payload, binding


def for_runtime(runtime_home: Path, runtime_generation: str, published, *, port=None):
    """Select only this resident's existing payload and active route, never recover."""
    from . import status_monitor_cutover as control
    import os

    backend = _backend()
    if backend is None:
        return ReceiptControl(lambda: None)
    try:
        payload_root, binding = _selected_payload_binding(runtime_home)
        projection_path = payload_root / "scripts" / "resident_control_projection.py"
        _regular(projection_path)
        spec = importlib.util.spec_from_file_location("_resident_control_projection", projection_path)
        if spec is None or spec.loader is None:
            return ReceiptControl(lambda: None)
        projection = importlib.util.module_from_spec(spec)
        with projection_path.open("rb") as source:
            code = source.read(65537)
        if len(code) > 65536:
            return ReceiptControl(lambda: None)
        exec(compile(code, str(projection_path), "exec"), projection.__dict__)
        initial_candidate = projection.project_candidate(binding)
        if initial_candidate is None or Path(initial_candidate["runtimeRoot"]) != runtime_home:
            return ReceiptControl(lambda: None)
    except (AttributeError, OSError, TypeError, ValueError, RuntimeError, ImportError, SyntaxError):
        return ReceiptControl(lambda: None)

    def owner():
        if not published():
            raise AdmissionUnavailable("owner-unavailable")
        current_payload, binding = _selected_payload_binding(runtime_home)
        candidate = projection.project_candidate(binding)
        if current_payload != payload_root or candidate != initial_candidate:
            raise AdmissionUnavailable("installation-unavailable")
        route = _json_file(control.routing_dir(runtime_home) / "active.json").get("active")
        if (
            not isinstance(route, dict) or route.get("bind") != "127.0.0.1"
            or type(route.get("port")) is not int or not 0 < route["port"] < 65536
            or port is None or route["port"] != port()
            or type(route.get("pid")) is not int or route["pid"] != os.getpid()
            or type(route.get("generation")) is not int or route["generation"] < 1
            or not isinstance(route.get("process_start_time"), str)
            or route["process_start_time"] != str(control.locks.process_start_time(os.getpid()))
        ):
            raise AdmissionUnavailable("active-owner-unavailable")
        return {
            **{key: candidate[key] for key in IDENTITY_FIELDS if key in candidate},
            "routeGeneration": route["generation"], "pid": route["pid"],
            "processStartTime": route["process_start_time"], "runtimeGeneration": runtime_generation,
        }

    return ReceiptControl(owner, backend=backend)
