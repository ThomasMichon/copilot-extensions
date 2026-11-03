"""Plain installed-application registration, separate from payload attribution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

from dropin_registry import atomic_write_text
from plugin_resolve import split_source

MANIFEST_NAME = "agent-codespaces.standalone.json"


def _document(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise RuntimeError("CodeSpaces provider declaration must be an ordinary file")
    if info.st_size > 65536:
        raise ValueError("CodeSpaces provider declaration exceeds its byte bound")
    with path.open(encoding="utf-8-sig") as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError("CodeSpaces provider declaration exceeds its byte bound")
    return json.loads(content)


def register_installed_application() -> Path:
    """Create only this app's plain contribution; preserve all other producers."""
    override = os.environ.get("AGENT_BRIDGE_PROVIDERS_DIR")
    directory = (
        Path(override).expanduser() if override else
        Path(
            os.environ.get("AGENT_BRIDGE_CONFIG_DIR")
            or os.environ.get("AGENT_BRIDGE_INSTALL_DIR")
            or Path.home() / ".agent-bridge"
        ).expanduser()
        / "providers.d"
    )
    target = directory / MANIFEST_NAME
    command = Path.home() / ".local" / "bin" / (
        "agent-codespaces.cmd" if os.name == "nt" else "agent-codespaces"
    )
    payload = {
        "schema_version": 0, "namespace": "codespace", "restricted": False,
        "description": "Installed agent-codespaces application", "command": [str(command)],
    }

    def check_target() -> bool:
        try:
            existing = _document(target)
        except FileNotFoundError:
            return False
        except ValueError as error:
            raise RuntimeError("Existing CodeSpaces provider requires its owner's migration") from error
        if (
            existing != payload or type(existing.get("schema_version")) is not int
            or type(existing.get("restricted")) is not bool
        ):
            raise RuntimeError("Existing CodeSpaces provider requires its owner's migration")
        return True

    try:
        for parent in (directory, *directory.parents):
            if parent.exists() or parent.is_symlink():
                info = parent.lstat()
                if (not stat.S_ISDIR(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400):
                    raise RuntimeError("CodeSpaces provider directory must have ordinary ancestors")
        present = check_target()
        try:
            peers = sorted(directory.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            peers = []
        for path in peers:
            if path.suffix != ".json":
                continue
            if path == target:
                continue
            try:
                other = _document(path)
            except FileNotFoundError:
                continue
            except ValueError:
                continue
            if not isinstance(other, dict) or not isinstance(other.get("namespace"), str):
                continue
            if other["namespace"].strip().rstrip(":") != "codespace":
                continue
            identity_bearing = (
                type(other.get("schema_version")) is int and other["schema_version"] == 1
                and isinstance(other.get("plugin"), str) and "@" in other["plugin"]
                and isinstance(other.get("plugin_root"), str) and bool(other["plugin_root"])
            )
            own_payload = False
            if identity_bearing:
                name, marketplace = split_source(other["plugin"])
                own_payload = name == "agent-codespaces" and bool(marketplace)
            # Only this app's payload may precede its plain fallback.
            if own_payload and path.name < target.name:
                continue
            raise RuntimeError(f"Existing codespace namespace provider must be preserved: {path}")
        if present:
            return target
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=directory, prefix=f".{MANIFEST_NAME}.", suffix=".pending",
        )
        os.close(descriptor)
        staged = Path(name)
        try:
            atomic_write_text(staged, json.dumps(payload, sort_keys=True, indent=2) + "\n")
            try:
                os.link(staged, target)
            except FileExistsError:
                if not check_target():
                    raise RuntimeError("CodeSpaces provider changed during publication")
        finally:
            staged.unlink(missing_ok=True)
        return target
    except OSError as error:
        raise RuntimeError("Cannot publish installed CodeSpaces application provider") from error
