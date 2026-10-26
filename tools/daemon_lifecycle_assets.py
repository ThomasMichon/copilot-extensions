"""Deterministic source-only lifecycle archive for pre-install shell callers."""

from __future__ import annotations

import io
from pathlib import Path
import zipfile

import uv_editable_ref as uer

ARCHIVE_NAME = "daemon_instance_runtime.pyz"
_ENTRYPOINT = (
    b"from agent_procutil._daemon_instance_cli import main\n"
    b"raise SystemExit(main())\n"
)


def lifecycle_archive(repo: Path) -> bytes:
    package = repo / "libs" / "agent-procutil" / "src" / "agent_procutil"
    if uer._find_symlinked_ancestor(package, repo.resolve()) is not None or not package.is_dir():
        raise ValueError("lifecycle source package missing or redirected")
    members = {"__main__.py": _ENTRYPOINT}
    for source in sorted(package.glob("*.py")):
        if source.name == "_build_native.py":
            continue
        if source.is_symlink() or not source.is_file():
            raise ValueError("lifecycle source is not an ordinary Python file")
        members["agent_procutil/" + source.name] = source.read_bytes()
    if not all("agent_procutil/" + name in members for name in (
        "__init__.py", "daemon_instances.py", "_daemon_instance_cli.py",
        "_daemon_instance_linux.py", "_daemon_instance_clock.py",
        "_daemon_instance_store.py", "_linux_pidfd.py",
    )):
        raise ValueError("lifecycle source package is incomplete")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, data)
    return output.getvalue()
