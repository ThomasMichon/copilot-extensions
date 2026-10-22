"""Retained authenticated artifact bytes, never installation or activation."""

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from types import ModuleType
import tempfile
import uuid

from .artifacts import (
    CHUNK_BYTES,
    MAX_ARTIFACT_BYTES,
    _content_stamp,
    _file_stat,
    _identity,
    _reader,
    verify_role_artifacts,
)
from .client_diagnostics import diagnose_client
from .planning import MAX_BYTES, PlanError, canonical, decode
from .release_auth import authenticate_release
from .staging_cleanup import directory_identity, remove_owned_stage

_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}\Z")
_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(10)
}


def _clock() -> int:
    return int(time.time())


def _filename(path: str) -> str:
    name = Path(path).name
    if (
        not _FILENAME.fullmatch(name) or name.endswith(".")
        or name.split(".", 1)[0].upper() in _DEVICES
    ):
        raise PlanError("artifact staging requires a portable ordinary filename")
    return name


def _directory(path: Path) -> tuple[int, int]:
    if not path.is_absolute() or ".." in path.parts:
        raise PlanError("staging directories must be absolute without parent traversal")
    for entry in (*reversed(path.parents), path):
        try:
            info = entry.lstat()
        except OSError:
            raise PlanError("staging directory could not be inspected") from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise PlanError("staging directory contains a linked or non-directory entry")
    try:
        return directory_identity(path)
    except OSError:
        raise PlanError("staging directory identity could not be inspected") from None


