"""Fixed, MCP-owned receiving executable; never serialize a callback or config."""

from __future__ import annotations

import hashlib
from contextlib import redirect_stdout
import io
from importlib.resources import files
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

_MCP = (
    "__init__.py", "owner_headed.py", "owner_overlay_pipe.py", "owner_exec.py",
    "owner_section.py", "owner_section_native.py", "owner_snapshot.py", "owner_bootstrap.py",
)
_ZDD = (
    "__init__.py", "private_windows_native.py", "private_windows.py",
    "private_protocol.py", "private_peer.py", "darwin_identity.py",
)


def build_headed_bundle(directory: Path) -> tuple[Path, str]:
    """Retain the selected core and a fixed reviewed MCP producer closure."""
    from agent_remote_driver.session_host.bundle import _sources
    from agent_remote_driver.session_host.launcher import main

    output = io.StringIO()
    with redirect_stdout(output):
        result = main(["--support-json"])
    support = json.loads(output.getvalue())
    if result != 0 or "explicit-inprocess-v1" not in support.get("headed_receivers", []):
        raise ValueError("Selected core does not declare explicit headed receiving")

    sources = dict(_sources(False))
    for package, selection in (("agent_mcp", _MCP), ("zdd", _ZDD)):
        root = files(package)
        for name in selection:
            relative = package + "/" + name
            if relative in sources:
                raise ValueError("MCP receiving source conflicts with the selected core")
            sources[relative] = root.joinpath(name).read_bytes()
    sources["__main__.py"] = (
        b"from agent_mcp.owner_headed import main\nraise SystemExit(main())\n"
    )
    digest = hashlib.sha256()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(sources.items()):
            digest.update(name.encode("utf-8") + b"\0" + content + b"\0")
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(entry, content)
    sha, payload = digest.hexdigest(), stream.getvalue()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / ("mcp-headed-" + sha[:16] + ".pyz")
    if destination.exists():
        if destination.is_symlink() or destination.read_bytes() != payload:
            raise ValueError("Retained MCP receiving executable changed")
        return destination, sha
    with tempfile.TemporaryDirectory(prefix=".mcp-headed-", dir=directory) as temporary:
        staged = Path(temporary) / "receiver.pyz"
        staged.write_bytes(payload)
        os.replace(staged, destination)
    return destination, sha


def prepare_headed_command(
    child_argv: list[str], *, configuration_path: Path, names: list[str],
    session_id: str, attempt_directory: Path, state_file: Path, cwd: str,
) -> list[str]:
    from agent_procutil._interpreter import physical_executable
    from agent_remote_driver.headed.launcher import _native_request

    _native_request(child_argv, session_id)
    if (
        not all(Path(path).is_absolute() for path in (
            configuration_path, attempt_directory, state_file, cwd,
        ))
        or not names or len(set(names)) != len(names)
        or any(not isinstance(name, str) or not name or "\0" in name for name in names)
    ):
        raise ValueError("MCP receiving requires absolute paths and an explicit unique selection")
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    if interpreter is None:
        raise ValueError("MCP receiving requires a physical base interpreter")
    archive, _ = build_headed_bundle(attempt_directory / "runtime")
    return [
        interpreter, "-I", "-S", str(archive), "--mcp-config", str(configuration_path),
        *("--server=" + name for name in names),
        "--", "--mode", "headed", "--session-id=" + session_id,
        "--attempt-directory", str(attempt_directory), "--state-file", str(state_file),
        "--cwd", cwd, "--", *child_argv,
    ]
