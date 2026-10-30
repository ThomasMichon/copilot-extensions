"""Rebuildable generation storage, separate from replay and operation receipts."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import stat

from single_instance_lease import OperationLock

from .review_workspace import MAX_GENERATION_BYTES, _digest, _generation, _scope
from .watch_replay_store import ReplayServiceIdentity, json_object
from .watch_storage import StateCommitUncertain, _atomic_write_json

SCHEMA = "agent-pull-requests.generation-cache"
POINTER_SCHEMA = "agent-pull-requests.generation-cache-current"
MAX_POINTER_BYTES = 16384


class CacheUnavailable(RuntimeError):
    """The selected cache has not been explicitly provisioned."""


class CacheInvalid(ValueError):
    """Owned cache state cannot be safely interpreted or replaced."""


class CacheFull(RuntimeError):
    """Explicit cache capacity cannot fit provisioning."""


def _encoded(value: object) -> bytes:
    return json.dumps(value, allow_nan=False).encode("utf-8")


def _identifier(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("a complete hexadecimal generation ID is required")
    return value


def _plain(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
    ):
        raise CacheInvalid("cache paths must be plain, unlinked files/directories")


class GenerationCache:
    """Explicit native/service embedding; namespace metadata is not authority.

    Service callers retain their existing writer lease/fencing and checkpoint.
    Native callers use their existing OS file authority. No provider IO, poller,
    credential source or posting operation is invoked here.
    """

    def __init__(
        self, owner_root: Path, namespace: str, *, max_bytes: int, max_generations: int,
        service_identity: ReplayServiceIdentity | None = None, checkpoint=None,
    ):
        self.owner_root = Path(owner_root)
        if (
            not self.owner_root.is_absolute() or self.owner_root.resolve() != self.owner_root
            or self.owner_root == Path(self.owner_root.anchor)
        ):
            raise ValueError("cache requires an absolute canonical owner-specific root")
        if (
            not isinstance(namespace, str) or not 1 <= len(namespace) <= 256
            or any(ord(char) < 32 for char in namespace)
            or type(max_bytes) is not int or max_bytes <= 0
            or type(max_generations) is not int or max_generations <= 0
            or service_identity is not None and not isinstance(service_identity, ReplayServiceIdentity)
            or checkpoint is not None and not callable(checkpoint)
        ):
            raise ValueError("cache requires explicit namespace, finite capacity and valid owner inputs")
        self.root = self.owner_root / "review-cache"
        self.descriptor = self.root / "descriptor.json"
        self.generations = self.root / "generations"
        self.targets = self.root / "targets"
        self.lock_path = self.root / "cache.lock"
        self.max_bytes, self.max_generations = max_bytes, max_generations
        self.checkpoint = checkpoint
        self.expected_descriptor = {
            "schema": SCHEMA, "version": 1, "namespace": namespace,
            "service": service_identity.document() if service_identity is not None else None,
            "limits": {"max_bytes": max_bytes, "max_generations": max_generations},
        }

    @staticmethod
    def contract() -> dict:
        """Executing component support, not store provisioning or authority."""
        return {
            "schema": "agent-pull-requests.generation-cache-contract", "version": 1,
            "generation_versions": [1], "operations": ["provision", "lookup", "publish", "rebuild"],
            "backend_access": False, "automatic_eviction": False,
            "service_activation": False, "writer_fencing": "existing_embedding_owner",
            "capacity_scope": "committed_cache_objects",
        }

    def _check(self, lock=None):
        if lock is not None:
            lock.assert_owned()
        if self.checkpoint is not None:
            self.checkpoint()

    def _read(self, path: Path, limit: int) -> dict:
        try:
            _plain(path)
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
            if len(data) > limit:
                raise CacheInvalid("cache object exceeds its byte budget")
            return json_object(data)
        except (FileNotFoundError, UnicodeError, RecursionError, ValueError):
            raise CacheInvalid("cache object is missing, malformed or unsafe") from None

    def _existing(self):
        try:
            _plain(self.owner_root, directory=True)
            _plain(self.root, directory=True)
        except FileNotFoundError:
            raise CacheUnavailable("cache is unavailable; provisioning is explicit") from None
        for path in (self.generations, self.targets):
            try:
                _plain(path, directory=True)
            except FileNotFoundError:
                raise CacheInvalid("cache directory is missing") from None
        if any(path.name not in {"descriptor.json", "cache.lock", "generations", "targets"}
               for path in self.root.iterdir()):
            raise CacheInvalid("unexpected cache root entry")
        if self.lock_path.exists() or self.lock_path.is_symlink():
            _plain(self.lock_path)

    @contextmanager
    def _transaction(self):
        self._check()
        self._existing()
        with OperationLock(self.lock_path) as lock:
            self._check(lock)
            descriptor = self._read(self.descriptor, 65536)
            limits = descriptor.get("limits")
            if (
                descriptor != self.expected_descriptor
                or type(descriptor.get("version")) is not int
                or not isinstance(limits, dict)
                or any(type(limits.get(name)) is not int for name in ("max_bytes", "max_generations"))
            ):
                raise CacheInvalid("cache descriptor namespace, identity, version or limits mismatch")
            yield lock

    def provision(self) -> None:
        """Create only this explicit cache child; never repair or replace it."""
        self._check()
        if len(_encoded(self.expected_descriptor)) > self.max_bytes:
            raise CacheFull("cache descriptor does not fit configured capacity")
        self.owner_root.mkdir(parents=True, exist_ok=True)
        _plain(self.owner_root, directory=True)
        self.root.mkdir()  # Existing/corrupt cache is not first provisioning.
        self.generations.mkdir()
        self.targets.mkdir()
        with OperationLock(self.lock_path) as lock:
            self._check(lock)
            _atomic_write_json(self.descriptor, self.expected_descriptor)

    def _inventory(self) -> tuple[list[Path], list[Path], int]:
        for path in self.root.iterdir():
            if path.name not in {"descriptor.json", "cache.lock", "generations", "targets"}:
                raise CacheInvalid("unexpected cache root entry")
            _plain(path, directory=path.name in {"generations", "targets"})
        generations, pointers = [], []
        total = self.descriptor.stat().st_size
        for directory, output in ((self.generations, generations), (self.targets, pointers)):
            for path in directory.iterdir():
                _plain(path)
                if path.suffix != ".json":
                    raise CacheInvalid("unexpected cache object name")
                try:
                    _identifier(path.stem)
                except ValueError:
                    raise CacheInvalid("unexpected cache object name") from None
                output.append(path)
                total += path.stat().st_size
        return generations, pointers, total

    def _load_generation(self, identifier: str, scope: dict) -> dict:
        value = self._read(self.generations / (_identifier(identifier) + ".json"), MAX_GENERATION_BYTES)
        try:
            _generation(value, scope)
        except ValueError:
            raise CacheInvalid("cached generation is corrupt or outside the selected scope") from None
        if value["id"] != identifier:
            raise CacheInvalid("cached generation filename does not match its content")
        return value

    def _current(self, scope: dict) -> str | None:
        path = self.targets / (_digest(scope) + ".json")
        if not path.exists() and not path.is_symlink():
            return None
        value = self._read(path, MAX_POINTER_BYTES)
        if (
            set(value) != {"schema", "version", "scope", "generation_id"}
            or value["schema"] != POINTER_SCHEMA or type(value["version"]) is not int
            or value["version"] != 1 or value["scope"] != scope
        ):
            raise CacheInvalid("cache pointer does not match the selected scope")
        try:
            _scope(value["scope"])
        except ValueError:
            raise CacheInvalid("cache pointer has an invalid scope") from None
        if value["generation_id"] is None:
            return None  # Explicit cache-only rebuild tombstone.
        try:
            identifier = _identifier(value["generation_id"])
        except ValueError:
            raise CacheInvalid("cache pointer has an invalid generation ID") from None
        self._load_generation(identifier, scope)
        return identifier

    def lookup(self, scope: dict, *, generation_id: str | None = None) -> dict:
        scope = _scope(scope)
        if generation_id is not None:
            _identifier(generation_id)
        with self._transaction() as lock:
            current = self._current(scope)
            selected = current if generation_id is None else generation_id
            generation = None
            if selected is not None:
                path = self.generations / (selected + ".json")
                if path.exists() or path.is_symlink():
                    generation = self._load_generation(selected, scope)
            self._check(lock)
            return {
                "schema": "agent-pull-requests.generation-cache-read", "version": 1,
                "found": generation is not None, "generation": generation,
                "current_id": current, "freshness": "retained", "effects": "none",
            }

    def publish(self, generation: dict, *, expected_current: str | None) -> dict:
        """CAS one target; contention retains the existing OperationLock error."""
        if not isinstance(generation, dict):
            raise ValueError("publication requires a generation object")
        generation = copy.deepcopy(generation)
        scope = _scope(generation.get("scope"))
        _generation(generation, scope)
        identifier = _identifier(generation["id"])
        if expected_current is not None:
            _identifier(expected_current)
        pointer = {
            "schema": POINTER_SCHEMA, "version": 1, "scope": scope, "generation_id": identifier,
        }
        if len(_encoded(pointer)) > MAX_POINTER_BYTES:
            raise ValueError("cache pointer exceeds its read budget")
        with self._transaction() as lock:
            current = self._current(scope)

            def outcome(status, *, visible=current, stage=None):
                return {
                    "schema": "agent-pull-requests.generation-cache-publication", "version": 1,
                    "status": status, "observed_generation_id": identifier,
                    "observed_revision": copy.deepcopy(generation["revision"]),
                    "confirmed_current_id": current if status != "published" else identifier,
                    "visible_current_id": visible, "stage": stage,
                    "readback_required": status == "unconfirmed", "effects": "cache_only",
                }

            if current != expected_current:
                return outcome("conflict")
            generations, _, total = self._inventory()
            object_path = self.generations / (identifier + ".json")
            target_path = self.targets / (_digest(scope) + ".json")
            existing = object_path.exists() or object_path.is_symlink()
            old_object_size = 0
            if existing:
                self._load_generation(identifier, scope)
                old_object_size = object_path.stat().st_size
            old_pointer_size = target_path.stat().st_size if target_path.exists() else 0
            projected = (
                total - old_object_size - old_pointer_size
                + len(_encoded(generation)) + len(_encoded(pointer))
            )
            if len(generations) + (0 if existing else 1) > self.max_generations or projected > self.max_bytes:
                return outcome("full")

            def write(path, value, stage):
                try:
                    _atomic_write_json(path, value)
                except StateCommitUncertain:
                    return outcome(
                        "unconfirmed", visible=identifier if stage == "pointer" else current,
                        stage=stage,
                    )
                except PermissionError:
                    raise
                except OSError:
                    return outcome("failed", stage=stage)
                return None

            self._check(lock)
            # Re-acknowledge durability even for a previously unconfirmed object;
            # its ID/content stay immutable before a pointer can reference it.
            failed = write(object_path, generation, "generation")
            if failed is not None:
                return failed
            self._check(lock)
            failed = write(target_path, pointer, "pointer")
            if failed is not None:
                return failed
            self._check(lock)
            return outcome("published", visible=identifier)

    def rebuild(self) -> None:
        """Explicit cache-only reset of valid managed state, never parent receipts."""
        with self._transaction() as lock:
            generations, pointers, _ = self._inventory()
            for path in generations:
                value = self._read(path, MAX_GENERATION_BYTES)
                self._load_generation(path.stem, _scope(value.get("scope")))
            for path in pointers:
                value = self._read(path, MAX_POINTER_BYTES)
                scope = _scope(value.get("scope"))
                if path.stem != _digest(scope):
                    raise CacheInvalid("cache pointer filename does not match its scope")
                self._current(scope)
            # Durably clear every pointer before retiring any object. A crash
            # during the clear leaves old pointers referencing still-held data.
            for path in pointers:
                self._check(lock)
                value = self._read(path, MAX_POINTER_BYTES)
                value["generation_id"] = None
                _atomic_write_json(path, value)
            for path in generations:
                self._check(lock)
                path.unlink()
            self._check(lock)
