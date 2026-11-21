"""Cold-only source layout and kernel exclusion for participating new binaries."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager, nullcontext
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Protocol, TypeVar, cast

from ..indexing.change_hints import _identity
from ..indexing.point_hints import PointBinding

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig

    from .multi_model_store import MultiModelStore

F = TypeVar("F", bound=Callable[..., Any])
PointScope = tuple[str, str, str, PointBinding]


class _ValidationTable(Protocol):
    def search(self) -> Any: ...


LAYOUT = "source-aware-v1"
MARKER = ".source-layout.json"
LOCK = ".source-writer.lock"
FTS_LOCK = ".source-fts.lock"
_domains: dict[tuple[int, int, str | None], SourceDomain] = {}
_registry = threading.Lock()
_child_guards = []
log = logging.getLogger(__name__)

def _open_receipt(path: Path) -> BinaryIO:
    """Keep an atomic snapshot readable without preventing its replacement."""
    if os.name != "nt":
        return path.open("rb")
    ctypes = import_module("ctypes")
    msvcrt = import_module("msvcrt")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_DELETE; OPEN_EXISTING.
    handle = create(str(path), 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        raise OSError(0, ctypes.FormatError(code), str(path), code)
    try:
        descriptor = msvcrt.open_osfhandle(
            handle, os.O_RDONLY | getattr(os, "O_BINARY") | getattr(os, "O_NOINHERIT"),
        )
    except OSError:
        close(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except OSError:
        os.close(descriptor)
        raise


def _replace_receipt(source: Path, target: Path) -> None:
    if os.name != "nt" or not target.exists():
        os.replace(source, target)
        return
    ctypes = import_module("ctypes")
    from ctypes import wintypes

    # MoveFileExW (os.replace) rejects an open destination even with delete sharing.
    # ReplaceFileW preserves the old snapshot handles and the destination's ACL.
    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID,
    )
    replace.restype = wintypes.BOOL
    if not replace(str(target), str(source), None, 0, None, None):
        code = ctypes.get_last_error()
        raise OSError(0, ctypes.FormatError(code), str(source), code, str(target))


def kernel_lock(path: Path) -> BinaryIO:
    stream = path.open("a+b")
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            msvcrt = import_module("msvcrt")

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl = import_module("fcntl")

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise SourceLayoutError("source-aware domain busy") from None


class SourceLayoutError(RuntimeError):
    """Unprepared, changed, busy or incompatible isolated domain."""


def _point_scope_json(scope: PointScope | None) -> str | None:
    if scope is None:
        return None
    if not isinstance(scope, tuple) or len(scope) != 4 or type(scope[3]) is not PointBinding:
        raise SourceLayoutError("point domain requires an exact typed point scope")
    source, family, account, binding = scope
    try:
        for name, value in (("source", source), ("canonical_family", family), ("account_id", account)):
            _identity(value, name)
        if source == "all":
            raise ValueError("point domain requires an exact source")
    except ValueError as exc:
        raise SourceLayoutError("point domain requires an exact typed point scope") from exc
    return json.dumps({
        "source": source, "canonical_family": family, "account_id": account,
        "binding": binding.to_dict(),
    }, sort_keys=True)


def selected_layout() -> str | None:
    value = os.environ.get("AGENT_INDEX_STORE_LAYOUT")
    if value not in (None, "", LAYOUT):
        raise SourceLayoutError("unsupported store layout")
    return value or None


def _versions(db: Any) -> dict[str, int]:
    return {name: db.open_table(name).version for name in db.list_tables().tables}


def _validate_point_models(value: object) -> None:
    if (not isinstance(value, dict) or set(value) != {"protocol", "content_table", "models"}
            or type(value["protocol"]) is not int or value["protocol"] != 1
            or not isinstance(value["models"], dict)):
        raise SourceLayoutError("invalid point model binding")
    try:
        _identity(value["content_table"], "content_table")
        tables = {value["content_table"]}
        for model_id, model in value["models"].items():
            _identity(model_id, "model_id")
            if (not isinstance(model, dict) or set(model) != {"table_name", "dim"}
                    or type(model["dim"]) is not int or model["dim"] <= 0):
                raise ValueError("invalid model")
            _identity(model["table_name"], "table_name")
            if model["table_name"] in tables:
                raise ValueError("duplicate table")
            tables.add(model["table_name"])
    except (TypeError, ValueError) as exc:
        raise SourceLayoutError("invalid point model binding") from exc


def _configured_point_models(config: IndexConfig) -> dict[str, Any]:
    if any(key != profile.model_id for key, profile in config.model_profiles.items()):
        raise SourceLayoutError("point-domain bootstrap requires distinct configured tables")
    value = {
        "protocol": 1, "content_table": config.content_table,
        "models": {
            key: {"table_name": profile.table_name, "dim": profile.dim}
            for key, profile in config.model_profiles.items()
        },
    }
    try:
        _validate_point_models(value)
    except SourceLayoutError as exc:
        raise SourceLayoutError("point-domain bootstrap requires distinct configured tables") from exc
    return value


def _refuse_marked_legacy(store: Any) -> None:
    path = getattr(store, "_source_path", None)
    if path is None:
        content = getattr(store, "_content", store)
        uri = getattr(getattr(content, "_db", None), "uri", None)
        path = uri if isinstance(uri, (str, Path)) else getattr(store, "_db_path", None)
    if isinstance(path, (str, Path)) and (Path(path) / MARKER).exists():
        raise SourceLayoutError("legacy handle refuses a newly selected source-aware domain")


class SourceDomain:
    def __init__(self, path: Path, *, point_scope: PointScope | None = None) -> None:
        self.path = path
        self._point_scope = _point_scope_json(point_scope)
        self._mutex = threading.RLock()
        self._local = threading.local()

    def _require_legacy_access(self) -> None:
        if self._point_scope is not None:
            raise SourceLayoutError("point-domain reads and application are not admitted")

    def _require_read_access(self, point_scope: PointScope | None) -> None:
        if point_scope is None:
            self._require_legacy_access()
        elif self._point_scope is None or _point_scope_json(point_scope) != self._point_scope:
            raise SourceLayoutError("point read scope differs from its selected domain")

    @property
    def writing(self) -> bool:
        return bool(getattr(self._local, "depth", 0))

    @contextmanager
    def exclusion(self) -> Iterator[None]:
        if getattr(self._local, "operation", False):
            yield
            return
        if not self._mutex.acquire(blocking=False):
            raise SourceLayoutError("source-aware domain busy")
        stream = None
        guard = None
        previous = getattr(self._local, "exclusion_owned", False)
        try:
            if (self.path / MARKER).exists() and not all(
                (self.path / name).is_file() for name in (LOCK, FTS_LOCK)
            ):
                raise SourceLayoutError("source-aware ownership file is missing")
            stream = kernel_lock(self.path / LOCK)
            guard = kernel_lock(self.path / FTS_LOCK)
            self._local.fts_guard = guard
            self._local.exclusion_owned = True
            yield
        finally:
            self._local.exclusion_owned = previous
            current = getattr(self._local, "fts_guard", None)
            if current is not None:
                current.close()
            if guard is not None:
                guard.close()
            self._local.fts_guard = None
            if stream is not None:
                stream.close()  # Closing releases the kernel lock, including after errors.
            self._mutex.release()

    @contextmanager
    def operation(self, db: Any, *, point_scope: PointScope | None = None) -> Iterator[None]:
        """Hold existing exclusion across independently committed reads/writes."""
        self._require_read_access(point_scope)
        if self.writing or getattr(self._local, "operation", False):
            self.receipt()
            yield
            return
        with self.exclusion():
            value = self.receipt()
            if (value.get("fts_pending") or "reconciliation" in value or value.get("state") != "clean"
                    or value["versions"] != _versions(db)):
                raise SourceLayoutError("source projection incomplete or changed")
            self._local.operation = True
            try:
                yield
            finally:
                self._local.operation = False

    def receipt(self) -> dict[str, Any]:
        duplicate = False

        def fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            nonlocal duplicate
            result: dict[str, Any] = {}
            for key, item in pairs:
                duplicate |= key in result
                result[key] = item
            return result

        try:
            with _open_receipt(self.path / MARKER) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise SourceLayoutError("invalid source-layout receipt")
            value = json.loads(raw, object_pairs_hook=fields)
        except (OSError, ValueError) as exc:
            raise SourceLayoutError("source-aware layout not initialized") from exc
        if duplicate:
            raise SourceLayoutError("invalid source-layout receipt: duplicate fields")
        self._validate_receipt(value)
        return cast("dict[str, Any]", value)

    def _validate_receipt(self, value: object) -> None:
        if (not isinstance(value, dict) or value.get("layout") != LAYOUT
                or type(value.get("protocol")) is not int
                or value["protocol"] != (2 if self._point_scope is not None else 1)
                or value.get("state") not in ("clean", "dirty")
                or not isinstance(value.get("versions"), dict)):
            raise SourceLayoutError("incompatible source-layout receipt")
        if (self._point_scope is not None
                and json.dumps(value.get("point_scope"), sort_keys=True) != self._point_scope):
            raise SourceLayoutError("point-domain scope differs from its retained binding")
        if self._point_scope is not None and "point_models" in value:
            _validate_point_models(value["point_models"])

    def save(self, value: dict[str, Any]) -> None:
        if self._point_scope is not None and not getattr(self._local, "exclusion_owned", False):
            self._validate_receipt(value)
            with self.exclusion():
                self._save_receipt(value)
        else:
            self._save_receipt(value)

    def _save_receipt(self, value: dict[str, Any]) -> None:
        self._validate_receipt(value)
        current = self.receipt() if (self.path / MARKER).exists() else {}
        if self._point_scope is not None:
            retained = current.get("point_models")
            selected = value.get("point_models")
            if retained is not None and selected != retained:
                raise SourceLayoutError("point model binding cannot be changed or removed")
            if retained is None and selected is not None and (
                not self.writing or current.get("versions") != {}
                or _versions(self._local.db)
            ):
                raise SourceLayoutError("point model binding requires an empty owning writer")
        payload = json.dumps(value, sort_keys=True)
        logical = value.get("logical_projection")
        if (len(payload.encode("utf-8")) > 65536 and isinstance(logical, dict)
                and "bucket_generations" in logical):
            value = {**value, "logical_projection": {
                key: item for key, item in logical.items() if key != "bucket_generations"
            }}
            payload = json.dumps(value, sort_keys=True)
            log.warning("Bucket generations exceed the receipt budget; retaining table coverage")
        if len(payload.encode("utf-8")) > 65536 and "logical_projection" in value:
            value = {key: item for key, item in value.items() if key != "logical_projection"}
            payload = json.dumps(value, sort_keys=True)
            log.warning("Logical projection metadata exceeds the receipt budget; retaining physical coverage")
        if len(payload.encode("utf-8")) > 65536:
            raise SourceLayoutError("source-layout receipt exceeds its size limit")
        temporary = self.path / f".source-layout-{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _replace_receipt(temporary, self.path / MARKER)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def read(self, db: Any, *, point_scope: PointScope | None = None) -> Iterator[None]:
        self._require_read_access(point_scope)
        if self.writing or getattr(self._local, "reading", False):
            self.receipt()
            yield
            return
        with self.exclusion():
            value = self.receipt()
            if (value.get("fts_pending") or "reconciliation" in value or value.get("state") != "clean"
                    or value["versions"] != _versions(db)):
                raise SourceLayoutError("source projection incomplete or changed")
            self._local.reading = True
            try:
                yield
            finally:
                self._local.reading = False

    def effect(self, operation: str, *, physical_only: str | None = None) -> None:
        """Persist before an irreversible step; surviving rows cannot prove no lost members."""
        if not self.writing:
            raise SourceLayoutError("effect requires participating writer ownership")
        if physical_only is None:
            self._local.logical_effect = True
        else:
            self._local.physical_only.add(physical_only)
        self._persist_effect(operation)

    def _persist_effect(self, operation: str) -> None:
        value = self.receipt()
        value.pop("reconciliation", None)
        kind, _, table = operation.partition(":")
        if kind in ("content-upsert", "canonical-delete", "content-compact"):
            value.setdefault("fts_coverage", {}).pop(table, None)
        value.update(uncertain_effect=True, operation=operation)
        self._local.effect_sequence += 1
        self.save(value)

    def _table_identity(self, table_name: str) -> dict[str, Any] | None:
        from .logical_projection import physical_tables

        return physical_tables(
            self.path, {table_name: self._local.db.open_table(table_name).version},
        )

    @contextmanager
    def bucket_mutation(
        self, table_name: str, operation: str, sources: Iterable[str] | None,
    ) -> Iterator[None]:
        """Qualify only a contiguous, owner-described table transition."""
        from .bucket_projection import MAX_BUCKETS, mutation_buckets

        if not self.writing:
            raise SourceLayoutError("bucket mutation requires participating writer ownership")
        buckets = mutation_buckets(sources)
        if buckets is None:
            self.effect(operation)
            yield
            return
        before = self._table_identity(table_name)
        self._persist_effect(operation)
        yield
        after = self._table_identity(table_name)
        if before is not None and before == after:
            return
        changes = self._local.bucket_changes.setdefault(table_name, set())
        if changes is not None:
            changes.update(buckets)
            if len(changes) > MAX_BUCKETS:
                self._local.bucket_changes[table_name] = None
                log.warning("Transaction bucket budget exceeded; retaining whole-table invalidation")
        if (before is not None and after is not None
                and self._local.bucket_witness.get(table_name) == before[table_name]):
            self._local.bucket_witness[table_name] = after[table_name]

    @contextmanager
    def physical_maintenance(self, table_name: str, operation: str) -> Iterator[None]:
        """Exempt only the witnessed transition of this maintenance operation."""
        if not self.writing:
            raise SourceLayoutError("maintenance requires participating writer ownership")

        before = self._table_identity(table_name)
        self.effect(operation, physical_only=table_name)
        yield
        after = self._table_identity(table_name)
        if (before is not None and after is not None
                and self._local.physical_witness.get(table_name) == before[table_name]):
            self._local.physical_witness[table_name] = after[table_name]
        if (before is not None and after is not None
                and self._local.bucket_witness.get(table_name) == before[table_name]):
            self._local.bucket_witness[table_name] = after[table_name]

    def cover_fts(self, content: Any) -> None:
        if not self.writing:
            raise SourceLayoutError("FTS coverage requires owning writer")
        value = self.receipt()
        if value.get("fts_pending"):
            raise SourceLayoutError("FTS writer ownership is unsettled")
        table = content._get_or_create_table()
        value.setdefault("fts_coverage", {})[content._table_name] = table.version
        self.save(value)

    def require_fts(self, content: Any) -> bool:
        table = content._get_or_create_table()
        version = self.receipt().get("fts_coverage", {}).get(content._table_name)
        if version != table.version:
            raise SourceLayoutError("source-aware FTS is stale; refresh through owning writer")
        if table.count_rows() == 0:
            return False
        if not content._durable_fts_index_exists(table):
            raise SourceLayoutError("covered source-aware FTS index is missing")
        content._fts_available = True
        return True

    @contextmanager
    def fts_handoff(self, *, table_name: str | None = None) -> Iterator[str]:
        """Pending intent closes the pre-spawn gap; child guard closes parent-death gap."""
        maintenance = (
            self.physical_maintenance(table_name, "fts-builder")
            if table_name is not None else nullcontext()
        )
        with maintenance:
            if table_name is None:
                self.effect("fts-builder")
            nonce = uuid.uuid4().hex
            value = self.receipt()
            value["fts_pending"] = nonce
            self.save(value)
            self._local.fts_guard.close()
            self._local.fts_guard = None
            try:
                yield nonce
            finally:
                self._local.fts_guard = kernel_lock(self.path / FTS_LOCK)

    def seal_reconciliation(self, owner: str, token: str) -> None:
        """Retain an owner's completed native phase, never an uncertain effect."""
        from .logical_projection import physical_tables

        if not self.writing or not owner or not token:
            raise SourceLayoutError("reconciliation seal requires owning writer and retained intent")
        value = self.receipt()
        physical = physical_tables(self.path, _versions(self._local.db))
        if value.get("fts_pending") or physical is None:
            raise SourceLayoutError("reconciliation requires settled native ownership")
        value["reconciliation"] = {
            "owner": owner, "token": token, "physical": physical,
        }
        # Older participating validators must refuse even an empty native phase.
        value["uncertain_effect"] = True
        self._local.effect_sequence += 1
        self.save(value)

    def write(self, db: Any) -> AbstractContextManager[None]:
        return self._write(db)

    def reconcile(
        self, db: Any, *, owner: str, token: str, point_scope: PointScope | None = None,
    ) -> AbstractContextManager[None]:
        """Resume only an exact sealed phase; arbitrary dirty stores still refuse."""
        return self._write(db, reconciliation=(owner, token), point_scope=point_scope)

    @contextmanager
    def _write(
        self, db: Any, *, reconciliation: tuple[str, str] | None = None,
        point_scope: PointScope | None = None,
    ) -> Iterator[None]:
        self._require_read_access(point_scope)
        if self.writing:
            self.receipt()
            if reconciliation is not None:
                raise SourceLayoutError("reconciliation requires outer source ownership")
            sequence = self._local.effect_sequence
            self._local.depth += 1
            try:
                yield
            except BaseException:
                if self._local.effect_sequence != sequence:
                    self._local.failed_effect = True
                raise
            finally:
                self._local.depth -= 1
            return
        with self.exclusion():
            value = self.receipt()
            versions = _versions(db)
            from .logical_projection import commit_projection, physical_tables

            before = physical_tables(self.path, versions)
            if reconciliation is None:
                if (value.get("fts_pending") or "reconciliation" in value or value.get("state") != "clean"
                        or value["versions"] != versions):
                    raise SourceLayoutError("source projection requires explicit recovery")
            else:
                seal = value.get("reconciliation")
                if (value.get("fts_pending") or value.get("state") != "dirty"
                        or not isinstance(seal, dict)
                        or set(seal) != {"owner", "token", "physical"}
                        or (seal["owner"], seal["token"]) != reconciliation
                        or before is None or seal["physical"] != before):
                    raise SourceLayoutError("source reconciliation seal is missing or changed")
                # Recovery cannot reconstruct the interrupted writer's bucket witnesses.
                before = None
            value["state"] = "dirty"
            self.save(value)
            self._local.depth = 1
            self._local.db = db
            self._local.logical_effect = False
            self._local.physical_only = set()
            self._local.physical_witness = dict(before or {})
            self._local.bucket_changes = {}
            self._local.bucket_witness = dict(before or {})
            self._local.effect_sequence = 0
            self._local.failed_effect = False
            try:
                yield
                if self._local.failed_effect:
                    raise SourceLayoutError("source writer retained a failed nested effect")
                if self.receipt().get("fts_pending"):
                    raise SourceLayoutError("FTS writer ownership is unsettled")
                value = self.receipt()
                value["versions"] = _versions(db)
                value["state"] = "clean"
                value.pop("uncertain_effect", None)
                value.pop("operation", None)
                value.pop("reconciliation", None)
                after = physical_tables(self.path, value["versions"])
                preserved = {
                    name for name in self._local.physical_only - self._local.bucket_changes.keys()
                    if after is not None and after.get(name) == self._local.physical_witness.get(name)
                } if not self._local.logical_effect else set()
                scoped = {
                    name: buckets for name, buckets in self._local.bucket_changes.items()
                    if buckets is not None and after is not None
                    and after.get(name) == self._local.bucket_witness.get(name)
                } if not self._local.logical_effect else {}
                logical = commit_projection(value, before, after, physical_only=preserved, scoped=scoped)
                self.save(value)
                from .mutation_result import record_write
                record_write(self, logical, after, self.receipt)
            finally:
                self._local.depth = 0
                self._local.db = None
                self._local.logical_effect = False
                self._local.physical_only = set()
                self._local.physical_witness = {}
                self._local.bucket_changes = {}
                self._local.bucket_witness = {}
                self._local.effect_sequence = 0
                self._local.failed_effect = False


