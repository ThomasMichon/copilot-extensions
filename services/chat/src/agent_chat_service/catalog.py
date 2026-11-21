"""Offline invocation evidence for this installed distribution, not release selection."""

from __future__ import annotations

import base64
import hashlib
from importlib import metadata as metadata
from pathlib import Path
import stat as stat
import sys as sys
import sysconfig as sysconfig
from typing import Any, NoReturn, cast

from . import __version__ as __version__
from .client import ClientError

MAX_RESOURCE_BYTES = 1024 * 1024
PACKAGE_FILES = (
    "__init__.py", "catalog.py", "cli.py",
    "client.py", "store.py", "transport.py", "hosting.py", "workspace.py", "collaboration.py",
    "construction.py", "setup.py",
    "_versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_publication.py", "versioned_runtime_source.py", "versioned_runtime_cache.py",
    "versioned_runtime_identity.py", "versioned_runtime_fingerprint.py",
    "installer_engine.py", "installer_engine_health.py", "installer_engine_process.py",
    "installer_dependency_policy.py", "installer_dependency_config.py", "installer_retained_content.py",
    "installer_dependency_arguments.py",
    "installer_wheel_inputs.py", "installer_wheel_snapshot.py", "runtime_launcher.py",
    "installer_source_wheel_cache.py", "installer_source_build.py", "installer_provider_roots.py",
)


def _refuse() -> NoReturn:
    raise ClientError(
        "invalid_installation",
        "Catalog requires an ordinary interpreter-local wheel installation with intact RECORD resources.",
    )


def _resource(path: Path) -> bytes:
    for current in (*reversed(path.parents), path):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            _refuse()
        if current != path and not stat.S_ISDIR(info.st_mode):
            _refuse()
    if not stat.S_ISREG(info.st_mode):
        _refuse()
    with path.open("rb") as stream:
        data = stream.read(MAX_RESOURCE_BYTES + 1)
    if len(data) > MAX_RESOURCE_BYTES:
        _refuse()
    return data


def _path_key(path: Path) -> Path:
    """Compare Windows verbatim aliases without changing resource I/O paths."""
    spelling = str(path.absolute())
    if sys.platform == "win32":
        if spelling.upper().startswith("\\\\?\\UNC\\"):
            spelling = "\\\\" + spelling[8:]
        elif spelling.startswith("\\\\?\\") and len(spelling) >= 7 and spelling[5:7] == ":\\":
            spelling = spelling[4:]
    return Path(spelling)


def installed_catalog() -> dict[str, Any]:
    """Return exact isolated argv only after checking the imported installed closure."""
    try:
        distribution = metadata.distribution("agent-chat-service")
        if distribution.version != __version__.replace("-dev", ".dev"):
            _refuse()
        entries = distribution.files
        if entries is None:
            _refuse()
        commands = [
            entry for entry in distribution.entry_points
            if entry.group == "console_scripts" and entry.name == "agent-chat"
        ]
        if len(commands) != 1 or commands[0].value != "agent_chat_service.cli:main":
            _refuse()
        package = Path(__file__).absolute().parent
        sites = {_path_key(Path(sysconfig.get_path(key))) for key in ("purelib", "platlib")}
        if _path_key(package.parent) not in sites:
            _refuse()
        if {item.name for item in package.glob("*.py")} != set(PACKAGE_FILES):
            _refuse()
        digests = {}
        for name in PACKAGE_FILES:
            member = "agent_chat_service/" + name
            matches = [entry for entry in entries if str(entry) == member]
            if len(matches) != 1:
                _refuse()
            entry = matches[0]
            if entry.hash is None or entry.hash.mode != "sha256":
                _refuse()
            # SimplePath does not promise PathLike; Path retains the validating conversion.
            path = Path(cast(Any, distribution.locate_file(entry))).absolute()
            if _path_key(path) != _path_key(package / name):
                _refuse()
            data = _resource(path)
            digest = hashlib.sha256(data).digest()
            encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if entry.size != len(data) or entry.hash.value != encoded:
                _refuse()
            digests[name] = digest.hex()
        interpreter = Path(sys.executable)
        if not interpreter.is_absolute() or not interpreter.is_file():
            _refuse()
    except metadata.PackageNotFoundError:
        raise ClientError("client_not_installed", "The client distribution is not installed.") from None
    except (OSError, ValueError, TypeError):
        raise ClientError("invalid_installation", "Installed client evidence could not be read.") from None
    # Preserve the venv interpreter spelling: resolving its symlink selects the base Python.
    return {
        "schema": "agent-chat.client-catalog", "version": 1,
        "distribution": {"name": "agent-chat-service", "version": distribution.version},
        "evidence": "local-installed-resource-record",
        "resource_sha256s": digests,
        "commands": [{
            "id": "agent-chat",
            "argv": [str(interpreter), "-I", "-m", "agent_chat_service.cli"],
            "shell": "direct", "availability": "ready",
        }],
        "release_selection_verified": False, "remote_verified": False,
        "native_listening_verified": False, "mutations_performed": False,
    }
