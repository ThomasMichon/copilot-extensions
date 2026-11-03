"""Operational identity of an installed source-qualified runtime."""
from __future__ import annotations

import re
import sys
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from agent_procutil._daemon_instance_identity import runtime_slot_paths

log = logging.getLogger("agent-codespaces")

_SOURCE_VERSION = re.compile(
    r"(?P<package>\d+\.\d+\.\d+(?:[.-]dev\d+)?"
    r"(?:\+[A-Za-z0-9][A-Za-z0-9._-]*)?)[.+]"
    r"source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}"
    r"(?:\.profile\.[0-9a-f]{16})?(?:[.+]repair\.[0-9a-f]{32})?"
)


def effective_runtime_version(
    package_version: str, *, prefix: Path | None = None, module_path: Path | None = None,
) -> str:
    candidate = prefix or Path(sys.prefix)
    layout = runtime_slot_paths(candidate)
    if layout is None or "+" not in layout[0]:
        return package_version
    # Numeric hash components must retain their opaque leading zeroes.
    match = _SOURCE_VERSION.fullmatch(layout[0])
    if not match:
        return package_version
    package = package_version.lower().replace("-", ".").replace("_", ".")
    selected = match["package"].lower().replace("-", ".").replace("_", ".")
    if package != selected:
        return package_version
    if not (module_path or Path(__file__)).resolve().is_relative_to(candidate.resolve()):
        return package_version
    return layout[0]


@dataclass(frozen=True)
class OwnerRuntimeSelection:
    """Frozen installed-slot provenance, never inferred from a peer's version."""

    root: Path
    identity: str
    runtime: ModuleType

    def selected_identity(self) -> str | None:
        """Read a principal-owned selection with this birth slot's retained helper."""
        try:
            proof = self.runtime._runtime_component("versioned_runtime_publication")
            selector = self.root / "current-version"
            before = proof.state_path(selector, private=True)
            version = selector.read_text(encoding="utf-8").strip() if before else ""
            identity = _owner_slot_identity(self.runtime, self.root, version)
            if before is None or before != proof.state_path(selector, private=True):
                return None
            return identity
        except (OSError, ValueError, RuntimeError):
            return None

    def superseded(self) -> bool:
        selected = self.selected_identity()
        return selected is not None and selected != self.identity


def _owner_slot_identity(runtime: ModuleType, root: Path, version: str) -> str | None:
    if not version or Path(version).name != version or version in {".", "..", "dev"}:
        return None
    slot = root / "versions" / version
    proof = runtime._runtime_component("versioned_runtime_publication")
    marker = slot / ".install-complete.json"
    before = proof.state_path(marker, private=True)
    completion = runtime.read_marker(root, version)
    if before is None or not completion or not completion.get("payload_hash"):
        return None
    if before != proof.state_path(marker, private=True):
        return None
    physical = slot.stat()
    # Identity inequality, including rollback/repair; no package-version ordering.
    return f"{slot}:{physical.st_dev}:{physical.st_ino}:{before.digest}"


def capture_owner_runtime(root: Path) -> OwnerRuntimeSelection | None:
    """Capture only this installed interpreter's immutable slot, once at birth.

    Source/legacy layouts retain their previous behavior. Unknown selection
    proof is not a request to stop an owner or a code-installation veto.
    """
    prefix = Path(sys.prefix).resolve()
    layout = runtime_slot_paths(prefix)
    root = root.resolve()
    if (layout is None or layout[2] != root
            or not Path(__file__).resolve().is_relative_to(prefix)):
        return None
    slot = Path(layout[1])
    helper = slot / ".runtime-bootstrap" / "versioned_runtime.py"
    try:
        spec = importlib.util.spec_from_file_location("_codespaces_owner_runtime", helper)
        if spec is None or spec.loader is None:
            return None
        runtime = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = runtime
        spec.loader.exec_module(runtime)
        identity = _owner_slot_identity(runtime, root, slot.name)
        return OwnerRuntimeSelection(root, identity, runtime) if identity else None
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        log.warning("Connection Owner slot attribution unavailable: %s", error)
        return None
