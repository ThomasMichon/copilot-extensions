"""Small advisory files derived from the resident's accepted current rows."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import uuid
from pathlib import Path

from .worktree_data_contract import segment, store_identity

VERSION = 2
MAX_BYTES = 16 * 1024
MAX_STRING_BYTES = 4096
MAX_VIEWS = 4096
_CONTEXT_FIELDS = ("marketplaceId", "installReceipt", "pluginRoot")
_TEXT_FACTS = ("status", "status_note_at", "last_session_id", "record_head_session")


def _text(value: object, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str) or (required and not value)
        or "\0" in value or len(value.encode("utf-8")) > MAX_STRING_BYTES
    ):
        raise ValueError("invalid hook fact string")
    return value


def _child(path: Path, *, directory: bool) -> None:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode))
        or (not directory and metadata.st_nlink != 1)
    ):
        raise ValueError("unsafe hook fact path")


class HookFactsPublisher:
    """Own only this process generation's optional files, never tracking state."""

    def __init__(
        self, runtime_root: Path, runtime_generation: str | None, *, project_root, context=None,
    ) -> None:
        if not runtime_root.is_absolute():
            raise ValueError("hook fact runtime root must be absolute")
        self.generation = uuid.uuid4().hex
        context = {} if context is None else context
        self.producer = {
            "runtime_root": _text(str(runtime_root), required=True),
            "runtime_generation": (
                None if runtime_generation is None else store_identity(runtime_generation)
            ),
            "hook_facts_generation": self.generation,
            **{field: _text(context.get(field)) for field in _CONTEXT_FIELDS},
        }
        self._project_root = project_root
        self._files: dict[str, dict[Path, bytes]] = {}
        self._lock = threading.RLock()
        self._active = False
        self.error: str | None = None

    def activate(self) -> None:
        with self._lock:
            self._active = True

    def _root(self, project: str) -> Path:
        root = Path(self._project_root(project))
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("hook fact project root is unavailable")
        return root

    def _home(self, root: Path, worktree: str, *, create: bool) -> Path:
        home = root / "worktrees"
        _child(home, directory=True)
        for name in (worktree, "hook-facts"):
            home /= name
            if create:
                home.mkdir(exist_ok=True)
            _child(home, directory=True)
        return home

    def _encode(self, project: str, row: dict, projection: dict) -> bytes | None:
        provenance = (row.get("fact_provenance") or {}).get("record") or {}
        if (
            provenance.get("quality") != "current" or row.get("id") == "@anchor"
            or not row.get("creation_nonce") or not row.get("path")
            or not any(row.get(field) for field in (
                "live_session_ids", "session_lock_live", "session_bound_live", "session_bridge_live",
            ))
        ):
            return None
        worktree = segment(row.get("id"), "hook fact worktree")
        nonce = segment(row.get("creation_nonce"), "hook fact incarnation")
        workspace = _text(row.get("path"), required=True)
        if not Path(workspace).is_absolute():
            raise ValueError("hook fact workspace must be absolute")
        yielded = row.get("head_yielded")
        if yielded is not None and type(yielded) is not bool:
            raise ValueError("invalid hook fact yielded-head flag")
        revision = row.get("semantic_revision")
        if type(revision) is not int or revision < 1:
            raise ValueError("invalid hook fact row revision")
        value = {
            "version": VERSION if "record_head_session" in row else 1,
            "quality": "advisory",
            "completeness": "partial",
            "scope": {
                "project": project, "worktree_id": worktree,
                "creation_nonce": nonce, "workspace": workspace,
            },
            "producer": {
                **self.producer,
                "projection_generation": store_identity(projection["generation"]),
            },
            "revision": revision,
            "facts": {
                **{
                    field: _text(row.get(field)) for field in _TEXT_FACTS
                    if field != "record_head_session" or field in row
                },
                "head_yielded": yielded,
            },
        }
        raw = json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise ValueError("hook fact view exceeds byte bound")
        return raw

    def _remove(self, root: Path, path: Path) -> None:
        try:
            home = self._home(root, path.parent.parent.name, create=False)
            if path.parent != home:
                raise ValueError("hook fact home changed")
            _child(path, directory=False)
            path.unlink()
        except FileNotFoundError:
            return

    def withdraw(self, project: str) -> None:
        with self._lock:
            paths = tuple(self._files.get(project, {}))
            if not paths:
                return
            try:
                root = self._root(project)
            except (OSError, ValueError) as error:
                self.error = type(error).__name__
                return
            for path in paths:
                try:
                    self._remove(root, path)
                except (OSError, ValueError) as error:
                    self.error = type(error).__name__
                else:
                    del self._files[project][path]

    def close(self) -> None:
        with self._lock:
            self._active = False
            for project in tuple(self._files):
                self.withdraw(project)

    def publish(self, project: str, publication: dict) -> None:
        with self._lock:
            if self._active:
                self._publish(project, publication)

    def _publish(self, project: str, publication: dict) -> None:
        try:
            segment(project, "hook fact project")
            rows = publication["worktrees"]
            projection = publication["projection"]
            if projection["quality"] != "current" or len(rows) > MAX_VIEWS:
                raise ValueError("hook fact roster is unavailable or exceeds its bound")
            desired = {}
            for row in rows:
                raw = self._encode(project, row, projection)
                if raw is not None:
                    desired[segment(row["id"], "hook fact worktree")] = raw
            retained = self._files.setdefault(project, {})
            root = self._root(project) if desired or retained else None
            wanted = set()
            for worktree, raw in desired.items():
                home = self._home(root, worktree, create=True)
                path = home / f"{self.generation}.json"
                wanted.add(path)
                previous = retained.get(path)
                unchanged = previous == raw
                if previous is not None and not unchanged:
                    before, after = json.loads(previous), json.loads(raw)
                    before.pop("revision")
                    after.pop("revision")
                    unchanged = before == after
                if unchanged and path.exists():
                    _child(path, directory=False)
                    continue
                try:
                    _child(path, directory=False)
                except FileNotFoundError:
                    pass
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=home, delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(raw)
                    self._home(root, worktree, create=False)
                    try:
                        _child(path, directory=False)
                    except FileNotFoundError:
                        pass
                    os.replace(temporary, path)
                    retained[path] = raw
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            for path in set(retained) - wanted:
                self._remove(root, path)
                del retained[path]
            self.error = None
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.withdraw(project)
            self.error = type(error).__name__