def domain_for(
    path: str | Path, *, point_scope: PointScope | None = None,
) -> SourceDomain | None:
    """Open one exact receipt selection; ordinary callers remain protocol-1-only."""
    scope = _point_scope_json(point_scope)
    path = Path(path).resolve()
    marker = path / MARKER
    if selected_layout() != LAYOUT:
        if marker.exists() or scope is not None:
            raise SourceLayoutError("source-aware store refuses unselected binary mode")
        return None
    if not marker.is_file():
        raise SourceLayoutError("initialize a fresh isolated source-aware store explicitly")
    info = path.stat()
    identity = (info.st_dev, info.st_ino, scope)
    with _registry:
        if identity not in _domains:
            _domains[identity] = SourceDomain(path, point_scope=point_scope)
        result = _domains[identity]
    result.receipt()
    return result


def initialize_source_layout(
    path: str | Path, *, point_scope: PointScope | None = None,
) -> dict[str, Any]:
    """Writer bootstrap only; no legacy adoption, transfer, backfill or offline assertion."""
    if selected_layout() != LAYOUT:
        raise SourceLayoutError("source-aware layout must be explicitly selected")
    domain = SourceDomain(Path(path).resolve(), point_scope=point_scope)
    receipt: dict[str, Any] = {
        "layout": LAYOUT, "protocol": 2 if point_scope is not None else 1,
        "state": "clean", "domain": uuid.uuid4().hex, "versions": {},
    }
    if domain._point_scope is not None:
        receipt["point_scope"] = json.loads(domain._point_scope)
    if len(json.dumps(receipt).encode("utf-8")) > 65536:
        raise SourceLayoutError("source-layout receipt exceeds its size limit")
    path = Path(path).resolve()
    if (path / MARKER).exists():
        domain.receipt()
    if path.exists() and not (path / MARKER).exists() and any(path.iterdir()):
        raise SourceLayoutError("cold initialization refuses nonempty or legacy state")
    path.mkdir(parents=True, exist_ok=True)
    with domain.exclusion():
        if (path / MARKER).exists():
            receipt = domain.receipt()
            if receipt.get("uncertain_effect"):
                raise SourceLayoutError(
                    "uncertain effect requires owner repair or fresh isolated reindex"
                )
            if "reconciliation" in receipt or receipt.get("state") != "clean":
                raise SourceLayoutError("source projection requires explicit recovery")
            return receipt
        if any(item.name not in (LOCK, FTS_LOCK) for item in path.iterdir()):
            raise SourceLayoutError("cold initialization refuses nonempty or legacy state")
        domain.save(receipt)
        return receipt


