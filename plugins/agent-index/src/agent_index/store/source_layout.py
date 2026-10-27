"""Cold-only source layout and kernel exclusion for participating new binaries."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import BinaryIO

LAYOUT = "source-aware-v1"
MARKER = ".source-layout.json"
LOCK = ".source-writer.lock"
FTS_LOCK = ".source-fts.lock"
_domains: dict[tuple[int, int], SourceDomain] = {}
_registry = threading.Lock()
_child_guards = []
log = logging.getLogger(__name__)

def _open_receipt(path: Path) -> BinaryIO:
    """Keep an atomic snapshot readable without preventing its replacement."""
    if os.name != "nt":
        return path.open("rb")
    import ctypes
    import msvcrt
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
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
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
    import ctypes
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


def kernel_lock(path: Path):
    stream = path.open("a+b")
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        stream.close()
        raise SourceLayoutError("source-aware domain busy") from None


class SourceLayoutError(RuntimeError):
    """Unprepared, changed, busy or incompatible isolated domain."""


def selected_layout() -> str | None:
    value = os.environ.get("AGENT_INDEX_STORE_LAYOUT")
    if value not in (None, "", LAYOUT):
        raise SourceLayoutError("unsupported store layout")
    return value or None


def _versions(db) -> dict[str, int]:
    return {name: db.open_table(name).version for name in db.list_tables().tables}


class SourceDomain:
    def __init__(self, path: Path):
        self.path = path
        self._mutex = threading.RLock()
        self._local = threading.local()

    @property
    def writing(self) -> bool:
        return bool(getattr(self._local, "depth", 0))

    @contextmanager
    def exclusion(self):
        if getattr(self._local, "operation", False):
            yield
            return
        if not self._mutex.acquire(blocking=False):
            raise SourceLayoutError("source-aware domain busy")
        stream = None
        guard = None
        try:
            if (self.path / MARKER).exists() and not all(
                (self.path / name).is_file() for name in (LOCK, FTS_LOCK)
            ):
                raise SourceLayoutError("source-aware ownership file is missing")
            stream = kernel_lock(self.path / LOCK)
            guard = kernel_lock(self.path / FTS_LOCK)
            self._local.fts_guard = guard
            yield
        finally:
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
    def operation(self, db):
        """Hold existing exclusion across independently committed reads/writes."""
        if self.writing or getattr(self._local, "operation", False):
            yield
            return
        with self.exclusion():
            value = self.receipt()
            if (value.get("fts_pending") or value.get("state") != "clean"
                    or value["versions"] != _versions(db)):
                raise SourceLayoutError("source projection incomplete or changed")
            self._local.operation = True
            try:
                yield
            finally:
                self._local.operation = False

    def receipt(self) -> dict:
        try:
            with _open_receipt(self.path / MARKER) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise SourceLayoutError("invalid source-layout receipt")
            value = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise SourceLayoutError("source-aware layout not initialized") from exc
        if (not isinstance(value, dict) or value.get("layout") != LAYOUT
                or type(value.get("protocol")) is not int or value["protocol"] != 1
                or value.get("state") not in ("clean", "dirty")
                or not isinstance(value.get("versions"), dict)):
            raise SourceLayoutError("incompatible source-layout receipt")
        return value

    def save(self, value: dict) -> None:
        payload = json.dumps(value, sort_keys=True)
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
    def read(self, db):
        if self.writing or getattr(self._local, "reading", False):
            yield
            return
        with self.exclusion():
            value = self.receipt()
            if (value.get("fts_pending") or value.get("state") != "clean"
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
        value = self.receipt()
        kind, _, table = operation.partition(":")
        if kind in ("content-upsert", "canonical-delete", "content-compact"):
            value.setdefault("fts_coverage", {}).pop(table, None)
        value.update(uncertain_effect=True, operation=operation)
        self.save(value)

    @contextmanager
    def physical_maintenance(self, table_name: str, operation: str):
        """Exempt only the witnessed transition of this maintenance operation."""
        from .logical_projection import physical_tables

        if not self.writing:
            raise SourceLayoutError("maintenance requires participating writer ownership")

        def snapshot():
            return physical_tables(
                self.path, {table_name: self._local.db.open_table(table_name).version},
            )

        before = snapshot()
        self.effect(operation, physical_only=table_name)
        yield
        after = snapshot()
        if (before is not None and after is not None
                and self._local.physical_witness.get(table_name) == before[table_name]):
            self._local.physical_witness[table_name] = after[table_name]

    def cover_fts(self, content) -> None:
        if not self.writing:
            raise SourceLayoutError("FTS coverage requires owning writer")
        value = self.receipt()
        if value.get("fts_pending"):
            raise SourceLayoutError("FTS writer ownership is unsettled")
        table = content._get_or_create_table()
        value.setdefault("fts_coverage", {})[content._table_name] = table.version
        self.save(value)

    def require_fts(self, content) -> bool:
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
    def fts_handoff(self, *, table_name: str | None = None):
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

    @contextmanager
    def write(self, db):
        if self.writing:
            self._local.depth += 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        with self.exclusion():
            value = self.receipt()
            versions = _versions(db)
            if (value.get("fts_pending") or value.get("state") != "clean"
                    or value["versions"] != versions):
                raise SourceLayoutError("source projection requires explicit recovery")
            from .logical_projection import commit_projection, physical_tables

            before = physical_tables(self.path, versions)
            value["state"] = "dirty"
            self.save(value)
            self._local.depth = 1
            self._local.db = db
            self._local.logical_effect = False
            self._local.physical_only = set()
            self._local.physical_witness = dict(before or {})
            try:
                yield
                if self.receipt().get("fts_pending"):
                    raise SourceLayoutError("FTS writer ownership is unsettled")
                value = self.receipt()
                value["versions"] = _versions(db)
                value["state"] = "clean"
                value.pop("uncertain_effect", None)
                value.pop("operation", None)
                after = physical_tables(self.path, value["versions"])
                preserved = {
                    name for name in self._local.physical_only
                    if after is not None and after.get(name) == self._local.physical_witness.get(name)
                } if not self._local.logical_effect else set()
                commit_projection(value, before, after, physical_only=preserved)
                self.save(value)
            finally:
                self._local.depth = 0
                self._local.db = None
                self._local.logical_effect = False
                self._local.physical_only = set()
                self._local.physical_witness = {}


def domain_for(path: str | Path) -> SourceDomain | None:
    path = Path(path).resolve()
    marker = path / MARKER
    if selected_layout() != LAYOUT:
        if marker.exists():
            raise SourceLayoutError("source-aware store refuses unselected binary mode")
        return None
    if not marker.is_file():
        raise SourceLayoutError("initialize a fresh isolated source-aware store explicitly")
    info = path.stat()
    identity = (info.st_dev, info.st_ino)
    with _registry:
        if identity not in _domains:
            _domains[identity] = SourceDomain(path)
        result = _domains[identity]
    result.receipt()
    return result


def initialize_source_layout(path: str | Path) -> dict:
    """Writer bootstrap only; no legacy adoption, transfer, backfill or offline assertion."""
    if selected_layout() != LAYOUT:
        raise SourceLayoutError("source-aware layout must be explicitly selected")
    path = Path(path).resolve()
    if path.exists() and not (path / MARKER).exists() and any(path.iterdir()):
        raise SourceLayoutError("cold initialization refuses nonempty or legacy state")
    path.mkdir(parents=True, exist_ok=True)
    domain = SourceDomain(path)
    with domain.exclusion():
        if (path / MARKER).exists():
            receipt = domain.receipt()
            if receipt.get("uncertain_effect"):
                raise SourceLayoutError(
                    "uncertain effect requires owner repair or fresh isolated reindex"
                )
            if receipt.get("state") != "clean":
                raise SourceLayoutError("source projection requires explicit recovery")
            return receipt
        if any(item.name not in (LOCK, FTS_LOCK) for item in path.iterdir()):
            raise SourceLayoutError("cold initialization refuses nonempty or legacy state")
        receipt = {"layout": LAYOUT, "protocol": 1, "state": "clean",
                   "domain": uuid.uuid4().hex, "versions": {}}
        domain.save(receipt)
        return receipt


def initialize_source_store(config):
    """Cold writer bootstrap, including its canonical content/model tables."""
    initialize_source_layout(config.lance_dir)
    from .multi_model_store import MultiModelStore

    store = MultiModelStore(config.lance_dir, content_table=config.content_table)
    domain = store._source_domain
    existing = domain.receipt()["versions"]
    names = {config.content_table, *(p.table_name for p in config.model_profiles.values())}
    for profile in config.model_profiles.values():
        store.register_model(profile)
    if names <= existing.keys():
        with domain.read(store.content_store.db):
            return store
    with domain.write(store.content_store.db):
        store.content_store._get_or_create_table()
        for profile in config.model_profiles.values():
            store.register_model(profile)._get_or_create_table()
    return store


def validate_source_store(store) -> dict:
    """Explicit NEW-domain recovery: validate only; never guess or backfill provenance."""
    from .source_projection import PAGE_SIZE

    domain = store._source_domain
    if domain is None:
        raise SourceLayoutError("not a source-aware domain")
    db = store.content_store.db
    with domain.exclusion():
        receipt = domain.receipt()
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
        content = store.content_store._get_or_create_table()
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


def source_write(method):
    """Serialize actual participating mutation paths and latch uncertainty on failure."""
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        domain = getattr(self, "_source_domain", None)
        if domain is None:
            return method(self, *args, **kwargs)
        db = (self._content.db if hasattr(self, "_content")
              else getattr(self, "_source_db", getattr(self, "_db", None)))
        with domain.write(db):
            return method(self, *args, **kwargs)
    return call


@contextmanager
def child_fts_owner(path: str, nonce: str):
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


def source_read(method):
    """Hold selected-domain exclusion over each public operation, including lazy iteration."""
    @contextmanager
    def owned(self):
        store = getattr(self, "_store", self)
        domain = getattr(store, "_source_domain", None)
        if domain is None:
            yield
            return
        db = (store._content.db if hasattr(store, "_content")
              else getattr(store, "_source_db", getattr(store, "_db", None)))
        with domain.read(db):
            yield

    if inspect.isgeneratorfunction(method):
        @functools.wraps(method)
        def iterate(self, *args, **kwargs):
            with owned(self):
                yield from method(self, *args, **kwargs)
        return iterate

    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with owned(self):
            return method(self, *args, **kwargs)
    return call


def source_fts_read(method):
    @source_read
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        store = getattr(self, "_store", self)
        domain = getattr(store, "_source_domain", None)
        if domain is not None:
            content = store._content if hasattr(store, "_content") else store
            domain.require_fts(content)
        return method(self, *args, **kwargs)
    return call