def _read_bytes(path: Path, limit: int) -> bytes:
    before = _file_stat(path)
    if not 1 <= before.st_size <= limit:
        raise PlanError("staged input exceeds its byte bound or is empty")
    with _reader(path) as incoming:
        opened = os.fstat(incoming.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise PlanError("staged input changed during admission")
        data = incoming.read(limit + 1)
        after = os.fstat(incoming.fileno())
        named = _file_stat(path)
        if not 1 <= len(data) <= limit or len(data) != opened.st_size:
            raise PlanError("staged input changed during admission (read-size)")
        if _content_stamp(after) != _content_stamp(opened):
            raise PlanError("staged input changed during admission (descriptor-stamp)")
        if _identity(named) != _identity(before):
            raise PlanError("staged input changed during admission (named-identity)")
        if _content_stamp(named) != _content_stamp(before):
            raise PlanError("staged input changed during admission (named-stamp)")
    return data


def _bytes(path: Path, limit: int) -> bytes:
    try:
        return _read_bytes(path, limit)
    except OSError:
        raise PlanError("staged input could not be read") from None


@contextmanager
def _primitive() -> Iterator[ModuleType]:
    evidence = diagnose_client()
    resource = Path(__file__).absolute().with_name("_versioned_runtime.py")
    content = _bytes(resource, MAX_BYTES)
    if hashlib.sha256(content).hexdigest() != evidence["runtime_resource_sha256"]:
        raise PlanError("installed runtime resource changed after RECORD admission")
    companions = {}
    for component in (
        "versioned_runtime_leases", "versioned_runtime_construction", "versioned_runtime_source",
        "versioned_runtime_publication",
    ):
        member = component + ".py"
        retained = _bytes(resource.with_name(member), MAX_BYTES)
        if hashlib.sha256(retained).hexdigest() != evidence["runtime_resource_sha256s"][member]:
            raise PlanError("installed runtime companion changed after RECORD admission")
        companions[component] = retained
    name = f"fleet_manager._retained_staging_runtime_{uuid.uuid4().hex}"
    module = ModuleType(name)
    module.__file__ = str(resource)
    module._retained_runtime_components = {}
    sys.modules[name] = module
    try:
        for component, retained in companions.items():
            child = ModuleType(name + "_" + component)
            child.__file__ = str(resource.with_name(component + ".py"))
            sys.modules[child.__name__] = child
            exec(compile(retained, child.__file__, "exec"), child.__dict__)
            module._retained_runtime_components[component] = child
        module._retained_runtime_components["versioned_runtime_publication"]._leases = (
            module._retained_runtime_components["versioned_runtime_leases"]
        )
        exec(compile(content, str(resource), "exec"), module.__dict__)
        yield module
    finally:
        for component in companions:
            sys.modules.pop(name + "_" + component, None)
        if sys.modules.get(name) is module:
            del sys.modules[name]


def _copy_bytes(source: Path, target: Path, expected: str) -> None:
    before = _file_stat(source)
    if not 1 <= before.st_size <= MAX_ARTIFACT_BYTES:
        raise PlanError("artifact exceeds its byte bound or is empty")
    with _reader(source) as incoming:
        opened = os.fstat(incoming.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise PlanError("artifact changed while being frozen")
        digest = hashlib.sha256()
        size = 0
        with target.open("xb") as outgoing:
            while block := incoming.read(min(CHUNK_BYTES, MAX_ARTIFACT_BYTES - size + 1)):
                size += len(block)
                if size > MAX_ARTIFACT_BYTES:
                    raise PlanError("artifact exceeds its byte bound")
                digest.update(block)
                outgoing.write(block)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        after = os.fstat(incoming.fileno())
        named = _file_stat(source)
        if size != opened.st_size or digest.hexdigest() != expected:
            raise PlanError("artifact changed while being frozen (content)")
        if _content_stamp(after) != _content_stamp(opened):
            raise PlanError("artifact changed while being frozen (descriptor-stamp)")
        if _identity(named) != _identity(before):
            raise PlanError("artifact changed while being frozen (named-identity)")
        if _content_stamp(named) != _content_stamp(before):
            raise PlanError("artifact changed while being frozen (named-stamp)")


def _copy(source: Path, target: Path, expected: str) -> None:
    try:
        _copy_bytes(source, target, expected)
    except OSError:
        raise PlanError("artifact could not be frozen as an unchanged ordinary file") from None


@dataclass(frozen=True)
class StagedArtifacts:
    """A live retained byte stage; serialized reports cannot reopen it."""

    authority: str
    installation: str
    role: str
    release_digest: str
    sequence: int
    _slot: Path = field(repr=False)
    _root: Path = field(repr=False)
    _root_identity: tuple[int, int] = field(repr=False)
    _manifest_bytes: bytes = field(repr=False)
    _proof_bytes: bytes = field(repr=False)
    _artifacts: tuple[tuple[str, Path], ...] = field(repr=False)
    _closed: threading.Event = field(repr=False, compare=False)
    _operation_lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False,
    )

    @property
    def artifacts(self) -> tuple[str, ...]:
        """Selected component names, never reusable filesystem paths."""
        return tuple(name for name, _ in self._artifacts)

    @property
    def artifact_filenames(self) -> dict[str, str]:
        """Portable artifact names only; no reusable stage pathname."""
        return {name: path.name for name, path in self._artifacts}

    def read_manifest(
        self, policy: object, *, clock: Callable[[], int] = _clock,
    ) -> bytes:
        """Return the exact manifest bytes after current live-stage authentication."""
        with self._operation_lock:
            self._reverify(policy, clock=clock)
            return self._manifest_bytes

    def read_artifact(
        self, component: str, policy: object, *, clock: Callable[[], int] = _clock,
    ) -> bytes:
        """Return a bounded immutable snapshot authenticated under current policy."""
        with self._operation_lock:
            if self._closed.is_set():
                raise PlanError("artifact staging retention has ended")
            if not isinstance(component, str) or component not in self.artifacts:
                raise PlanError("artifact component is not selected by this stage")
            if _directory(self._root) != self._root_identity:
                raise PlanError("artifact staging ownership changed")
            path = dict(self._artifacts)[component]
            content = _bytes(path, MAX_ARTIFACT_BYTES)
            evidence = self._reverify(policy, clock=clock)
            expected = next(
                item for item in evidence["components"] if item["component"] == component
            )
            if (
                len(content) != expected["bytes"]
                or hashlib.sha256(content).hexdigest() != expected["artifact_sha256"]
            ):
                raise PlanError("artifact snapshot digest mismatch")
            if _directory(self._root) != self._root_identity:
                raise PlanError("artifact staging ownership changed")
            return content

    def reverify(self, policy: object, *, clock: Callable[[], int] = _clock) -> dict:
        """Re-authenticate exact frozen bytes under supplied current trusted policy."""
        with self._operation_lock:
            return self._reverify(policy, clock=clock)

    def read_artifacts(
        self, policy: object, *, clock: Callable[[], int] = _clock,
    ) -> dict[str, bytes]:
        """Consume the selected snapshots with constant-count closure verification."""
        with self._operation_lock:
            evidence = self._reverify(policy, clock=clock)
            expected = {item["component"]: item for item in evidence["components"]}
            snapshots = {}
            for name, path in self._artifacts:
                content = _bytes(path, MAX_ARTIFACT_BYTES)
                if (
                    len(content) != expected[name]["bytes"]
                    or hashlib.sha256(content).hexdigest() != expected[name]["artifact_sha256"]
                ):
                    raise PlanError("artifact snapshot digest mismatch")
                snapshots[name] = content
            self._reverify(policy, clock=clock)
            return snapshots

    def _close(self) -> None:
        with self._operation_lock:
            self._closed.set()

    def _reverify(self, policy: object, *, clock: Callable[[], int]) -> dict:
        if self._closed.is_set():
            raise PlanError("artifact staging retention has ended")
        if _directory(self._root) != self._root_identity:
            raise PlanError("artifact staging ownership changed")
        manifest = _bytes(self._slot / "release.json", MAX_BYTES)
        if manifest != self._manifest_bytes:
            raise PlanError("frozen manifest identity changed")
        authenticated = authenticate_release(
            manifest, decode(self._proof_bytes), policy, authority=self.authority,
            installation=self.installation, role=self.role, now=clock(),
        )
        sources = {
            "schema": "fleet-manager.artifact-sources", "version": 1,
            "components": {name: str(path) for name, path in self._artifacts},
        }
        integrity = verify_role_artifacts(decode(manifest), sources, role=self.role)
        if self._closed.is_set() or _directory(self._root) != self._root_identity:
            raise PlanError("artifact staging retention or ownership changed")
        result = {
            "schema": "fleet-manager.staged-artifacts", "version": 1,
            "status": "retained-byte-stage", "evidence": "current-owned-stage-reverification",
            "release": authenticated["release"], "release_digest": authenticated["release_digest"],
            "authority": self.authority, "installation": self.installation, "role": self.role,
            "sequence": authenticated["sequence"], "verified_at": authenticated["verified_at"],
            "valid_until": authenticated["valid_until"],
            "policy_digest": authenticated["policy_digest"],
            "policy": authenticated["policy"], "method": authenticated["method"],
            "policy_revision": authenticated["policy_revision"],
            "trust_revision": authenticated["trust_revision"],
            "signer_key": authenticated["signer_key"],
            "signer_key_sha256": authenticated["signer_key_sha256"],
            "components": integrity["components"], "total_bytes": integrity["total_bytes"],
            "authenticity_verified": True, "artifact_integrity_verified": True,
            "retention_held": True, "activation_authorized": False,
            "live_readiness_verified": False, "mutations_performed": True,
        }
        canonical(result)
        return result


@contextmanager
def stage_authenticated_artifacts(
    manifest_bytes: bytes, proof: object, policy: object, sources: object, *,
    authority: str, installation: str, role: str, staging_parent: Path,
    clock: Callable[[], int] = _clock,
) -> Iterator[StagedArtifacts]:
    """Freeze selected authenticated bytes in an owned, context-lifetime stage."""
    if type(manifest_bytes) is not bytes:
        raise PlanError("artifact staging requires immutable canonical manifest bytes")
    proof_bytes, policy_bytes, source_bytes = canonical(proof), canonical(policy), canonical(sources)
    authenticated = authenticate_release(
        manifest_bytes, decode(proof_bytes), decode(policy_bytes), authority=authority,
        installation=installation, role=role, now=clock(),
    )
    release = decode(manifest_bytes)
    selected = decode(source_bytes)
    integrity = verify_role_artifacts(release, selected, role=role)
    if release["id"].casefold() == "dev":
        raise PlanError("artifact staging cannot use a mutable development identity")
    _filename(release["id"])
    filenames = {}
    for component in integrity["components"]:
        name = component["component"]
        filenames[name] = _filename(selected["components"][name])
    parent = Path(staging_parent)
    parent_identity = _directory(parent)
    root = None
    root_identity = None
    staged = None
    with _primitive() as primitive:
        try:
            root = Path(tempfile.mkdtemp(prefix="fleet-artifact-stage-", dir=parent))
            root_identity = _directory(root)
            if _directory(parent) != parent_identity:
                raise PlanError("staging parent ownership changed")
            with ExitStack() as lifecycle:
                with primitive.construction_lease(root, release["id"]):
                    slot = primitive.slot(root, release["id"], clean_incomplete=False)
                    if slot != primitive.version_dir(root, release["id"]):
                        raise PlanError("canonical primitive selected an unexpected artifact slot")
                    _directory(slot)
                    (slot / "release.json").write_bytes(manifest_bytes)
                    artifacts = []
                    for component in integrity["components"]:
                        name = component["component"]
                        directory = (
                            slot / "artifacts" / hashlib.sha256(name.encode("utf-8")).hexdigest()
                        )
                        directory.mkdir(parents=True)
                        target = directory / filenames[name]
                        _copy(Path(selected["components"][name]).absolute(), target,
                              component["artifact_sha256"])
                        artifacts.append((name, target))
                    staged = StagedArtifacts(
                        authority, installation, role, authenticated["release_digest"],
                        authenticated["sequence"], slot, root, root_identity,
                        manifest_bytes, proof_bytes, tuple(artifacts), threading.Event(),
                    )
                    lifecycle.enter_context(primitive.retention_lease(root, release["id"]))
                    staged.reverify(decode(policy_bytes), clock=clock)
                try:
                    yield staged
                finally:
                    staged._close()
        finally:
            if staged is not None:
                staged._close()
            if root is not None:
                if root_identity is None or _directory(root) != root_identity:
                    raise PlanError("artifact stage cleanup ownership is unconfirmed")
                remove_owned_stage(root, root_identity)