def initialize_source_store(
    config: IndexConfig, *, point_scope: PointScope | None = None,
) -> MultiModelStore:
    """Cold writer bootstrap, including its canonical content/model tables."""
    models = _configured_point_models(config) if point_scope is not None else None
    names = {config.content_table, *(p.table_name for p in config.model_profiles.values())}
    initialize_source_layout(config.lance_dir, point_scope=point_scope)
    from .content_store import _CONTENT_SCHEMA
    from .multi_model_store import MultiModelStore

    store = MultiModelStore(
        config.lance_dir, content_table=config.content_table, point_scope=point_scope,
    )
    domain = store._source_domain
    if domain is None:
        raise SourceLayoutError("source-aware store requires its configured domain")
    with domain.operation(store.content_store.db, point_scope=point_scope):
        receipt = domain.receipt()
        existing = receipt["versions"]
        retained = receipt.get("point_models")
        if models is not None and (
            (retained is not None and retained != models) or (retained is None and existing)
        ):
            raise SourceLayoutError("point-domain bootstrap model binding is missing or changed")
        if names <= existing.keys():
            for profile in config.model_profiles.values():
                store.register_model(profile)
            if point_scope is not None:
                if not store.content_store._get_or_create_table().schema.equals(_CONTENT_SCHEMA):
                    raise SourceLayoutError("point-domain bootstrap content schema changed")
                for profile in config.model_profiles.values():
                    owner = store.get_vector_table(profile.model_id)
                    if not owner._get_or_create_table().schema.equals(owner._schema):
                        raise SourceLayoutError("point-domain bootstrap model schema changed")
            return store
        if point_scope is not None and existing:
            raise SourceLayoutError("point-domain bootstrap refuses changed configured tables")
        # Scope is explicit only for schema bootstrap, never inherited by ordinary writers.
        with domain._write(store.content_store.db, point_scope=point_scope):
            if models is not None and retained is None:
                value = domain.receipt()
                value["point_models"] = models
                domain.save(value)
            cast("Callable[[], object]", store.content_store._get_or_create_table)()
            for profile in config.model_profiles.values():
                cast("Callable[[], object]", store.register_model(profile)._get_or_create_table)()
    return store


