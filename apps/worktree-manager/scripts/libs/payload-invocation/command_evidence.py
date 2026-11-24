"""Package canonical stdlib-only diagnostic owners, never installed libraries."""
from __future__ import annotations

import io
from pathlib import Path
import zipfile

INPUTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("process_telemetry", "process-telemetry", (
        "command_errors.py", "command_entry.py", "diagnostic_logs.py", "append.py",
    )),
    ("agent_procutil", "agent-procutil", (
        "diagnostics.py", "python_cache_permissions.py", "_daemon_instance_owner.py",
    )),
    ("single_instance_lease", "single-instance-lease", ("operation.py", "lease.py")),
)
NATIVE_INPUTS = {
    **{name: f"libs/process-telemetry/src/process_telemetry/native/{name}" for name in (
        "command-errors.sh", "command-errors.ps1", "command-causes.tsv",
    )},
    **{name.replace("_", "-"): f"libs/agent-procutil/src/agent_procutil/{name}" for name in (
        "private_paths.sh", "private_paths.ps1",
    )},
}


def native_files(root: Path) -> dict[str, bytes]:
    """Read the finite native resource closure without executing source code."""
    outputs = {}
    for name, relative in NATIVE_INPUTS.items():
        source = root / relative
        for path in (source, *source.parents):
            if path.is_symlink() or (
                path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
            ):
                raise ValueError("native command evidence source is redirected")
            if path == root:
                break
        outputs[name] = source.read_bytes()
    return outputs


def archive(root: Path) -> bytes:
    """Build deterministic same-interpreter command support for payloads."""
    files = {
        "__main__.py": b"from _command_evidence.process_telemetry.command_entry import main\nmain()\n",
        "_command_evidence/__init__.py": b"",
        "_command_evidence/process_telemetry/__init__.py": b"",
        "_command_evidence/agent_procutil/__init__.py": b"",
        "_command_evidence/single_instance_lease/__init__.py": b"",
    }
    for package, library, names in INPUTS:
        for name in names:
            source = (
                root / "libs" / library / "src" / package / name
            )
            for path in (source, *source.parents):
                if path.is_symlink() or (
                    path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400
                ):
                    raise ValueError("command evidence source is redirected")
                if path == root:
                    break
            content = source.read_bytes()
            if name == "command_errors.py":
                content = content.replace(
                    b"from agent_procutil", b"from _command_evidence.agent_procutil",
                ).replace(
                    b"from single_instance_lease", b"from _command_evidence.single_instance_lease",
                )
            files[f"_command_evidence/{package}/{name}"] = content
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o600 << 16
            target.writestr(info, content)
    return output.getvalue()
