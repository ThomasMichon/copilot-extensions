"""Root-wide, fail-closed retention for dispatch-owned companion generations."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from plugin_activation import write_json_object_atomic

from .managed_runtime import (
    RECEIPT_NAME,
    ManagedRuntimeError,
    _assert_safe_descendant,
    _authority,
    _canonical_digest,
    _cell_key,
    _ensure_safe_root,
    _hash_regular_file,
    _plugin_identity,
    _python_path,
    _read_metadata,
    _reject_link,
    _RootLock,
    _safe_directory,
    _tree_digest,
    _walk_error,
    managed_runtime_root,
)
from .registrations import RegistrationError, validate_registration

if TYPE_CHECKING:
    from .companion import ManagedLaunchSnapshot


def process_identity_domain() -> str:
    """Identify the OS PID authority, never a supervisor's configurable host alias."""
    if os.name == "nt":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            machine, _ = winreg.QueryValueEx(key, "MachineGuid")
        if not isinstance(machine, str) or not machine:
            raise ManagedRuntimeError("Windows process identity authority is unavailable")
        return _canonical_digest({"platform": "windows", "machine": machine})
    if sys.platform.startswith("linux"):
        boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        namespace = os.readlink("/proc/self/ns/pid")
        if not boot or not re.fullmatch(r"pid:\[\d+\]", namespace):
            raise ManagedRuntimeError("Linux process identity authority is unavailable")
        return _canonical_digest({"platform": "linux", "boot": boot, "pid_namespace": namespace})
    raise ManagedRuntimeError(
        "managed retention requires verifiable OS process identity authority"
    )


@dataclass(frozen=True)
class RetentionPolicy:
    """Keep recent unreferenced cells in addition to all selections and live leases."""

    keep_generations: int = 2
    minimum_age_seconds: float = 86400

    def __post_init__(self) -> None:
        if (
            type(self.keep_generations) is not int
            or not 0 <= self.keep_generations <= 100
            or isinstance(self.minimum_age_seconds, bool)
            or not isinstance(self.minimum_age_seconds, (int, float))
            or not 0 <= self.minimum_age_seconds <= 365 * 86400
        ):
            raise ValueError("managed retention policy is outside its bounded limits")


@dataclass(frozen=True)
class CleanupResult:
    """Retired published cells, preserved cells, and removed stale leases."""

    deleted: tuple[Path, ...]
    preserved: tuple[Path, ...]
    stale_leases: int


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _ordinary_tree(root: Path) -> None:
    """Preflight every descendant, including non-reclaimable staging and legacy data."""
    _reject_link(root, description="managed retention tree")
    if not root.is_dir():
        raise ManagedRuntimeError("managed retention tree is not a directory")
    for current, directories, files in os.walk(root, followlinks=False, onerror=_walk_error):
        for name in directories + files:
            child = Path(current) / name
            _reject_link(child, description="managed retention descendant")
            mode = child.lstat().st_mode
            expected = stat.S_ISDIR if name in directories else stat.S_ISREG
            if not expected(mode):
                raise ManagedRuntimeError("managed retention tree contains a special file")


def _remove_deletion_staging(root: Path, staging: Path) -> None:
    """Best-effort removal of an unpublished cell; never retry old staging debris."""
    if staging.parent != root / ".deleting" or not re.fullmatch(r"[0-9a-f]{32}", staging.name):
        raise ManagedRuntimeError("managed deletion staging path is inconsistent")
    for attempt in range(3):
        if attempt:
            time.sleep(0.1 * attempt)
        try:
            _assert_safe_descendant(root, staging, description="managed deletion staging")
            try:
                staging.lstat()
            except FileNotFoundError:
                return
            _ordinary_tree(staging)
            shutil.rmtree(staging)
            return
        except OSError as exc:
            if attempt == 2:
                logging.getLogger("agent-dispatch.companion").warning(
                    "managed cell is unpublished; preserving deletion staging residue %s: %s",
                    staging, exc,
                )


def _cell_owner(root: Path, cell: Path) -> Path:
    _assert_safe_descendant(root, cell, description="managed retention cell")
    relative = cell.relative_to(root).parts
    if (
        len(relative) != 3
        or relative[0] != "cells"
        or not re.fullmatch(r"[0-9a-f]{16}", relative[1])
        or not re.fullmatch(r"[0-9a-f]{40}", relative[2])
    ):
        raise ManagedRuntimeError("managed retention cell path is inconsistent")
    return cell.parent