def validate_source_store(store: MultiModelStore) -> dict[str, Any]:
    """Explicit NEW-domain recovery: validate only; never guess or backfill provenance."""
    from .source_projection import PAGE_SIZE

    domain = store._source_domain
    if domain is None:
        raise SourceLayoutError("not a source-aware domain")
    db = store.content_store.db
    with domain.exclusion():
        receipt = domain.receipt()
        if "reconciliation" in receipt:
            raise SourceLayoutError("retained publication requires its owning reconciliation")
        if receipt.get("fts_pending"):
            raise SourceLayoutError("FTS writer ownership is unsettled")
        if receipt.get("uncertain_effect"):
            raise SourceLayoutError(
                "uncertain effect requires owner repair or fresh isolated reindex"
            )
        versions = _versions(db)
        if versions != receipt["versions"]:
            raise SourceLayoutError(
                "unexplained version drift requires retained intent or fresh reindex"
            )
        content = cast(
            "Callable[[], _ValidationTable]", store.content_store._get_or_create_table,
        )()
        for name in versions:
            table = db.open_table(name)
            if "vector" not in table.schema.names:
                continue
            if not {"source", "language"} <= set(table.schema.names):
                raise SourceLayoutError("incomplete source-aware schema")
            reader = table.search().select(["chunk_id", "source", "language"]).limit(None)
            reader = reader.to_batches(batch_size=PAGE_SIZE)
            try:
                for batch in reader:
                    rows = batch.to_pylist()
                    from .repo_filter import _sql_str

                    ids = ", ".join(f"'{_sql_str(r['chunk_id'])}'" for r in rows)
                    canonical = (
                        content.search().where(f"chunk_id IN ({ids})", prefilter=True)
                        .select(["chunk_id", "source", "language"]).limit(PAGE_SIZE + 1).to_list()
                    )
                    by_id = {r["chunk_id"]: r for r in canonical}
                    if len(by_id) != len(canonical) or any(
                        by_id.get(r["chunk_id"]) != r for r in rows
                    ):
                        raise SourceLayoutError("missing, orphaned or conflicting projection")
            finally:
                reader.close()
        if versions != _versions(db):
            raise SourceLayoutError("domain changed during validation")
        receipt.update(state="clean", versions=versions)
        domain.save(receipt)
        return receipt


