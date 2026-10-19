"""Owner commit/rollback custody independent of the shared ZDD breadcrumb."""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import UUID, uuid4

from zdd import diagnostics, publication_history
from zdd.routing import Endpoint
from zdd.cutover_lock import CutoverLock, CutoverLockedError, lock_path
from zdd.process_exit import ProcessExitReference

from . import _installation_context as api
from .watch_installation import PLUGIN_ID, WatchInstallation
from .watch_custody import endpoint_tuple, endpoint_value, read_launch, require_absent
from .watch_exit import observe_cleanup_exit, observe_exit, record_exit
from .watch_private import confirm_private, require_private, write_private
from .watch_runtime import WatchInstance, WatchRuntime
from .watch_rollback import require_rollback_selection, restore_route, validate_restoration

_FILE = "watch-owner-transaction.json"
_PHASES = {
    "prepared", "passive", "draining", "released", "read-ready", "publishing",
    "committed", "complete", "rolling-back", "rolled-back",
}
_CONTROL_PHASES = {
    ("predecessor", "drain"): {"draining"},
    ("predecessor", "release"): {"draining"},
    ("successor", "adopt"): {"released"},
    ("successor", "activate"): {"committed", "complete"},
    ("predecessor", "retire"): {"committed"},
    ("successor", "retire"): {"rolling-back"},
    ("predecessor", "resume"): {"rolling-back"},
}
_CLEANUP_PHASES = {"prepared", "passive", "draining", "released", "rolling-back"}


