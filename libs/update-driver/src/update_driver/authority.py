"""Installation-scoped selection of the suite update execution owner."""

from __future__ import annotations

import math
import os
import re
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from single_instance_lease.operation import OperationLock, OperationLockedError
from ._directory import create_queue_directory
from .foundation_state import _publish

if TYPE_CHECKING:
    from .core import RequestSpec

LEGACY_SCHEMA_VERSION = 5
SCHEMA_VERSION = 6
CAPABILITY = "suite-update-authority/v1"
QUEUED_NATIVE_WRITER_CAPABILITY = "suite-queued-native-writer-fencing/v1"
_GUARD = b"copilot-extensions.update-authority/v1\n"
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_MARKETPLACE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}\Z")
_PLUGIN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_DIRECT_WRITER_BACKENDS = frozenset({
    "engine-native-install", "plugin-native-install", "manager-bootstrap",
    "manager-uninstall", "manager-self-install", "owning-plugin-provision",
})
_SUITE_BACKENDS = _DIRECT_WRITER_BACKENDS | frozenset({
    "engine", "manager", "engine-launch-update", "engine-stage-update",
    "engine-reconcile-plugins", "engine-dev-release", "manager-self-install",
})


class AuthorityError(ValueError):
    """Authority state or its selected installation is invalid."""


@dataclass(frozen=True)
class UpdateAuthority:
    mode: str = "standalone"
    project: str = ""
    configured: bool = False


def selection(mode: str, project: str = "") -> UpdateAuthority:
    if (
        mode not in ("standalone", "project") or not isinstance(project, str)
        or (mode == "standalone" and project)
        or (mode == "project" and _PROJECT.fullmatch(project) is None)
    ):
        raise AuthorityError("invalid update authority selection")
    return UpdateAuthority(mode, project, True)


def scope(context: str = "", *, cwd: str = "", durable: Path) -> str:
    if not isinstance(context, str) or "\0" in context or len(context) > 32768:
        raise AuthorityError("invalid update authority context")
    if not context:
        return ""
    path = Path(context)
    if not path.is_absolute() or len(path.parents) < 4:
        raise AuthorityError("update authority context must be an absolute installation receipt")
    try:
        for item in (path, *path.parents):
            info = item.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise AuthorityError("update authority context traverses a link")
        if not stat.S_ISREG(path.lstat().st_mode):
            raise AuthorityError("update authority context is not an ordinary receipt")
        path = path.resolve(strict=True)
    except OSError as error:
        raise AuthorityError("update authority context is unavailable") from error
    plugin, plugins, cell, marketplaces = path.parents[:4]
    if (
        path.name != "install.json" or plugins.name != "plugins"
        or marketplaces != durable / "marketplaces"
        or _MARKETPLACE.fullmatch(cell.name) is None or _PLUGIN.fullmatch(plugin.name) is None
    ):
        raise AuthorityError("update authority context is outside a canonical marketplace cell")
    return os.path.normcase(str(cell))