def _inspect_cell(root: Path, cell: Path, authority: dict | None = None) -> dict:
    _cell_owner(root, cell)
    receipt = _read_metadata(root, cell / RECEIPT_NAME)
    schema = receipt.get("schema_version")
    keys = {
        "schema_version",
        "name",
        "version",
        "profile",
        "content_digest",
        "authority_digest",
        "toolchain_digest",
        "imports",
        "windows_trust_files",
        "snapshot",
        "cell_digest",
    }
    if schema == 2:
        keys.add("ownership")
    if (
        type(schema) is not int
        or schema not in (1, 2)
        or set(receipt) != keys
        or any(
            not _digest(receipt[key])
            for key in ("content_digest", "authority_digest", "toolchain_digest", "cell_digest")
        )
        or any(
            not isinstance(receipt[key], str) or not receipt[key]
            for key in ("name", "version", "profile")
        )
        or not isinstance(receipt["snapshot"], dict)
        or not isinstance(receipt["imports"], list)
        or not isinstance(receipt["windows_trust_files"], list)
        or _cell_key(receipt) != cell.name
    ):
        raise ManagedRuntimeError("managed retention cell receipt is invalid or mismatched")
    if schema == 2:
        owner = receipt["ownership"]
        if (
            not isinstance(owner, dict)
            or set(owner) != {"root", "cell", "authority", "windows"}
            or owner["root"] != str(root)
            or owner["cell"] != str(cell)
            or type(owner["windows"]) is not bool
            or not isinstance(owner["authority"], dict)
            or (authority is not None and owner["authority"] != authority)
        ):
            raise ManagedRuntimeError("managed retention cell ownership is ambiguous")
        authority = owner["authority"]
    if authority is not None:
        if (
            set(authority)
            != {
                "plugin_root",
                "plugin_owner",
                "plugin_source_path",
                "plugin_version",
                "activation_scopes",
                "managed_runtime",
            }
            and set(authority)
            != {
                "plugin_root",
                "plugin_owner",
                "plugin_source_path",
                "plugin_version",
                "activation_scopes",
                "managed_runtime",
                "transition_group",
            }
            or any(
                not isinstance(authority[key], str) or not authority[key]
                for key in ("plugin_root", "plugin_owner", "plugin_source_path", "plugin_version")
            )
            or not isinstance(authority["activation_scopes"], list)
            or _canonical_digest(authority) != receipt["authority_digest"]
            or _plugin_identity(authority) != cell.parent.name
        ):
            raise ManagedRuntimeError("managed retention cell authority is inconsistent")
        try:
            validate_registration(
                "plugin-companion",
                {"command": ["bin/service"], "managed_runtime": authority["managed_runtime"]},
            )
        except RegistrationError as exc:
            raise ManagedRuntimeError(
                "managed retention declaration authority is invalid"
            ) from exc
        declared = [
            item
            for item in authority["managed_runtime"]["runtimes"]
            if item["name"] == receipt["name"]
        ]
        if (
            len(declared) != 1
            or any(declared[0][key] != receipt[key] for key in ("version", "profile", "imports"))
            or _canonical_digest(
                {
                    "runtime": {
                        key: declared[0][key]
                        for key in ("name", "version", "profile", "projects", "imports")
                    },
                    "snapshot": receipt["snapshot"],
                }
            )
            != receipt["content_digest"]
        ):
            raise ManagedRuntimeError("managed retention snapshot authority is inconsistent")
    if _tree_digest(cell, excluded=frozenset({RECEIPT_NAME})) != receipt["cell_digest"]:
        raise ManagedRuntimeError("managed retention cell contents changed")
    return receipt