def source_write(method: F) -> F:
    """Serialize actual participating mutation paths and latch uncertainty on failure."""
    @functools.wraps(method)
    def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        domain = getattr(self, "_source_domain", None)
        if domain is None:
            _refuse_marked_legacy(self)
            return method(self, *args, **kwargs)
        db = (self._content.db if hasattr(self, "_content")
              else getattr(self, "_source_db", getattr(self, "_db", None)))
        with domain.write(db):
            return method(self, *args, **kwargs)
    return cast(F, call)


@contextmanager
def child_fts_owner(path: str, nonce: str) -> Iterator[None]:
    """Child process only: retain the kernel guard until interpreter/process exit."""
    domain = SourceDomain(Path(path))
    guard = kernel_lock(domain.path / FTS_LOCK)
    _child_guards.append(guard)
    value = domain.receipt()
    if value.get("state") != "dirty" or value.get("fts_pending") != nonce:
        raise SourceLayoutError("FTS handoff is not current")
    try:
        yield
    finally:
        value = domain.receipt()
        if value.get("fts_pending") != nonce:
            raise SourceLayoutError("FTS handoff changed")
        value.pop("fts_pending")
        domain.save(value)


def source_read(method: F) -> F:
    """Hold selected-domain exclusion over each public operation, including lazy iteration."""
    @contextmanager
    def owned(self: Any) -> Iterator[None]:
        store = getattr(self, "_store", self)
        domain = getattr(store, "_source_domain", None)
        if domain is None:
            _refuse_marked_legacy(store)
            yield
            return
        db = (store._content.db if hasattr(store, "_content")
              else getattr(store, "_source_db", getattr(store, "_db", None)))
        with domain.read(db):
            yield

    if inspect.isgeneratorfunction(method):
        @functools.wraps(method)
        def iterate(self: Any, *args: Any, **kwargs: Any) -> Iterator[Any]:
            with owned(self):
                yield from method(self, *args, **kwargs)
        return cast(F, iterate)

    @functools.wraps(method)
    def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        with owned(self):
            return method(self, *args, **kwargs)
    return cast(F, call)


def source_fts_read(method: F) -> F:
    @source_read
    @functools.wraps(method)
    def call(self: Any, *args: Any, **kwargs: Any) -> Any:
        store = getattr(self, "_store", self)
        domain = getattr(store, "_source_domain", None)
        if domain is not None:
            content = store._content if hasattr(store, "_content") else store
            domain.require_fts(content)
        return method(self, *args, **kwargs)
    return cast(F, call)
