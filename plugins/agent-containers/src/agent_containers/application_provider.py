"""Plain installed-application contribution, separate from payload attribution."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import tempfile

from dropin_registry import atomic_write_text
from plugin_resolve import split_source

MANIFEST_NAME = "agent-containers.standalone.json"


def _normalized_path(path: Path) -> str:
    raw = os.path.abspath(os.fspath(path.expanduser()))
    if os.name == "nt":
        return os.path.normcase(raw).replace("/", "\\")
    return os.path.normpath(raw)


def _document(path: Path, *, limit: int | None = 65536):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ValueError(f"provider entry is not an ordinary file: {path}")
    with path.open(encoding="utf-8-sig") as stream:
        content = stream.read(limit + 1) if limit is not None else stream.read()
    if limit is not None and len(content) > limit:
        raise ValueError(f"provider entry exceeds its read bound: {path}")
    return json.loads(content)


def register_installed_application() -> Path:
    """Publish only a new plain entry; never replace another producer's entry."""
    override = os.environ.get("AGENT_BRIDGE_PROVIDERS_DIR")
    config = os.environ.get("AGENT_BRIDGE_CONFIG_DIR")
    install = os.environ.get("AGENT_BRIDGE_INSTALL_DIR")
    config_root = Path(config or install or Path.home() / ".agent-bridge").expanduser()
    directory = (
        Path(override).expanduser() if override else config_root / "providers.d"
    )
    if (
        directory.name == "providers.d"
        and _normalized_path(directory.parent) != _normalized_path(config_root)
    ):
        raise RuntimeError(f"Provider registry targets a different Bridge installation: {directory}")
    try:
        root_info = directory.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RuntimeError(f"Cannot inspect provider registry: {directory}") from error
    else:
        if (
            not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode)
            or getattr(root_info, "st_file_attributes", 0) & 0x400
            or getattr(root_info, "st_reparse_tag", 0)
        ):
            raise RuntimeError(f"Provider registry must be a directory, not a link or reparse point: {directory}")
    target = directory / MANIFEST_NAME
    command = Path.home() / ".local" / "bin" / (
        "agent-containers.cmd" if os.name == "nt" else "agent-containers"
    )
    payload = {
        "schema_version": 0,
        "namespace": "container",
        "command": [str(command)],
        "restricted": True,
        "description": "Installed agent-containers application",
    }

    def check_target() -> bool:
        try:
            existing = _document(target)
        except FileNotFoundError:
            return False
        except ValueError as error:
            raise RuntimeError(f"Refusing to replace provider entry: {target}") from error
        if (
            existing != payload or type(existing.get("schema_version")) is not int
            or type(existing.get("restricted")) is not bool
        ):
            raise RuntimeError(f"Refusing to replace another provider entry: {target}")
        return True

    try:
        present = check_target()
        try:
            entries = sorted(directory.iterdir())
        except FileNotFoundError:
            entries = []
        for path in entries:
            if path.suffix != ".json" or path == target:
                continue
            try:
                other = _document(path, limit=None)
            except FileNotFoundError:
                continue
            except ValueError:
                continue  # The consumer likewise isolates malformed peer entries.
            if not isinstance(other, dict) or not isinstance(other.get("namespace"), str):
                continue
            if other["namespace"].strip().rstrip(":") != "container":
                continue
            name, marketplace = split_source(
                other["plugin"] if isinstance(other.get("plugin"), str) else "",
            )
            own_payload = (
                type(other.get("schema_version")) is int and other["schema_version"] == 1
                and name == "agent-containers" and bool(marketplace)
                and isinstance(other.get("plugin_root"), str) and bool(other["plugin_root"])
            )
            # Bridge selects eligible entries in filename order. A payload entry
            # must retain precedence, without asking whether its plugin is enabled.
            if own_payload and str(path) < str(target):
                continue
            raise RuntimeError(f"Existing container namespace provider must be preserved: {path}")
        if present:
            return target
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=directory, prefix=f".{MANIFEST_NAME}.", suffix=".pending",
        )
        os.close(descriptor)
        staged = Path(name)
        try:
            atomic_write_text(staged, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            try:
                # Atomic create-only publication cannot downgrade a concurrently
                # written identity-bearing entry at this same destination.
                os.link(staged, target)
            except FileExistsError:
                if not check_target():
                    raise RuntimeError(f"Provider entry changed during publication: {target}")
        finally:
            staged.unlink(missing_ok=True)
        return target
    except OSError as error:
        raise RuntimeError(f"Cannot publish installed application provider: {target}") from error


def add_install_parsers(sub, *, config_loader) -> None:
    def source_callback(args) -> int:
        config_loader(strict=True)
        if args.command == "__source-setup":
            register_installed_application()
        return 0

    for command in ("__source-setup", "__source-start"):
        sub.add_parser(command, help=argparse.SUPPRESS).set_defaults(func=source_callback)
    sub.add_parser(
        "installer-readiness",
        help="Emit the plugin-owned installer/readiness contract state as JSON",
    )