class ManagedRuntimeRetention:
    """Share leases and durable selected/rollback pins through one physical root."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        policy: RetentionPolicy = RetentionPolicy(),
        domain_source: Callable[[], str] = process_identity_domain,
        token_source: Callable[[int], str | None] | None = None,
        process_exists: Callable[[int], bool] | None = None,
        group_exists: Callable[[int], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        from .companion import _process_exists, _process_group_exists, process_start_token

        self.root = (root if root is not None else managed_runtime_root()).expanduser().absolute()
        self.policy = policy
        self.domain_source = domain_source
        self.token_source = token_source or process_start_token
        self.process_exists = process_exists or _process_exists
        self.group_exists = group_exists or _process_group_exists
        self.clock = clock

    def _scope(self, receipt_dir: Path, registration_id: str) -> dict:
        domain = self.domain_source()
        if not _digest(domain) or not registration_id:
            raise ManagedRuntimeError("managed lease supervisor authority is unavailable")
        return {
            "domain": domain,
            "receipt_dir": str(receipt_dir.expanduser().absolute()),
            "registration_id": registration_id,
        }

    def _reference(self, snapshot: ManagedLaunchSnapshot) -> dict:
        from .companion import _command_digest

        resolution = snapshot.resolution()
        authority, _ = _authority(resolution.registration, require_payload=False)
        cells = []
        for runtime in snapshot.runtimes:
            receipt = _inspect_cell(self.root, runtime.cell, authority)
            windows = receipt.get("ownership", {}).get("windows", os.name == "nt")
            if (
                runtime.receipt != runtime.cell / RECEIPT_NAME
                or runtime.python != _python_path(runtime.cell / "runtime", windows=windows)
                or any(
                    getattr(runtime, key) != receipt[key]
                    for key in ("name", "version", "profile", "content_digest")
                )
            ):
                raise ManagedRuntimeError("managed lease runtime identity is inconsistent")
            cells.append(
                {
                    "cell": str(runtime.cell),
                    "receipt_digest": _hash_regular_file(
                        runtime.receipt, description="managed lease receipt"
                    ),
                }
            )
        return {
            "registration_id": resolution.registration["id"],
            "launch_digest": snapshot.fingerprint,
            "command_digest": _command_digest(resolution.command),
            "authority": authority,
            "cells": cells,
        }

    def _path(self, kind: str, identity: dict) -> Path:
        parent = _safe_directory(self.root, ".retention", kind)
        return parent / f"{_canonical_digest(identity)}.json"

    def _write(self, path: Path, record: dict) -> None:
        _assert_safe_descendant(self.root, path, description="managed retention record")
        write_json_object_atomic(path, record)

    def _check_reference(self, reference: object) -> set[Path]:
        if (
            not isinstance(reference, dict)
            or set(reference)
            != {"registration_id", "launch_digest", "command_digest", "authority", "cells"}
            or not isinstance(reference["registration_id"], str)
            or not reference["registration_id"]
            or not _digest(reference["launch_digest"])
            or not _digest(reference["command_digest"])
            or not isinstance(reference["authority"], dict)
            or not isinstance(reference["cells"], list)
            or not reference["cells"]
        ):
            raise ManagedRuntimeError("managed retention launch reference is malformed")
        cells = set()
        names = set()
        for item in reference["cells"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"cell", "receipt_digest"}
                or not isinstance(item["cell"], str)
                or not _digest(item["receipt_digest"])
            ):
                raise ManagedRuntimeError("managed retention cell reference is malformed")
            cell = Path(item["cell"])
            receipt = _inspect_cell(self.root, cell, reference["authority"])
            if (
                cell in cells
                or receipt["name"] in names
                or _hash_regular_file(cell / RECEIPT_NAME, description="leased receipt")
                != item["receipt_digest"]
            ):
                raise ManagedRuntimeError("managed retention receipt reference is mismatched")
            cells.add(cell)
            names.add(receipt["name"])
        if len(cells) != len(reference["authority"]["managed_runtime"]["runtimes"]):
            raise ManagedRuntimeError("managed retention launch reference is incomplete")
        return cells

    def _check_record(
        self, path: Path, kind: str, *, check_references: bool = True
    ) -> tuple[dict, set[Path]]:
        record = _read_metadata(self.root, path)
        keys = (
            {"schema_version", "root", "scope", "selected", "rollback"}
            if kind == "selections"
            else {
                "schema_version",
                "root",
                "scope",
                "reference",
                "holder",
                "role",
            }
        )
        if (
            set(record) != keys
            or type(record["schema_version"]) is not int
            or record["schema_version"] != 1
            or record["root"] != str(self.root)
        ):
            raise ManagedRuntimeError("managed retention record schema or root is inconsistent")
        scope = record["scope"]
        if (
            not isinstance(scope, dict)
            or set(scope) != {"domain", "receipt_dir", "registration_id"}
            or not _digest(scope["domain"])
            or not isinstance(scope["receipt_dir"], str)
            or not scope["receipt_dir"]
            or not isinstance(scope["registration_id"], str)
            or not scope["registration_id"]
        ):
            raise ManagedRuntimeError("managed retention supervisor scope is malformed")
        if kind == "selections":
            identity = scope
            references = [record["selected"]]
            if record["rollback"] is not None:
                references.append(record["rollback"])
        else:
            holder = record["holder"]
            if (
                record["role"] not in ("preparation", "process")
                or not isinstance(holder, dict)
                or set(holder) != {"pid", "start_token", "domain"}
                or type(holder["pid"]) is not int
                or holder["pid"] <= 0
                or not isinstance(holder["start_token"], str)
                or not holder["start_token"]
                or holder["domain"] != scope["domain"]
            ):
                raise ManagedRuntimeError("managed runtime lease process identity is malformed")
            references = [record["reference"]]
            identity = {key: record[key] for key in ("scope", "reference", "holder", "role")}
        if path.name != f"{_canonical_digest(identity)}.json":
            raise ManagedRuntimeError("managed retention record path is mismatched")
        cells: set[Path] = set()
        if check_references:
            for reference in references:
                cells.update(self._check_reference(reference))
                if reference["registration_id"] != scope["registration_id"]:
                    raise ManagedRuntimeError("managed retention registration scope is mismatched")
        return record, cells

    def _preservation_roots(self, reference: object) -> set[Path]:
        """Exclude an invalid reference's owners, or all cells if opaque."""
        all_cells = {self.root / "cells"}
        if not isinstance(reference, dict):
            return all_cells
        items = reference.get("cells")
        if not isinstance(items, list) or not items:
            return all_cells
        owners: set[Path] = set()
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("cell"), str):
                return all_cells
            try:
                owners.add(_cell_owner(self.root, Path(item["cell"])))
            except (ManagedRuntimeError, OSError, ValueError):
                return all_cells
        return owners

    def _hold(
        self,
        snapshot: ManagedLaunchSnapshot,
        receipt_dir: Path,
        *,
        role: str,
        pid: int,
        token: str,
    ) -> None:
        root = _ensure_safe_root(self.root)
        with _RootLock(root):
            reference = self._reference(snapshot)
            scope = self._scope(receipt_dir, snapshot.to_dict()["registration"]["id"])
            if not token or self.token_source(pid) != token:
                raise ManagedRuntimeError("managed lease process identity cannot be verified")
            identity = {
                "scope": scope,
                "reference": reference,
                "role": role,
                "holder": {"domain": scope["domain"], "pid": pid, "start_token": token},
            }
            path = self._path("leases", identity)
            if path.exists() or path.is_symlink():
                self._check_record(path, "leases")
            self._write(path, {"schema_version": 1, "root": str(root), **identity})

    def prepare(self, snapshot: ManagedLaunchSnapshot, receipt_dir: Path) -> None:
        """Lease a validated replacement before the predecessor can be stopped."""
        token = self.token_source(os.getpid())
        if not token:
            raise ManagedRuntimeError("managed preparation requires supervisor process identity")
        self._hold(snapshot, receipt_dir, role="preparation", pid=os.getpid(), token=token)

    def launched(
        self,
        snapshot: ManagedLaunchSnapshot,
        receipt_dir: Path,
        pid: int,
        token: str,
    ) -> None:
        """Publish the child lease after its process receipt, before releasing its gate."""
        self._hold(snapshot, receipt_dir, role="process", pid=pid, token=token)

    def release_preparation(self, snapshot: ManagedLaunchSnapshot, receipt_dir: Path) -> None:
        """Idempotently release this exact supervisor/snapshot lease, never peer records."""
        root = _ensure_safe_root(self.root)
        with _RootLock(root):
            scope = self._scope(receipt_dir, snapshot.to_dict()["registration"]["id"])
            token = self.token_source(os.getpid())
            if not token:
                raise ManagedRuntimeError("managed preparation release identity is unavailable")
            identity = {
                "scope": scope,
                "reference": self._reference(snapshot),
                "role": "preparation",
                "holder": {"domain": scope["domain"], "pid": os.getpid(), "start_token": token},
            }
            path = self._path("leases", identity)
            if path.exists() or path.is_symlink():
                self._check_record(path, "leases")
                path.unlink()

    def select(self, snapshot: ManagedLaunchSnapshot, receipt_dir: Path) -> None:
        """Persist selected and prior-ready rollback pins before external selection."""
        root = _ensure_safe_root(self.root)
        with _RootLock(root):
            scope = self._scope(receipt_dir, snapshot.to_dict()["registration"]["id"])
            reference = self._reference(snapshot)
            path = self._path("selections", scope)
            previous = None
            if path.exists() or path.is_symlink():
                previous, _ = self._check_record(path, "selections")
            rollback = previous["selected"] if previous else None
            if previous and previous["selected"] == reference:
                rollback = previous["rollback"]
            self._write(
                path,
                {
                    "schema_version": 1,
                    "root": str(root),
                    "scope": scope,
                    "selected": reference,
                    "rollback": rollback,
                },
            )

    def forget(self, registration_id: str, receipt_dir: Path) -> None:
        """Withdraw only this supervisor environment's persistent selection."""
        with self.withdraw_selection(registration_id, receipt_dir):
            pass

    @contextmanager
    def withdraw_selection(self, registration_id: str, receipt_dir: Path) -> Iterator[None]:
        """Fence external selection withdrawal and remove its root pin only afterward."""
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            yield
            return
        root = _ensure_safe_root(self.root)
        with _RootLock(root):
            scope = self._scope(receipt_dir, registration_id)
            path = self._path("selections", scope)
            present = path.exists() or path.is_symlink()
            if present:
                record, _ = self._check_record(path, "selections")
                if record["scope"] != scope:
                    raise ManagedRuntimeError("cannot withdraw a foreign managed selection")
            yield
            if present:
                path.unlink()

    def _stale(self, record: dict, domain: str) -> bool:
        holder = record["holder"]
        if holder["domain"] != domain:
            return False
        current = self.token_source(holder["pid"])
        if current == holder["start_token"]:
            return False
        if (
            current is not None
            or self.process_exists(holder["pid"])
            or self.group_exists(holder["pid"])
        ):
            raise ManagedRuntimeError(
                "managed lease process identity is ambiguous; preserving runtimes"
            )
        return True

    def _launch_state(self, scope: dict) -> tuple[set[Path], set[Path]]:
        from .companion import ManagedLaunchSnapshot, companion_receipt_path

        receipt_dir = Path(scope["receipt_dir"])
        if not receipt_dir.is_absolute():
            raise ManagedRuntimeError("managed retention launch-state path is not absolute")
        process_path = companion_receipt_path(receipt_dir, scope["registration_id"])
        protected: set[Path] = set()
        preservation_roots: set[Path] = set()
        for path in (process_path, process_path.with_suffix(".managed-launch.json")):
            _assert_safe_descendant(receipt_dir, path, description="managed selected launch state")
            if not path.exists():
                continue
            record = _read_metadata(receipt_dir, path)
            if path == process_path and "managed_snapshot" not in record:
                if (
                    set(record)
                    != {
                        "schema_version",
                        "registration_id",
                        "fingerprint",
                        "pid",
                        "start_token",
                        "command_digest",
                        "runtime_revision",
                        "containment",
                    }
                    or type(record["schema_version"]) is not int
                    or record["schema_version"] != 1
                    or record["registration_id"] != scope["registration_id"]
                    or type(record["pid"]) is not int
                    or record["pid"] <= 0
                    or not isinstance(record["start_token"], str)
                    or not record["start_token"]
                    or not _digest(record["command_digest"])
                    or not isinstance(record["fingerprint"], str)
                    or not record["fingerprint"]
                    or record["containment"] not in ("windows-job", "posix-process-group")
                    or (
                        record["runtime_revision"] is not None
                        and (
                            not isinstance(record["runtime_revision"], dict)
                            or "managed_runtime" in record["runtime_revision"]
                        )
                    )
                ):
                    raise ManagedRuntimeError(
                        "managed lease scope has an ambiguous successor receipt"
                    )
                continue
            snapshot = ManagedLaunchSnapshot.from_dict(record.get("managed_snapshot"))
            try:
                reference = self._reference(snapshot)
            except (ManagedRuntimeError, OSError) as exc:
                preservation_roots.update(
                    self._preservation_roots({"cells": snapshot.to_dict()["runtimes"]})
                )
                logging.getLogger("agent-dispatch.companion").warning(
                    "preserving managed launch receipt with invalid reference %s "
                    "and protecting its scope: %s", path, exc
                )
                continue
            if (
                type(record.get("schema_version")) is not int
                or record["schema_version"] != 1
                or record.get("registration_id") != scope["registration_id"]
                or reference["registration_id"] != scope["registration_id"]
                or record.get("fingerprint") != reference["launch_digest"]
            ):
                raise ManagedRuntimeError("managed selected launch receipt is inconsistent")
            if path == process_path and (
                type(record.get("pid")) is not int
                or record["pid"] <= 0
                or not isinstance(record.get("start_token"), str)
                or not record["start_token"]
                or record.get("command_digest") != reference["command_digest"]
                or record.get("runtime_revision") != reference["authority"]
                or record.get("containment") not in ("windows-job", "posix-process-group")
            ):
                raise ManagedRuntimeError("managed process receipt authority is inconsistent")
            protected.update(Path(item["cell"]) for item in reference["cells"])
        return protected, preservation_roots

    def _transition_group_state(self, receipt_dir: Path) -> tuple[set[Path], set[Path]]:
        from .companion import ManagedLaunchSnapshot, transition_group_receipt_path

        protected: set[Path] = set()
        preservation_roots: set[Path] = set()
        if not receipt_dir.exists():
            return protected, preservation_roots
        for path in sorted(receipt_dir.glob("*.managed-transition-group.json")):
            _assert_safe_descendant(
                receipt_dir, path, description="managed transition group state"
            )
            record = _read_metadata(receipt_dir, path)
            if (
                type(record.get("schema_version")) is not int
                or record["schema_version"] != 1
                or not isinstance(record.get("group_id"), str)
                or not record["group_id"]
                or path != transition_group_receipt_path(receipt_dir, record["group_id"])
                or not isinstance(record.get("members"), list)
                or sorted(record["members"]) != record["members"]
                or not all(isinstance(member, str) and member for member in record["members"])
                or not isinstance(record.get("selected"), dict)
                or set(record["selected"]) != set(record["members"])
            ):
                raise ManagedRuntimeError("managed transition group receipt is malformed")
            for field in ("selected", "rollback", "pending"):
                value = record.get(field)
                if value is None and field != "selected":
                    continue
                if not isinstance(value, dict) or set(value) != set(record["members"]):
                    raise ManagedRuntimeError(
                        "managed transition group snapshot membership is inconsistent"
                    )
                for registration_id, raw_snapshot in value.items():
                    snapshot = ManagedLaunchSnapshot.from_dict(raw_snapshot)
                    if snapshot.to_dict()["registration"]["id"] != registration_id:
                        raise ManagedRuntimeError(
                            "managed transition group snapshot identity is inconsistent"
                        )
                    try:
                        reference = self._reference(snapshot)
                    except (ManagedRuntimeError, OSError) as exc:
                        preservation_roots.update(
                            self._preservation_roots(
                                {"cells": snapshot.to_dict()["runtimes"]}
                            )
                        )
                        logging.getLogger("agent-dispatch.companion").warning(
                            "preserving managed transition group receipt with invalid "
                            "reference %s: %s",
                            path,
                            exc,
                        )
                        continue
                    protected.update(Path(item["cell"]) for item in reference["cells"])
        return protected, preservation_roots

    def cleanup(self) -> CleanupResult:
        """Reclaim only fully attested, unreferenced cells after a complete preflight."""
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            return CleanupResult((), (), 0)
        root = _ensure_safe_root(self.root)
        with _RootLock(root):
            _ordinary_tree(root)
            metadata_root = root / ".retention"
            if metadata_root.exists() and any(
                path.name not in ("leases", "selections") or not path.is_dir()
                for path in metadata_root.iterdir()
            ):
                raise ManagedRuntimeError("managed retention metadata layout is ambiguous")
            domain = self.domain_source()
            if not _digest(domain):
                raise ManagedRuntimeError("managed cleanup process authority is unavailable")
            protected: set[Path] = set()
            stale: list[tuple[Path, str, dict]] = []
            scopes: dict[str, dict] = {}
            selected_scopes: set[str] = set()
            for kind in ("leases", "selections"):
                parent = root / ".retention" / kind
                if not parent.exists():
                    continue
                for path in sorted(parent.iterdir()):
                    record, cells = self._check_record(
                        path, kind, check_references=kind != "leases"
                    )
                    scope_key = _canonical_digest(record["scope"])
                    scopes[scope_key] = record["scope"]
                    if kind == "leases" and self._stale(record, domain):
                        stale.append((path, scope_key, record))
                    else:
                        if kind == "leases":
                            _, cells = self._check_record(path, kind)
                        protected.update(cells)
                    if kind == "selections":
                        selected_scopes.add(scope_key)
            interrupted_scopes: set[str] = set()
            preservation_roots: set[Path] = set()
            receipt_dirs = {
                Path(scope["receipt_dir"])
                for scope in scopes.values()
                if isinstance(scope.get("receipt_dir"), str)
            }
            for scope_key, scope in scopes.items():
                if scope["domain"] == domain:
                    launch_cells, launch_roots = self._launch_state(scope)
                    protected.update(launch_cells)
                    preservation_roots.update(launch_roots)
                    if launch_roots or (launch_cells and scope_key not in selected_scopes):
                        interrupted_scopes.add(scope_key)
            for receipt_dir in sorted(receipt_dirs):
                group_cells, group_roots = self._transition_group_state(receipt_dir)
                protected.update(group_cells)
                preservation_roots.update(group_roots)
            # A gated first-launch receipt is selected state too. Keep its discovery
            # lease until recovery retires it or publishes a durable selection.
            stale_paths: list[Path] = []
            for path, scope_key, record in stale:
                try:
                    _, cells = self._check_record(path, "leases")
                except (ManagedRuntimeError, OSError) as exc:
                    preservation_roots.update(self._preservation_roots(record["reference"]))
                    logging.getLogger("agent-dispatch.companion").warning(
                        "preserving stale managed lease with invalid reference %s: %s", path, exc
                    )
                    continue
                if scope_key in interrupted_scopes:
                    protected.update(cells)
                else:
                    stale_paths.append(path)
            candidates: dict[tuple[str, str, str], list[tuple[float, Path]]] = {}
            cells_root = root / "cells"
            if cells_root.exists():
                for owner in sorted(cells_root.iterdir()):
                    if not owner.is_dir() or not re.fullmatch(r"[0-9a-f]{16}", owner.name):
                        raise ManagedRuntimeError("managed runtime owner directory is ambiguous")
                    for cell in sorted(owner.iterdir()):
                        if any(cell.is_relative_to(parent) for parent in preservation_roots):
                            protected.add(cell)
                            continue
                        receipt = _inspect_cell(root, cell)
                        if (
                            receipt["schema_version"] == 1
                            or cell in protected
                        ):
                            protected.add(cell)
                            continue
                        key = (owner.name, receipt["name"], receipt["profile"])
                        candidates.setdefault(key, []).append(
                            ((cell / RECEIPT_NAME).stat().st_mtime, cell)
                        )
            now = self.clock()
            if not isinstance(now, (int, float)) or not 0 < now < float("inf"):
                raise ManagedRuntimeError("managed retention clock is invalid")
            delete = []
            for entries in candidates.values():
                for index, (created, cell) in enumerate(sorted(entries, reverse=True)):
                    if (
                        index < self.policy.keep_generations
                        or now - created < self.policy.minimum_age_seconds
                    ):
                        protected.add(cell)
                    else:
                        delete.append(cell)
            # No mutation precedes the full preflight, including conservative exclusions.
            for path in stale_paths:
                path.unlink()
            deleted: list[Path] = []
            for cell in delete:
                _assert_safe_descendant(root, cell, description="managed cleanup target")
                _inspect_cell(root, cell)
                parent = _safe_directory(root, ".deleting")
                staging = parent / uuid.uuid4().hex
                _assert_safe_descendant(root, staging, description="managed deletion staging")
                if staging.exists():
                    raise ManagedRuntimeError("managed deletion staging destination already exists")
                try:
                    os.replace(cell, staging)
                except OSError as exc:
                    logging.getLogger("agent-dispatch.companion").warning(
                        "cannot unpublish managed cell; preserving %s: %s", cell, exc
                    )
                    protected.add(cell)
                    continue
                deleted.append(cell)
                _remove_deletion_staging(root, staging)
            return CleanupResult(tuple(deleted), tuple(sorted(protected)), len(stale_paths))