def _regular_bytes(path: Path) -> bytes | None:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise PermissionError("watch journal metadata must be a regular non-link file")
    if info.st_size > 1_048_576:
        raise ValueError("watch journal metadata exceeds its bounded size")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        content = handle.read(1_048_577)
        finished = os.fstat(handle.fileno())
    current = path.stat(follow_symlinks=False)
    identities = [
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        for item in (info, opened, finished, current)
    ]
    # Windows can expose creation time via lstat and change time via fstat.
    metadata_stable = all(
        (before.st_mode, before.st_ctime_ns) == (after.st_mode, after.st_ctime_ns)
        for before, after in ((info, current), (opened, finished))
    )
    if (
        len(content) > 1_048_576 or any(item != identities[0] for item in identities[1:])
        or not metadata_stable
        or not stat.S_ISREG(current.st_mode)
        or getattr(current, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise PermissionError("watch journal metadata changed during its bounded read")
    return content


def _json_object(raw: bytes) -> dict:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("watch journal contains duplicate fields")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("watch journal metadata must be a JSON object")
    return value

def _private_bytes(path: Path) -> bytes | None:
    if not os.path.lexists(path):
        return None
    before = path.stat(follow_symlinks=False)
    require_private(path)
    raw = _regular_bytes(path)
    require_private(path)
    after = path.stat(follow_symlinks=False)
    if (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    ) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        raise PermissionError("watch capability changed during its private read")
    return raw


def _persist(path: Path, document: dict, *, replace: bool = True) -> None:
    if len(json.dumps(document, allow_nan=False).encode("utf-8")) > 1_048_576:
        raise ValueError("watch owner transaction exceeds its bounded recovery size")
    write_private(path, document, replace=replace)


def _selection(installation: WatchInstallation) -> dict[str, str | None]:
    paths = {
        "current": installation.plugin_root / api.CURRENT_VERSION_FILE,
        "lastKnownGood": installation.plugin_root / api.LAST_KNOWN_GOOD_FILE,
        "manifest": installation.plugin_root / "deploy-manifest.json",
        "route": installation.run_root / "active.json",
    }
    return {
        key: None if (raw := _regular_bytes(path)) is None else raw.hex()
        for key, path in paths.items()
    }


def _manifest(raw: bytes, runtime: WatchRuntime) -> None:
    manifest = _json_object(raw)
    source = manifest.get("source")
    if (
        manifest.get("service") != PLUGIN_ID or not isinstance(source, dict)
        or source.get("plugin") != PLUGIN_ID or source.get("version") != runtime.payload_version
        or not isinstance(manifest.get("venv"), str)
        or not api.paths_equal(manifest["venv"], runtime.runtime_root)
    ):
        raise PermissionError("watch selection manifest does not match its completed runtime")

def _route(raw: bytes, instance: WatchInstance) -> None:
    active = _json_object(raw).get("active")
    if (
        not isinstance(active, dict) or active.get("pid") != instance.pid
        or active.get("process_start_time") != instance.start_time
        or active.get("version") != instance.runtime.runtime_version
        or active.get("bind") not in {"127.0.0.1", "::1"}
        or type(active.get("port")) is not int or not 0 < active["port"] < 65536
    ):
        raise PermissionError("watch target route does not match exact successor")


def _target_selection(installation: WatchInstallation, document: dict) -> None:
    selected = _selection(installation)
    version = document["target"]["runtimeVersion"]
    expected_route = document["targetRoute"]
    if document["successor"] is None and document["predecessor"] is None:
        expected_route = document["prior"]["route"]
    route_matches = selected["route"] == expected_route
    if selected["route"] is not None and expected_route is not None:
        actual = _json_object(bytes.fromhex(selected["route"]))
        expected = _json_object(bytes.fromhex(expected_route))
        route_matches = actual.get("active") == expected.get("active")
        if "publication_history" in expected:
            route_matches = route_matches and all(
                actual.get(key) == expected.get(key)
                for key in ("previous", "publication_history")
            )
    if (
        selected["current"] is None or selected["lastKnownGood"] is None
        or bytes.fromhex(selected["current"]).decode("utf-8").strip() != version
        or bytes.fromhex(selected["lastKnownGood"]).decode("utf-8").strip() != version
        or selected["manifest"] != document["targetManifest"]
        or not route_matches
    ):
        raise PermissionError("watch target marker, manifest and route are not coherent")

def _controller() -> dict[str, object]:
    pid = os.getpid()
    token = diagnostics.process_start_time(pid)
    if token is None:
        raise NotImplementedError("watch controller requires native process birth evidence")
    return {"pid": pid, "startTime": token}


def _controller_held(
    installation: WatchInstallation, controller: dict, guard: CutoverLock | None,
) -> None:
    if guard is not None:
        if (
            controller != _controller()
            or not api.paths_equal(guard.path, lock_path(installation.run_root))
        ):
            raise PermissionError("watch control guard belongs to another controller")
        guard.require_owned()
        return
    if diagnostics.process_start_time(controller["pid"]) != controller["startTime"]:
        raise PermissionError("watch transaction controller identity is not live")
    contender = CutoverLock(installation.run_root)
    try:
        contender.acquire()
    except CutoverLockedError as exc:
        if exc.holder_pid != controller["pid"]:
            raise PermissionError("watch transaction guard has a different native holder") from exc
    else:
        contender.release()
        raise PermissionError("watch lifecycle control has no held transaction guard")
    if diagnostics.process_start_time(controller["pid"]) != controller["startTime"]:
        raise PermissionError("watch transaction controller changed during guard validation")


def _read_identity(
    installation: WatchInstallation, *, environment: Mapping[str, str] | None,
) -> dict:
    installation.read_identity(environment=environment)
    raw = _private_bytes(installation.run_root / _FILE)
    if raw is None:
        raise FileNotFoundError("watch owner transaction is missing")
    document = _json_object(raw)
    if document.get("installation") != installation.evidence():
        raise PermissionError("watch owner transaction binding or phase is invalid")
    return _validate_document(installation, document, environment=environment)


def _validate_document(
    installation: WatchInstallation, document: dict, *, environment: Mapping[str, str] | None,
) -> dict:
    if set(document) != {
        "schema", "transactionId", "authorization", "installation", "phase", "revision",
        "controller", "prior", "target", "targetManifest", "predecessor", "successor",
        "targetRoute", "exited", "launchInstanceId", "endpoints", "rollbackRoute",
    }:
        raise ValueError("watch owner transaction has unknown or missing fields")
    if (
        type(document["schema"]) is not int or document["schema"] != 3
        or not isinstance(document["phase"], str) or document["phase"] not in _PHASES
        or type(document["revision"]) is not int or document["revision"] < 1
    ):
        raise PermissionError("watch owner transaction binding or phase is invalid")
    if str(UUID(document["transactionId"])) != document["transactionId"]:
        raise ValueError("watch owner transaction ID is invalid")
    launch_id = document["launchInstanceId"]
    if launch_id is not None and (
        not isinstance(launch_id, str) or str(UUID(launch_id)) != launch_id
        or UUID(launch_id).version != 4
    ):
        raise ValueError("watch successor launch identity is invalid")
    controller = document["controller"]
    if (
        not isinstance(controller, dict) or set(controller) != {"pid", "startTime"}
        or type(controller["pid"]) is not int or not 0 < controller["pid"] <= 0xFFFFFFFF
        or not isinstance(controller["startTime"], str)
        or not 0 < len(controller["startTime"]) <= 64
        or not controller["startTime"].isascii() or not controller["startTime"].isdecimal()
    ):
        raise ValueError("watch transaction controller identity is invalid")
    authorization = document["authorization"]
    if (
        not isinstance(authorization, str) or len(authorization) != 64
        or any(char not in "0123456789abcdef" for char in authorization)
    ):
        raise ValueError("watch owner transaction authorization is invalid")
    target = WatchRuntime.from_identity_evidence(
        document["target"], installation, environment=environment,
    )
    _manifest(bytes.fromhex(document["targetManifest"]), target)
    instances = {}
    for role in ("predecessor", "successor"):
        if document[role] is not None:
            value = document[role]
            same_runtime = isinstance(value, dict) and {
                key: item for key, item in value.items()
                if key not in {"pid", "startTime", "instanceId"}
            } == target.evidence()
            if role == "successor" or same_runtime:
                value = document[role]
                if not isinstance(value, dict):
                    raise ValueError("watch successor evidence must be an object")
                instance = WatchInstance(
                    target, value.get("pid"), value.get("startTime"), value.get("instanceId"),
                )
                if instance.evidence() != value:
                    raise PermissionError("watch successor does not match the completed target")
            else:
                instance = WatchInstance.from_identity_evidence(
                    document[role], installation, environment=environment,
                )
            instances[role] = instance
    if (
        not isinstance(document["prior"], dict)
        or set(document["prior"]) != {"current", "lastKnownGood", "manifest", "route"}
        or not isinstance(document["exited"], list)
        or len(set(document["exited"])) != len(document["exited"])
        or any(role not in {"predecessor", "successor"} for role in document["exited"])
    ):
        raise ValueError("watch owner transaction recovery evidence is invalid")
    for value in document["prior"].values():
        if value is not None:
            bytes.fromhex(value)
    if document["targetRoute"] is not None:
        if "successor" not in instances:
            raise PermissionError("watch route has no attributable successor")
        route = bytes.fromhex(document["targetRoute"])
        _route(route, instances["successor"])
        if (
            launch_id is not None and document["phase"] in {"committed", "complete"}
            and publication_history.read(_json_object(route)) is None
        ):
            raise PermissionError("watch committed successor lacks captured native promotion history")
    elif "successor" in instances and document["phase"] not in {
        "prepared", "rolling-back", "rolled-back",
    }:
        raise PermissionError("watch successor has no bound target route")
    if "successor" in instances and launch_id is not None and (
        instances["successor"].instance_id != launch_id
    ):
        raise PermissionError("watch successor differs from its reserved launch")
    if (
        document["phase"] in {"passive", "draining", "released", "read-ready", "publishing"}
        and "successor" not in instances
    ):
        raise PermissionError("live watch transition lacks exact instance evidence")
    if (
        "successor" in instances and "predecessor" in instances and (
            instances["predecessor"].pid == instances["successor"].pid
            or instances["predecessor"].instance_id == instances["successor"].instance_id
        )
    ):
        raise PermissionError("watch transaction instances are not distinct")
    if "predecessor" not in instances and document["prior"]["route"] is not None:
        from .watch_withdrawal import require_withdrawn_route

        require_withdrawn_route(
            installation, _json_object(bytes.fromhex(document["prior"]["route"])),
        )
    if any(role not in instances for role in document["exited"]):
        raise PermissionError("watch exit evidence has no attributable instance")
    endpoints = document["endpoints"]
    if not isinstance(endpoints, dict) or set(endpoints) != {"predecessor", "successor"}:
        raise ValueError("watch endpoint custody has unknown or missing roles")
    for role, value in endpoints.items():
        if value is not None:
            endpoint_tuple(value)
            if role not in instances:
                raise PermissionError("watch endpoint has no attributable instance")
    if all(endpoints.values()) and (
        endpoints["predecessor"]["host"], endpoints["predecessor"]["port"],
    ) == (
        endpoints["successor"]["host"], endpoints["successor"]["port"],
    ):
        raise PermissionError("watch transaction endpoints are not distinct")
    if endpoints["successor"] is not None and document["targetRoute"] is not None:
        route = _json_object(bytes.fromhex(document["targetRoute"]))["active"]
        if route["bind"] != "127.0.0.1" or route["port"] != endpoints["successor"]["port"]:
            raise PermissionError("watch successor route differs from its authenticated endpoint")
    if endpoints["predecessor"] is not None and document["prior"]["route"] is not None:
        _route(bytes.fromhex(document["prior"]["route"]), instances["predecessor"])
        route = _json_object(bytes.fromhex(document["prior"]["route"]))["active"]
        if route["bind"] != "127.0.0.1" or route["port"] != endpoints["predecessor"]["port"]:
            raise PermissionError("watch predecessor route differs from its authenticated endpoint")
    if document["phase"] in {"complete", "rolled-back"} and (
        launch_id is not None and "successor" not in instances
    ):
        raise PermissionError("watch terminal transaction has unresolved launch custody")
    if (
        launch_id is not None and "predecessor" in instances
        and launch_id == instances["predecessor"].instance_id
    ):
        raise PermissionError("watch reserved successor is not distinct from predecessor")
    if (
        document["phase"] in {"committed", "complete"}
        and "predecessor" in instances and "successor" not in instances
    ):
        raise PermissionError("live watch commit has no attributable successor")
    if (
        document["phase"] in {"committed", "complete"} and "successor" in document["exited"]
        or document["phase"] == "complete" and "predecessor" in instances
        and "predecessor" not in document["exited"]
        or document["phase"] == "rolled-back" and (
            "predecessor" in document["exited"]
            or "successor" in instances and "successor" not in document["exited"]
        )
    ):
        raise PermissionError("watch terminal custody lacks exact obsolete-instance exit evidence")
    validate_restoration(document)
    installation.read_identity(environment=environment)
    return document


def _cleanup_document(installation: WatchInstallation, document: dict) -> None:
    if (
        document["phase"] not in _CLEANUP_PHASES
        or _selection(installation) != document["prior"]
    ):
        raise PermissionError("watch passive cleanup requires unchanged precommit custody")


def _passive_health(instance: WatchInstance, health: Mapping[str, object]) -> None:
    if (
        health.get("instance") != instance.evidence()
        or health.get("writerHeld") is not False or health.get("readReady") is not False
        or health.get("effectsEnabled") is not False
    ):
        raise PermissionError("watch cleanup requires an exact effect-free passive instance")


def _terminal_custody(document: dict) -> dict:
    # Recovery may rotate only controller capability, not completed custody.
    return {
        key: value for key, value in document.items()
        if key not in {"controller", "authorization", "revision"}
    }


def _require_cell_history(installation: WatchInstallation, historical: object) -> None:
    current = installation.evidence()
    advancing = {"generation", "namespaceGeneration", "payloadRoot"}
    if (
        not isinstance(historical, dict) or set(historical) != set(current)
        or any(historical[key] != current[key] for key in current if key not in advancing)
        or any(
            type(historical[key]) is not int or not 0 < historical[key] <= current[key]
            for key in ("generation", "namespaceGeneration")
        )
        or not isinstance(historical["payloadRoot"], str)
        or not Path(historical["payloadRoot"]).is_absolute()
    ):
        raise PermissionError("watch archived terminal belongs to another installation identity")


def _archived_terminal(
    installation: WatchInstallation, *, environment: Mapping[str, str] | None,
) -> dict:
    """Only already-archived terminal history may cross a management receipt update."""
    installation.require_current(environment=environment)
    raw = _private_bytes(installation.run_root / _FILE)
    if raw is None:
        raise FileNotFoundError("watch terminal transaction is missing")
    document = _json_object(raw)
    if document.get("phase") not in {"complete", "rolled-back"}:
        raise FileExistsError("watch owner transaction requires explicit reconciliation and archival")
    _require_cell_history(installation, document.get("installation"))
    document = _validate_document(installation, document, environment=environment)
    archive = installation.run_root / "watch-owner-history" / f'{document["transactionId"]}.json'
    saved = _private_bytes(archive)
    if saved is None or _terminal_custody(_json_object(saved)) != _terminal_custody(document):
        raise FileExistsError("watch owner transaction requires explicit reconciliation and archival")
    confirm_private(archive)
    installation.require_current(environment=environment)
    return document


def read_known_owner(
    installation: WatchInstallation, *, environment: Mapping[str, str] | None,
) -> dict:
    """Read current custody or already-archived terminal history in this exact cell."""
    installation.read_identity(environment=environment)
    raw = _private_bytes(installation.run_root / _FILE)
    if raw is None:
        raise FileNotFoundError("watch owner transaction is missing")
    if _json_object(raw).get("installation") == installation.evidence():
        return _read_identity(installation, environment=environment)
    return _archived_terminal(installation, environment=environment)


class WatchJournal:
    """A controller journal; every write validates custody of the exact guard."""

    def __init__(
        self, installation: WatchInstallation, guard: CutoverLock, document: dict, *,
        environment: Mapping[str, str] | None,
    ) -> None:
        self.installation = installation
        self.guard = guard
        self._document = document
        self.environment = environment
        self._execution = (os.getpid(), threading.current_thread())

    def _guard(self) -> None:
        if self._execution != (os.getpid(), threading.current_thread()):
            raise PermissionError("watch journal belongs to another controller execution")
        if not api.paths_equal(self.guard.path, lock_path(self.installation.run_root)):
            raise PermissionError("watch journal requires its installation cutover guard")
        self.guard.require_owned()

    @classmethod
    def begin(
        cls, installation: WatchInstallation, guard: CutoverLock, *,
        target: WatchRuntime, target_manifest: bytes,
        predecessor: WatchInstance | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> WatchJournal:
        journal = cls(installation, guard, {}, environment=environment)
        journal._guard()
        target.require_current(installation, environment=environment)
        _manifest(target_manifest, target)
        if predecessor is not None:
            predecessor.runtime.require_current(installation, environment=environment)
        previous = None
        if os.path.lexists(installation.run_root / _FILE):
            previous = _archived_terminal(installation, environment=environment)
            from .watch_withdrawal import read, require_withdrawn_route

            withdrawal = read(installation, previous)
            if withdrawal is not None:
                if withdrawal["phase"] != "complete":
                    raise PermissionError("watch withdrawal requires reconciliation before startup")
                route = _selection(installation)["route"]
                if route is None:
                    raise PermissionError("watch withdrawal lost its native route custody")
                require_withdrawn_route(
                    installation, _json_object(bytes.fromhex(route)), probe_writer=True,
                )
                served = None
            elif previous["phase"] == "complete":
                _target_selection(installation, previous)
                served = previous["successor"]
            else:
                require_rollback_selection(installation, previous)
                served = previous["predecessor"]
            if served is not None and (
                predecessor is None or predecessor.evidence() != served
            ):
                raise PermissionError("watch next transaction must retain the exact terminal owner")
        prior = _selection(installation)
        if predecessor is None and prior["route"] is not None:
            from .watch_withdrawal import require_withdrawn_route

            require_withdrawn_route(
                installation, _json_object(bytes.fromhex(prior["route"])), probe_writer=True,
            )
        if predecessor is None:
            require_absent(installation)
        if predecessor is not None and prior["route"] is not None:
            _route(bytes.fromhex(prior["route"]), predecessor)
        journal._document = {
            "schema": 3, "transactionId": str(uuid4()),
            "authorization": secrets.token_hex(32), "installation": installation.evidence(),
            "controller": _controller(),
            "phase": "prepared", "revision": 1, "prior": prior,
            "target": target.evidence(), "targetManifest": target_manifest.hex(),
            "predecessor": None if predecessor is None else predecessor.evidence(),
            "successor": None, "targetRoute": None, "rollbackRoute": None,
            "exited": [], "launchInstanceId": None,
            "endpoints": {"predecessor": None, "successor": None},
        }
        if previous is not None and predecessor is not None:
            prior_role = "successor" if previous["phase"] == "complete" else "predecessor"
            journal._document["endpoints"]["predecessor"] = previous["endpoints"][prior_role]
        journal._guard()
        installation.require_current(environment=environment)
        if previous is not None and _archived_terminal(installation, environment=environment) != previous:
            raise PermissionError("watch archived transaction changed before the next transaction")
        _persist(installation.run_root / _FILE, journal._document)
        return journal

    @classmethod
    def recover(
        cls, installation: WatchInstallation, guard: CutoverLock, *,
        environment: Mapping[str, str] | None = None,
        expected_document: dict | None = None,
    ) -> WatchJournal:
        journal = cls(installation, guard, {}, environment=environment)
        journal._guard()
        installation.require_current(environment=environment)
        journal._document = _read_identity(installation, environment=environment)
        if expected_document is not None and journal._document != expected_document:
            raise PermissionError("watch recovery journal changed before controller rotation")
        document = dict(journal._document)
        document.update(controller=_controller(), authorization=secrets.token_hex(32))
        journal._write(document)
        return journal

    @classmethod
    def recover_cleanup(
        cls, installation: WatchInstallation, guard: CutoverLock, *,
        environment: Mapping[str, str] | None = None,
    ) -> WatchJournal:
        """Rotate controller custody for passive cleanup, leaving recovery pending."""
        journal = cls(installation, guard, {}, environment=environment)
        journal._guard()
        installation.require_cleanup(environment=environment)
        journal._document = _read_identity(installation, environment=environment)
        _cleanup_document(installation, journal._document)
        document = dict(journal._document)
        document.update(controller=_controller(), authorization=secrets.token_hex(32))
        journal._write_cleanup(document)
        return journal

    @property
    def transaction_id(self) -> str:
        return self._document["transactionId"]

    @property
    def authorization(self) -> str:
        return self._document["authorization"]

    def _current(self) -> dict:
        self.installation.require_current(environment=self.environment)
        return self._current_identity()

    def _current_identity(self) -> dict:
        self._guard()
        current = _read_identity(self.installation, environment=self.environment)
        if current != self._document:
            raise PermissionError("watch owner journal changed; reconcile before continuing")
        return current

    def _write(self, document: dict) -> None:
        self.installation.require_current(environment=self.environment)
        self._persist_current(document)

    def _write_cleanup(self, document: dict) -> None:
        self.installation.require_cleanup(environment=self.environment)
        _cleanup_document(self.installation, document)
        self._persist_current(document)

    def _persist_current(self, document: dict) -> None:
        self._guard()
        self.installation.read_identity(environment=self.environment)
        raw = _private_bytes(self.installation.run_root / _FILE)
        if raw is None or _json_object(raw) != self._document:
            raise PermissionError("watch owner journal changed; reconcile before continuing")
        document["revision"] += 1
        _persist(self.installation.run_root / _FILE, document)
        self._document = document

    def archive(self) -> Path:
        """Retain terminal custody durably before a later transaction can replace it."""
        document = self._current()
        if document["phase"] not in {"complete", "rolled-back"}:
            raise PermissionError("watch pending transaction cannot be archived")
        if document["phase"] == "complete":
            _target_selection(self.installation, document)
        else:
            require_rollback_selection(self.installation, document)
        path = self.installation.run_root / "watch-owner-history" / f'{self.transaction_id}.json'
        raw = _private_bytes(path)
        if raw is not None:
            if _terminal_custody(_json_object(raw)) != _terminal_custody(document):
                raise PermissionError("watch transaction archive already has different custody")
            confirm_private(path)
        else:
            self._current()
            _persist(path, document, replace=False)
        return path

    def bind_endpoint(
        self, role: str, endpoint: tuple[str, int, str], *,
        read_health: Callable[[], Mapping[str, object]],
    ) -> None:
        """Bind an authenticated endpoint to an already attributable exact instance."""
        document = self._current()
        if (
            role not in {"predecessor", "successor"} or document[role] is None
            or role in document["exited"] or document["phase"] in {"complete", "rolled-back"}
        ):
            raise PermissionError("watch endpoint binding lacks an attributable instance")
        value = endpoint_value(endpoint)
        if document["endpoints"][role] not in (None, value):
            raise PermissionError("watch endpoint custody cannot be replaced")
        other = document["endpoints"]["successor" if role == "predecessor" else "predecessor"]
        if other is not None and (other["host"], other["port"]) == endpoint[:2]:
            raise PermissionError("watch transaction endpoints are not distinct")
        instance = WatchInstance.from_evidence(
            document[role], self.installation, environment=self.environment,
        )
        instance.require_live(
            self.installation, read_health=read_health, environment=self.environment,
        )
        if role == "successor" and document["targetRoute"] is not None:
            route = _json_object(bytes.fromhex(document["targetRoute"]))["active"]
            if route["bind"] != endpoint[0] or route["port"] != endpoint[1]:
                raise PermissionError("watch route differs from its authenticated endpoint")
        if role == "predecessor" and document["prior"]["route"] is not None:
            route = _json_object(bytes.fromhex(document["prior"]["route"]))["active"]
            if route["bind"] != endpoint[0] or route["port"] != endpoint[1]:
                raise PermissionError("watch route differs from its authenticated endpoint")
        document["endpoints"][role] = value
        self._write(document)

    def endpoint(self, role: str) -> tuple[WatchInstance, tuple[str, int, str]]:
        document = self._current()
        if (
            role not in {"predecessor", "successor"} or document[role] is None
            or document["endpoints"][role] is None or role in document["exited"]
        ):
            raise PermissionError("watch endpoint lacks live attributable custody")
        return WatchInstance.from_evidence(
            document[role], self.installation, environment=self.environment,
        ), endpoint_tuple(document["endpoints"][role])

    def launched_successor(self) -> tuple[WatchInstance, tuple[str, int, str]]:
        return read_launch(self.installation, self._current(), environment=self.environment)

    def cleanup_successor(self) -> tuple[WatchInstance, tuple[str, int, str]]:
        document = self._current_identity()
        self.installation.require_cleanup(environment=self.environment)
        _cleanup_document(self.installation, document)
        return read_launch(self.installation, document, environment=self.environment)

    def reserve_successor(self, instance_id: str) -> None:
        """Durably reserve the random child identity BEFORE attributable spawning."""
        document = self._current()
        if (
            document["phase"] != "prepared"
            or document["successor"] is not None or document["launchInstanceId"] is not None
            or not isinstance(instance_id, str) or str(UUID(instance_id)) != instance_id
            or UUID(instance_id).version != 4
            or (document["predecessor"] is not None
                and instance_id == document["predecessor"]["instanceId"])
        ):
            raise PermissionError("watch successor launch cannot be reserved")
        _cleanup_document(self.installation, document)
        document["launchInstanceId"] = instance_id
        self._write(document)

    def bind_successor(self, instance: WatchInstance, *, route: bytes) -> None:
        document = self._current()
        if (
            document["phase"] != "prepared"
            or document["successor"] is not None
        ):
            raise PermissionError("watch passive binding requires a prepared transaction")
        if instance.runtime.evidence() != document["target"]:
            raise PermissionError("watch passive runtime differs from completed target")
        if document["launchInstanceId"] not in {None, instance.instance_id}:
            raise PermissionError("watch passive differs from reserved launch")
        if (
            document["predecessor"] is not None and (
            instance.pid == document["predecessor"]["pid"]
            or instance.instance_id == document["predecessor"]["instanceId"]
            )
        ):
            raise PermissionError("watch passive must be a distinct native instance")
        _route(route, instance)
        document.update(successor=instance.evidence(), targetRoute=route.hex(), phase="passive")
        self._write(document)

    def bind_cleanup_successor(
        self, instance: WatchInstance, *, read_health: Callable[[], Mapping[str, object]],
        endpoint: tuple[str, int, str] | None = None,
    ) -> None:
        """Recover only the exact pre-reserved, never-promoted child after spawn."""
        document = self._current_identity()
        self.installation.require_cleanup(environment=self.environment)
        _cleanup_document(self.installation, document)
        if (
            document["phase"] != "prepared" or document["successor"] is not None
            or document["launchInstanceId"] != instance.instance_id
            or instance.runtime.evidence() != document["target"]
            or (document["predecessor"] is not None
                and instance.pid == document["predecessor"]["pid"])
        ):
            raise PermissionError("watch cleanup child lacks exact reserved launch identity")
        health = instance.require_live_identity(
            self.installation, read_health=read_health, environment=self.environment,
        )
        _passive_health(instance, health)
        document["successor"] = instance.evidence()
        if endpoint is not None:
            value = endpoint_value(endpoint)
            launched, launched_endpoint = read_launch(
                self.installation, document, environment=self.environment,
            )
            if launched != instance or launched_endpoint != endpoint:
                raise PermissionError("watch cleanup endpoint lacks exact launch custody")
            document["endpoints"]["successor"] = value
        self._write_cleanup(document)

    def record_cleanup_exit(self, reference: ProcessExitReference, *, timeout: float) -> None:
        document = self._current_identity()
        self.installation.require_cleanup(environment=self.environment)
        _cleanup_document(self.installation, document)
        instance = document["successor"]
        if instance is None or (
            reference.pid, reference.start_time
        ) != (instance["pid"], instance["startTime"]):
            raise PermissionError("watch cleanup exit lacks exact successor evidence")
        if not reference.wait(timeout):
            raise TimeoutError("watch passive exit remains unproven")
        if "successor" not in document["exited"]:
            document["exited"].append("successor")
            self._write_cleanup(document)

    def advance(self, phase: str) -> None:
        """Journal intent before control, without claiming completion of that control."""
        document = self._current()
        allowed = {
            "passive": "draining", "draining": "released",
            "released": "read-ready", "read-ready": "publishing",
        }
        if allowed.get(document["phase"]) != phase or document["exited"]:
            raise PermissionError("invalid watch owner transaction transition")
        document["phase"] = phase
        self._write(document)

    def commit(self, *, read_health: Callable[[], Mapping[str, object]] | None = None) -> None:
        document = self._current()
        metadata_only = document["predecessor"] is None and document["successor"] is None
        if metadata_only and document["launchInstanceId"] is not None:
            raise PermissionError("watch reserved successor launch remains unresolved")
        if document["phase"] != ("prepared" if metadata_only else "publishing"):
            raise PermissionError("watch owner is not ready to commit")
        if not metadata_only and document["launchInstanceId"] is not None and (
            publication_history.read(_json_object(bytes.fromhex(document["targetRoute"]))) is None
        ):
            raise PermissionError("watch reserved successor lacks captured native promotion history")
        if not metadata_only:
            if read_health is None:
                raise PermissionError("watch commit requires exact successor readiness")
            successor = WatchInstance.from_evidence(
                document["successor"], self.installation, environment=self.environment,
            )
            health = successor.require_live(
                self.installation, read_health=read_health, environment=self.environment,
            )
            if (
                health.get("instance") != successor.evidence()
                or health.get("writerHeld") is not True or health.get("readReady") is not True
                or health.get("effectsEnabled") is not False
            ):
                raise PermissionError("watch successor is not an effect-free read-ready writer")
        _target_selection(self.installation, document)
        document["phase"] = "committed"
        self._write(document)

    def rollback(self) -> None:
        document = self._current()
        if document["phase"] in {"committed", "complete", "rolled-back"}:
            raise PermissionError("committed watch owner recovery is forward-only")
        document["phase"] = "rolling-back"
        self._write(document)

    def restore_route(self, *, expected_active: Endpoint, deadline: float) -> Endpoint:
        return restore_route(self, expected_active=expected_active, deadline=deadline)

    def record_exit(self, role: str, reference: ProcessExitReference, *, timeout: float) -> None:
        record_exit(self, role, reference, timeout=timeout)

    def observe_exit(self, role: str) -> None:
        observe_exit(self, role)

    def observe_cleanup_exit(self) -> None:
        observe_cleanup_exit(self)

    def finish(self, *, read_health: Callable[[], Mapping[str, object]] | None = None) -> None:
        document = self._current()
        if document["phase"] not in {"committed", "rolling-back"}:
            raise PermissionError("watch transaction is not ready for completion")
        forward = document["phase"] == "committed"
        if forward:
            _target_selection(self.installation, document)
            role = "successor"
            if document["predecessor"] is not None and "predecessor" not in document["exited"]:
                raise PermissionError("watch predecessor retirement remains unproven")
        else:
            require_rollback_selection(self.installation, document)
            if document["launchInstanceId"] is not None and document["successor"] is None:
                raise PermissionError("watch reserved successor launch remains unresolved")
            role = "predecessor"
            if document["successor"] is not None and "successor" not in document["exited"]:
                raise PermissionError("watch successor exit remains unproven")
        if document[role] is not None:
            if read_health is None:
                raise PermissionError("watch completion requires exact active-owner health")
            instance = WatchInstance.from_evidence(
                document[role], self.installation, environment=self.environment,
            )
            health = instance.require_live(
                self.installation, read_health=read_health, environment=self.environment,
            )
            if (
                health.get("instance") != instance.evidence()
                or health.get("writerHeld") is not True or health.get("readReady") is not True
                or health.get("effectsEnabled") is not True
            ):
                raise PermissionError("watch transaction has no verified active writer")
        document["phase"] = "complete" if forward else "rolled-back"
        self._write(document)


def authorize_control(
    installation: WatchInstallation, instance: WatchInstance, *, operation: str,
    transaction_id: str, authorization: str,
    held_guard: CutoverLock | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Require capability plus native controller custody, without taking it over."""
    installation.require_current(environment=environment)
    document = _read_identity(installation, environment=environment)
    if (
        transaction_id != document["transactionId"] or not isinstance(authorization, str)
        or not hmac.compare_digest(authorization, document["authorization"])
        or instance.pid != os.getpid()
    ):
        raise PermissionError("watch lifecycle control lacks exact transaction authorization")
    _controller_held(installation, document["controller"], held_guard)
    role = next((
        role for role in ("predecessor", "successor")
        if document[role] == instance.evidence()
    ), None)
    if (
        role is None or document["phase"] not in _CONTROL_PHASES.get((role, operation), set())
        or role in document["exited"]
        or (operation == "resume"
            and (document["successor"] is not None or document["launchInstanceId"] is not None)
            and "successor" not in document["exited"])
    ):
        raise PermissionError("watch lifecycle operation is not authorized in this phase")
    if operation in {"activate", "retire"} and document["phase"] in {"committed", "complete"}:
        _target_selection(installation, document)
    if operation == "resume":
        require_rollback_selection(installation, document)
    instance.require_live(
        installation, read_health=lambda: {"instance": instance.evidence()}, environment=environment,
    )


def authorize_cleanup(
    installation: WatchInstallation, instance: WatchInstance, *,
    transaction_id: str, authorization: str,
    held_guard: CutoverLock | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Authorize only passive cleanup under exact journal and native custody."""
    installation.require_cleanup(environment=environment)
    document = _read_identity(installation, environment=environment)
    if (
        transaction_id != document["transactionId"] or not isinstance(authorization, str)
        or not hmac.compare_digest(authorization, document["authorization"])
        or instance.pid != os.getpid()
    ):
        raise PermissionError("watch passive cleanup lacks exact transaction authorization")
    _controller_held(installation, document["controller"], held_guard)
    _cleanup_document(installation, document)
    expected = document["successor"]
    if (
        "successor" in document["exited"]
        or (expected is not None and expected != instance.evidence())
        or (expected is None and (
            document["phase"] != "prepared"
            or document["launchInstanceId"] != instance.instance_id
            or document["target"] != instance.runtime.evidence()
            or (document["predecessor"] is not None
                and instance.pid == document["predecessor"]["pid"])
        ))
    ):
        raise PermissionError("watch passive cleanup instance is not attributable")
    if os.path.lexists(
        installation.run_root / f'watch-owner-launch-{document["transactionId"]}.json'
    ):
        launched, _ = read_launch(installation, document, environment=environment)
        if launched != instance:
            raise PermissionError("watch passive cleanup differs from durable native launch custody")
    instance.require_live_identity(
        installation, read_health=lambda: {"instance": instance.evidence()}, environment=environment,
    )
    installation.require_cleanup(environment=environment)
    _cleanup_document(installation, document)