def guarded(root: Path) -> bool:
    path = root / "authority.guard"
    try:
        before = path.lstat()
    except FileNotFoundError:
        if path.is_symlink():
            raise AuthorityError("update authority guard is a dangling link")
        return False
    except OSError as error:
        raise AuthorityError("update authority guard cannot be inspected") from error
    if (
        not stat.S_ISREG(before.st_mode) or path.is_symlink() or before.st_size != len(_GUARD)
        or getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise AuthorityError("update authority guard is not an ordinary file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not os.path.samestat(before, opened):
                raise AuthorityError("update authority guard changed while opening")
            raw = stream.read(len(_GUARD) + 1)
            after = os.fstat(stream.fileno())
        if (
            raw != _GUARD or not os.path.samestat(opened, after)
            or not os.path.samestat(opened, path.lstat())
            or opened.st_size != after.st_size or opened.st_mtime_ns != after.st_mtime_ns
        ):
            raise AuthorityError("update authority guard changed or is malformed")
        return True
    except OSError as error:
        raise AuthorityError("update authority guard is unreadable") from error


def ensure_guard(root: Path) -> None:
    if guarded(root):
        return
    path = root / "authority.guard"
    temporary = root / (".authority-" + uuid.uuid4().hex + ".tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(_GUARD)
            stream.flush()
            os.fsync(stream.fileno())
        _publish(temporary, path)
        if not guarded(root):
            raise AuthorityError("update authority guard was not published")
    except OSError as error:
        raise AuthorityError("update authority guard could not be published") from error
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def validate(db) -> None:
    table = db.execute(
        "SELECT type FROM sqlite_master WHERE name='suite_authority'",
    ).fetchone()
    columns = db.execute("PRAGMA table_info(suite_authority)").fetchall()
    expected = [("scope", 1), ("mode", 0), ("project", 0)]
    if (
        table is None or table[0] != "table" or len(columns) != len(expected)
        or any(
            (row[1], row[5]) != expected[index] or row[2].upper() != "TEXT" or row[3] != 1
            for index, row in enumerate(columns)
        )
    ):
        raise AuthorityError("update authority table is malformed")
    for context, mode, project in db.execute("SELECT scope, mode, project FROM suite_authority"):
        if not isinstance(context, str) or "\0" in context or len(context) > 32768:
            raise AuthorityError("update authority scope is malformed")
        selection(mode, project)


def initialize(db) -> None:
    db.execute(
        "CREATE TABLE suite_authority ("
        "scope TEXT PRIMARY KEY NOT NULL, mode TEXT NOT NULL, project TEXT NOT NULL)"
    )
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def read(db, context: str) -> UpdateAuthority:
    row = db.execute(
        "SELECT mode, project FROM suite_authority WHERE scope=?", (context,),
    ).fetchone()
    return selection(row[0], row[1]) if row is not None else UpdateAuthority()


def write(db, context: str, selected: UpdateAuthority) -> None:
    db.execute(
        "INSERT INTO suite_authority(scope,mode,project) VALUES(?,?,?) "
        "ON CONFLICT(scope) DO UPDATE SET mode=excluded.mode,project=excluded.project",
        (context, selected.mode, selected.project),
    )


def suite_request(spec: RequestSpec) -> bool:
    return spec.backend in _SUITE_BACKENDS and not (
        spec.backend == "engine-launch-update" and "--no-update" in spec.argv
        and "--with-reconcile" not in spec.argv
    )


def _scope(context: str, *, root: Path, cwd: str = "") -> str:
    from . import core

    try:
        return scope(context, cwd=cwd, durable=root.parent)
    except (OSError, RuntimeError, AuthorityError) as error:
        raise core.StateError("update authority context is unavailable or invalid") from error


def _from_db(
    root: Path, selected_scope: str, db: sqlite3.Connection, *, allow_incomplete: bool = False,
) -> UpdateAuthority:
    from . import core

    try:
        marker = guarded(root)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
            if not marker:
                raise core.StateError("update authority guard is missing")
            selected = read(db, selected_scope)
            if (
                version == LEGACY_SCHEMA_VERSION and not allow_incomplete
                and selected.mode == "project"
            ):
                raise core.StateError("update authority requires direct-writer protocol upgrade")
            return selected
        if marker and not allow_incomplete:
            raise core.StateError("update authority transition is incomplete")
        return UpdateAuthority()
    except (OSError, AuthorityError) as error:
        raise core.StateError("update authority state is unavailable or invalid") from error


def current(*, root: Path | None = None, context: str = "", cwd: str = "") -> UpdateAuthority:
    """Inspect one installation's mode without creating or changing state."""
    from . import core

    root = core._root(root)
    try:
        marker = guarded(root)
        try:
            (root / "queue.sqlite3").lstat()
        except FileNotFoundError:
            if marker:
                raise core.StateError("update authority database is missing")
            return UpdateAuthority()
        with core._database(root, allow_pristine=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            selected_scope = (
                _scope(context, root=root, cwd=cwd)
                if version in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION) else ""
            )
            if version == LEGACY_SCHEMA_VERSION:
                selected = _from_db(root, selected_scope, db, allow_incomplete=True)
                if selected.mode == "project" and _legacy_project_reader(
                    root, selected_scope, selected, db,
                ):
                    return selected
            return _from_db(root, selected_scope, db)
    except (OSError, AuthorityError) as error:
        raise core.StateError("update authority state is unavailable or invalid") from error


def _wait_operation(path: Path, deadline: float, label: str) -> OperationLock:
    from . import core

    lock = OperationLock(path)
    while True:
        try:
            lock.acquire()
            return lock
        except OperationLockedError as error:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise core.StateError(f"{label} is busy; update authority was not changed") from error
            time.sleep(min(.05, remaining))


def _request_scopes(spec: RequestSpec, root: Path) -> tuple[str, ...]:
    from . import core

    context = dict(spec.context).get("COPILOT_EXTENSIONS_CONTEXT", "")
    if spec.backend in ("engine-native-install", "plugin-native-install") and spec.project:
        installation = Path(spec.project)
        marketplaces = root.parent / "marketplaces"
        try:
            resolved = installation.resolve() if installation.is_absolute() else installation
            physical_marketplaces = marketplaces.resolve()
        except (OSError, RuntimeError) as error:
            raise core.StateError("native installation authority root is unavailable") from error
        if installation.is_absolute() and installation.is_relative_to(marketplaces) and not (
            resolved.is_relative_to(physical_marketplaces)
        ):
            raise core.StateError("native installation authority root is redirected")
        if installation.is_absolute() and resolved.is_relative_to(physical_marketplaces):
            parts = resolved.relative_to(physical_marketplaces).parts
            if len(parts) < 3 or parts[1] != "plugins" or ".." in parts:
                raise core.StateError("native installation authority scope is invalid")
            receipt = marketplaces / parts[0] / "plugins" / parts[2] / "install.json"
            selected = _scope(str(receipt), root=root, cwd=spec.cwd)
            if context and _scope(context, root=root, cwd=spec.cwd) != selected:
                raise core.StateError("native installation authority context differs from its root")
            return (selected,)
    selected = _scope(context, root=root, cwd=spec.cwd)
    if spec.backend in _DIRECT_WRITER_BACKENDS and selected:
        return ("", selected)
    return (selected,)


def _choose_selection(selections: list[UpdateAuthority]) -> UpdateAuthority:
    for value in selections:
        if value.configured and value.mode == "project":
            return value
    return next((value for value in selections if value.configured), UpdateAuthority())


def _selected_for_request(
    root: Path, spec: RequestSpec, db: sqlite3.Connection | None = None,
    *, allow_legacy_project: bool = False,
):
    from . import core

    if db is None:
        if os.path.lexists(root / "queue.sqlite3"):
            with core._database(root) as existing:
                return _selected_for_request(
                    root, spec, existing, allow_legacy_project=allow_legacy_project,
                )
        return current(
            root=root, context=dict(spec.context).get("COPILOT_EXTENSIONS_CONTEXT", ""),
            cwd=spec.cwd,
        )
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        _from_db(root, "", db, allow_incomplete=True)
        try:
            selected_scopes = _request_scopes(spec, root)
        except core.StateError:
            return None
    else:
        selected_scopes = ("",)
    selections = [
        _from_db(
            root, value, db,
            allow_incomplete=allow_legacy_project and version == LEGACY_SCHEMA_VERSION,
        )
        for value in selected_scopes
    ]
    return _choose_selection(selections)


def _refusal_for_selection(
    spec: RequestSpec, selected: UpdateAuthority | None, *, legacy_native: bool = False,
) -> str | None:
    if selected is None:
        return "suite update authority context is unavailable or invalid"
    if selected.configured and spec.backend in _DIRECT_WRITER_BACKENDS:
        if selected.mode == "project":
            return "direct installation is disabled by project update authority"
        if legacy_native and not spec.authority:
            return None
    if not selected.configured and spec.authority in ("", "standalone"):
        return None
    if selected.mode == spec.authority and (
        selected.mode != "project" or selected.project == spec.project
    ):
        return None
    return "suite update authority does not admit this request"


def refusal(root: Path, spec: RequestSpec, *, db: sqlite3.Connection | None = None) -> str | None:
    if not suite_request(spec):
        return None
    return _refusal_for_selection(
        spec, _selected_for_request(root, spec, db, allow_legacy_project=db is not None),
        legacy_native=(
            db is not None
            and db.execute("PRAGMA user_version").fetchone()[0] == LEGACY_SCHEMA_VERSION
        ),
    )


def _legacy_project_reader(root: Path, selected_scope: str, selected: UpdateAuthority, db) -> bool:
    from . import core
    from .participant import (
        ENVIRONMENT_KEY, ParticipantError, borrowed_custody, participant, participant_environment,
    )

    try:
        inherited = participant_environment()
        custody = (
            participant(root=root, witness=inherited[ENVIRONMENT_KEY])
            if inherited else borrowed_custody(root=root)
        )
        if custody is None:
            return False
        try:
            custody.assert_owned()
            row = db.execute("SELECT * FROM requests WHERE id=?", (custody.request_id,)).fetchone()
            if row is None:
                return False
            request = core._request(row, root)
            if (
                request.state != "running" or request.started_at is None
                or not suite_request(request.spec)
                or request.spec.authority != "project" or request.spec.project != selected.project
                or selected_scope not in _request_scopes(request.spec, root)
                or _refusal_for_selection(request.spec, selected) is not None
            ):
                return False
            custody.assert_owned()
            return True
        finally:
            custody.close()
    except ParticipantError as error:
        raise core.StateError("update authority custody is unavailable or invalid") from error


@contextmanager
def submission(root: Path, spec: RequestSpec):
    from . import core

    if not suite_request(spec):
        yield spec
        return
    create_queue_directory(root)
    gate = _wait_operation(root / "authority.lock", time.monotonic() + 30, "update authority")
    try:
        selected = _selected_for_request(root, spec)
        if (
            selected is not None and selected.configured and selected.mode == "standalone"
            and spec.backend in _DIRECT_WRITER_BACKENDS and not spec.authority
        ):
            spec = replace(spec, authority="standalone")
        reason = _refusal_for_selection(spec, selected)
        if reason is not None:
            raise core.StateError(reason)
        yield spec
    finally:
        gate.release()


def change(
    mode: str, *, project: str = "", context: str = "", cwd: str = "",
    root: Path | None = None, timeout: float = 30,
    verify_capabilities: Callable[[float], bool] | None = None,
) -> UpdateAuthority:
    """Switch only after current execution and pending suite requests settle."""
    from . import core

    root = core._root(root)
    try:
        selected = selection(mode, project)
        selected_scope = _scope(context, root=root, cwd=cwd)
    except AuthorityError as error:
        raise core.StateError("update authority selection is invalid") from error
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout) or timeout < 0 or not callable(verify_capabilities)
    ):
        raise core.StateError("update authority requires a bounded wait and capability check")
    create_queue_directory(root)
    try:
        if guarded(root) and not (root / "queue.sqlite3").exists():
            raise core.StateError("update authority database is missing")
    except (OSError, AuthorityError) as error:
        raise core.StateError("update authority state is unavailable or invalid") from error
    deadline = time.monotonic() + timeout

    def verify():
        try:
            capable = verify_capabilities(deadline)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise core.StateError("update authority capability check failed") from error
        if capable is not True or time.monotonic() > deadline:
            raise core.StateError("update authority capabilities are not established in time")

    while True:
        execution = _wait_operation(root / "execution.lock", deadline, "installation execution")
        pending = False
        try:
            core._reconcile_foundation(root, execution)
            gate = _wait_operation(root / "authority.lock", deadline, "update authority")
            try:
                with core._database(root, create=True):
                    pass
                database = root / "queue.sqlite3"
                identity = database.lstat()
                with core._database(root, writable=True, expected_identity=identity) as db:
                    db.execute("BEGIN IMMEDIATE")
                    core._upgrade_schema(db, root, execution)
                    version = db.execute("PRAGMA user_version").fetchone()[0]
                    previous = _from_db(root, selected_scope, db, allow_incomplete=True)
                    if previous == selected and version == SCHEMA_VERSION:
                        db.commit()
                        return previous
                    if core._has_fence(db, root):
                        raise core.InterruptedError(
                            "update authority cannot change while execution is unsettled or "
                            "pending execution evidence is unconfirmed",
                        )
                    requests = [
                        core._request(row, root) for row in db.execute(
                            "SELECT * FROM requests WHERE state IN ('pending','running')",
                        )
                    ]
                    if any(request.state == "running" for request in requests):
                        raise core.InterruptedError("update authority cannot change while execution is unsettled")
                    try:
                        incomplete = version not in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION) and guarded(root)
                    except (OSError, AuthorityError) as error:
                        raise core.StateError("update authority state is unavailable or invalid") from error
                    late = [
                        request for request in requests
                        if incomplete and request.state == "pending" and suite_request(request.spec)
                    ]
                    if version == LEGACY_SCHEMA_VERSION:
                        def obsolete(request):
                            selections = [
                                read(db, value) for value in _request_scopes(request.spec, root)
                            ]
                            return (
                                any(value.mode == "project" for value in selections)
                                and _refusal_for_selection(request.spec, _choose_selection(selections)) is not None
                            )

                        late.extend(
                            request for request in requests
                            if request.state == "pending" and suite_request(request.spec)
                            and obsolete(request)
                        )
                    verified = False
                    if late:
                        if any(
                            request.started_at is not None or request.finished_at is not None
                            or request.boot_id is not None for request in late
                        ):
                            raise core.InterruptedError(
                                "pending suite request carries unconfirmed execution evidence",
                            )
                        verify()
                        verified = True
                        execution.assert_owned()
                        gate.assert_owned()
                        core._assert_database_identity(database, identity)
                        reason = (
                            "suite update refused during direct-writer authority upgrade"
                            if version == LEGACY_SCHEMA_VERSION
                            else "suite update refused after an interrupted authority switch"
                        )
                        for request in late:
                            db.execute(
                                """UPDATE requests SET state='completed',exit_code=3,error=?,
                                finished_at=? WHERE id=? AND state='pending'""",
                                (reason, time.time(), request.id),
                            )
                        late_ids = {request.id for request in late}
                        requests = [request for request in requests if request.id not in late_ids]
                    pending = any(
                        request.state == "pending" and (
                            version != SCHEMA_VERSION
                            or (suite_request(request.spec) and selected_scope in _request_scopes(request.spec, root))
                        ) for request in requests
                    )
                    if pending:
                        execution.assert_owned()
                        gate.assert_owned()
                        core._assert_database_identity(database, identity)
                        db.commit()
                    else:
                        if not verified:
                            verify()
                        try:
                            execution.assert_owned()
                            gate.assert_owned()
                            ensure_guard(root)
                            if version == LEGACY_SCHEMA_VERSION:
                                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                            elif version != SCHEMA_VERSION:
                                initialize(db)
                            write(db, selected_scope, selected)
                            core._validate_db(db)
                            core._assert_database_identity(database, identity)
                            db.commit()
                            core._assert_database_identity(database, identity)
                            return _from_db(root, selected_scope, db)
                        except AuthorityError as error:
                            raise core.StateError("update authority could not be changed") from error
            finally:
                gate.release()
        finally:
            execution.release()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise core.StateError("pending suite updates must settle before changing authority")
        time.sleep(min(.05, remaining))
