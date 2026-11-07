"""Deployment evidence from the retained installed build, not today's checkout."""

from __future__ import annotations

import json
import os
import platform
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from packaging.version import Version


def manifest_bytes(identity: dict, slot: Path, payload_hash: str) -> bytes:
    info = identity["build_info"]
    package_version = info["version"]
    if Version(package_version) != Version(identity["package_version"]):
        raise RuntimeError("Installed provenance differs from its distribution version")
    serialized_source = info.get("deployment_source")
    application_source = False
    if serialized_source is None:
        print(
            "Restoring legacy build provenance; payload fingerprint is unknown.",
            file=sys.stderr,
        )
        source = {
            "kind": "unknown", "path": info.get("source"), "repo": "copilot-extensions",
            "plugin": "agent-worktrees", "version": package_version,
            "commit": info.get("commit"), "branch": info.get("branch"),
            "dirty": None, "payload_fingerprint": None,
        }
    else:
        source = json.loads(serialized_source)
        application_source = (
            isinstance(source, dict) and source.get("schema") == "copilot-extensions.runtime-source"
        )
        if application_source:
            source = {
                "kind": "source", "path": info["source"], "repo": "copilot-extensions",
                "plugin": "agent-worktrees", "version": package_version,
                "commit": info["commit"], "branch": info["branch"], "dirty": None,
                "payload_fingerprint": None,
                "runtime_version": source["runtime_version"], "runtime_source": source,
            }
        if (
            not isinstance(source, dict) or source.get("version") != package_version
            or source.get("plugin") != "agent-worktrees"
            or not isinstance(source.get("path"), str)
            or not (
                isinstance(source.get("payload_fingerprint"), str)
                or application_source and source.get("payload_fingerprint") is None
            )
        ):
            raise RuntimeError("Installed deployment provenance is malformed")
    manifest = {
        "schema_version": 3,
        "service": "agent-worktrees",
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "deployed_by": f"{platform.node().lower()}-{platform.system().lower()}",
        "source": source,
        "venv": (slot / ".venv" if application_source else slot).as_posix(),
        "runtime": "python",
        "runtime_slot": slot.name,
        "runtime_generation": info.get("runtime_generation"),
        "payload_hash": payload_hash,
    }
    if "runtime_source" in source:
        document = source["runtime_source"]
        if (
            not isinstance(document, dict) or document.get("plugin") != "agent-worktrees"
            or document.get("package_version") != package_version
            or source.get("runtime_version") != document.get("runtime_version")
        ):
            raise RuntimeError("Installed source-qualified deployment provenance is malformed")
        manifest.update(runtime_version=slot.name, runtime_source=document)
    return (json.dumps(manifest, indent=2) + "\n").encode("utf-8")


def start_published_application() -> int:
    from .status_monitor_cutover import start_application

    with application_publication():
        status = start_application()
        if status:
            raise RuntimeError(f"Application monitor startup failed with exit code {status}")
        return status


@contextmanager
def application_publication() -> Iterator[None]:
    """Complete application startup through the original command owner's lease."""
    from . import _command_binding as binding, config
    from ._build_info import BUILD_INFO
    from .runtime_candidate import _native

    environment = Path(sys.prefix).resolve()
    root = config.install_dir().resolve()
    slot = root / "versions" / environment.parent.name
    if environment.name != ".venv" or not _native(slot / ".venv").samefile(environment):
        raise RuntimeError("Application publication requires its installed version slot")
    declaration = json.loads(_native(slot / "application.json").read_text(encoding="utf-8"))
    construction = declaration["construction"]
    content = manifest_bytes({
        "package_version": version("agent-worktrees"), "build_info": BUILD_INFO,
    }, slot, construction["payload_hash"])
    manifest = json.loads(content)
    if (
        declaration["name"] != "agent-worktrees"
        or manifest.get("runtime_source") != construction["identity"]["runtime_source"]
        or BUILD_INFO["runtime_version"] != slot.name
    ):
        raise RuntimeError("Application deployment differs from its installed Source identity")
    owner = Path(os.environ["COPILOT_PLUGIN_STAGED_FROM"])
    if not owner.is_absolute():
        raise ValueError("Application publication requires its absolute original command owner")
    primitive = binding._load_primitive(owner)
    arguments = {
        "payload_root": owner, "plugin_id": "agent-worktrees", "legacy_root": root,
        "project_root": Path(os.environ["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"])
        if os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT") else None,
    }
    lifecycle = binding._load_lifecycle()
    snapshot = read_manifest(root)
    previous = json.loads(snapshot) if snapshot is not None else {}

    def selected() -> bool:
        return (root / "current-version").read_text(encoding="utf-8").strip() == slot.name

    if (
        not os.environ.get(lifecycle.TICKET_ENV) and selected()
        and all(previous.get(key) == value for key, value in manifest.items()
                if key not in {"deployed_at", "deployed_by"})
        and binding.resolve_command_binding(primitive, **arguments)["status"] == "selected"
    ):
        # Completed-slot reuse does not acquire management authority or rewrite evidence.
        yield
        if read_manifest(root) != snapshot or not selected():
            raise RuntimeError("Application deployment changed during completed-slot reuse")
        return
    with lifecycle.legacy_lifecycle(
        binding._rules(), primitive, reserve=False, **arguments,
    ) as lease:
        with binding.legacy_manifest_publication(
            primitive, manifest, **arguments,
        ) as (bound, result, _lock):
            if result["status"] != "published":
                raise RuntimeError("Application command ownership could not be published")
            yield
            lease.assert_owned()
            if read_manifest(root) != snapshot or not selected():
                raise RuntimeError("Application deployment changed during startup")
            publish(root, (json.dumps(bound, indent=2) + "\n").encode("utf-8"))


def read_manifest(root: Path) -> bytes | None:
    try:
        return (root / "deploy-manifest.json").read_bytes()
    except FileNotFoundError:
        return None


def publish(root: Path, content: bytes) -> None:
    path = root / "deploy-manifest.json"
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(root: Path, snapshot: bytes | None, candidate: bytes) -> None:
    if read_manifest(root) not in (snapshot, candidate):
        raise RuntimeError("Deployment evidence changed before rollback")
    if snapshot is None:
        (root / "deploy-manifest.json").unlink(missing_ok=True)
    else:
        publish(root, snapshot)
