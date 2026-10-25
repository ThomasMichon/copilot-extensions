"""Read the installed Manager's known versioned selection protocol, never import it."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .automatic_update_ownership import _unique_object, ownership_policy_path

VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.+_-]*)")
# Frozen recognition of the installer-owned protocol, not another stub writer.
POSIX_SELECTION_STUB = (
    "#!/usr/bin/env bash\n"
    "# worktree-manager binstub (versioned) \u2014 resolves the current-version marker.\n"
    "set -euo pipefail\n"
    'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
    'ver="$(cat "$root/current-version")"\n'
    'exec uv run --quiet --project "$root/versions/$ver" python -m worktree_manager "$@"\n'
)
WINDOWS_SELECTION_STUB = (
    "@echo off\r\nsetlocal\r\n"
    'set "PYTHONUTF8=1"\r\n'
    'if "%WORKTREE_MANAGER_ROOT%"=="" '
    'set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
    'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
    'uv run --quiet --project "%WORKTREE_MANAGER_ROOT%\\versions\\%VER%" '
    "python -m worktree_manager %*\r\n"
)
# Frozen native launcher protocol. Recognition does not import another runtime.
_LEGACY_RUNTIME_PROBE = (
    "import ast, importlib.metadata as m, pathlib as p, sys; "
    "import worktree_manager as package; "
    "slot=p.Path(sys.argv[1]).resolve(); runtime=slot/'.venv'; "
    "assert p.Path(sys.prefix).resolve()==runtime; "
    "assert p.Path(m.distribution('copilot-extensions-worktree-manager').locate_file(''))"
    ".resolve().is_relative_to(runtime); "
    "assert p.Path(package.__file__).resolve().is_relative_to(slot); "
    "source=ast.parse((slot/'src/worktree_manager/__init__.py').read_text(encoding='utf-8')); "
    "expected=next(node.value.value for node in source.body if isinstance(node, ast.Assign) "
    "and any(isinstance(target, ast.Name) and target.id=='__version__' for target in node.targets)); "
    "assert package.__version__==expected; "
    "assert all((slot/name).is_file() for name in "
    "('.install-complete', 'pyproject.toml', 'src/worktree_manager/__init__.py', "
    "'src/worktree_manager/__main__.py'))"
)
RETAINED_POSIX_SELECTION_STUB = (
    "#!/usr/bin/env bash\n"
    "# worktree-manager binstub (versioned) \u2014 resolves the current-version marker.\n"
    "set -euo pipefail\n"
    "unset __PYVENV_LAUNCHER__\n"
    'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
    'export WORKTREE_MANAGER_ROOT="$root"\n'
    'ver="$(cat "$root/current-version")"\n'
    'slot="$root/versions/$ver"\n'
    'python="$slot/bin/python"\n'
    '[[ ! -x "$slot/Scripts/python.exe" ]] || python="$slot/Scripts/python.exe"\n'
    'if [[ -f "$slot/.runtime-bootstrap/runtime_launcher.py" && -x "$python" ]]; then\n'
    '    exec "$python" -I -S -B -X utf8 "$slot/.runtime-bootstrap/runtime_launcher.py" '
    '-m worktree_manager "$@"\n'
    "fi\n"
    'python="$slot/.venv/bin/python"\n'
    'if [[ -x "$slot/.venv/Scripts/python.exe" ]]; then\n'
    '    python="$slot/.venv/Scripts/python.exe"\n'
    "fi\n"
    'if [[ -x "$python" ]]; then\n'
    f'    if ! "$python" -I -c "{_LEGACY_RUNTIME_PROBE}" "$slot" >/dev/null 2>&1; then\n'
    '        echo "invalid constructed worktree-manager runtime: $slot" >&2\n'
    "        exit 1\n"
    "    fi\n"
    '    exec "$python" -I -X utf8 -m worktree_manager "$@"\n'
    "fi\n"
    'echo "invalid constructed worktree-manager runtime: $slot; run bootstrap to repair" >&2\n'
    "exit 1\n"
)
RETAINED_WINDOWS_SELECTION_STUB = (
    "@echo off\r\n"
    "setlocal\r\n"
    'set "ERRORLEVEL="\r\n'
    'set "PYTHONUTF8=1"\r\n'
    'set "__PYVENV_LAUNCHER__="\r\n'
    'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
    'set "VER="\r\n'
    'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
    'if "%VER%"=="" exit /b 1\r\n'
    'set "SLOT=%WORKTREE_MANAGER_ROOT%\\versions\\%VER%"\r\n'
    'if exist "%SLOT%\\.runtime-bootstrap\\runtime_launcher.py" goto retained\r\n'
    'if not exist "%SLOT%\\.venv\\Scripts\\python.exe" goto invalid\r\n'
    f'"%SLOT%\\.venv\\Scripts\\python.exe" -I -c "{_LEGACY_RUNTIME_PROBE}" "%SLOT%" >nul 2>&1\r\n'
    'if errorlevel 1 goto invalid\r\n'
    '"%SLOT%\\.venv\\Scripts\\python.exe" -I -X utf8 -m worktree_manager %* '
    '& call exit /b %%errorlevel%%\r\n'
    ':invalid\r\n'
    '>&2 echo invalid constructed worktree-manager runtime: "%SLOT%"\r\n'
    'exit /b 1\r\n'
    ':retained\r\n'
    '"%SLOT%\\Scripts\\python.exe" -I -S -B -X utf8 "%SLOT%\\.runtime-bootstrap\\runtime_launcher.py" '
    '-m worktree_manager %* & call exit /b %%errorlevel%%\r\n'
)
BOOTSTRAP_FILES = (
    "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
    "versioned_runtime_source.py", "versioned_runtime_construction.py",
    "versioned_runtime_publication.py",
)


class ManagerSelectionRefused(RuntimeError):
    """An installed selection is incomplete, ambiguous or outside the supported root."""


class ManagerGenerationChanged(ManagerSelectionRefused):
    """Read-only negotiation no longer describes the selected generation."""


@dataclass(frozen=True)
class ManagerSelection:
    root: Path
    command: str
    version: str
    fingerprint: str
    retained_command: tuple[str, ...] = ()

    @property
    def invocation(self) -> tuple[str, ...]:
        """Use the fixed retained generation, never redispatch through its selector."""
        return self.retained_command or (self.command,)


def _stat_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _regular_bytes(path: Path, *, directory: bool = False) -> tuple[bytes, os.stat_result]:
    absolute = path.absolute()
    for part in reversed((absolute, *absolute.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ManagerSelectionRefused(f"linked or reparsed Manager selection: {part}")
    info = absolute.stat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ManagerSelectionRefused(f"Manager selection is not a directory: {absolute}")
        return b"", info
    if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
        raise ManagerSelectionRefused(f"invalid Manager selection file: {absolute}")
    with absolute.open("rb") as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    after = absolute.stat()
    if len(data) > 2 * 1024 * 1024 or _stat_identity(info) != _stat_identity(after):
        raise ManagerGenerationChanged(f"Manager selection changed while reading {absolute}")
    return data, info


def read_manager_selection(home: Path | None = None) -> ManagerSelection:
    """Require the standard root, completion evidence, version agreement and owned stub."""
    base = (home or Path(os.environ.get("USERPROFILE") or Path.home())).absolute()
    root = ownership_policy_path(home).parent.absolute()
    if root != base / ".worktree-manager":
        raise ManagerSelectionRefused(
            "legacy compatibility requires the standard user-local Manager root"
        )
    try:
        _regular_bytes(root, directory=True)
        marker = root / "current-version"
        first, marker_info = _regular_bytes(marker)
        generation = first.decode("utf-8").strip()
        if VERSION.fullmatch(generation) is None or generation.endswith("."):
            raise ManagerSelectionRefused("invalid Manager current-version marker")
        slot = root / "versions" / generation
        _regular_bytes(slot, directory=True)
        command = (
            base
            / ".local"
            / "bin"
            / ("worktree-manager.cmd" if os.name == "nt" else "worktree-manager")
        )
        digest = hashlib.sha256()
        retained = (slot / ".runtime-bootstrap").exists() or (slot / "payload").exists()
        payload = slot / "payload" if retained else slot
        files = [
            marker,
            slot / (".install-complete.json" if retained else ".install-complete"),
            payload / "pyproject.toml",
            payload / "src" / "worktree_manager" / "__init__.py",
            payload / "src" / "worktree_manager" / "__main__.py",
            command,
        ]
        retained_command: tuple[str, ...] = ()
        if retained:
            files.extend(slot / ".runtime-bootstrap" / name for name in BOOTSTRAP_FILES)
            python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            _regular_bytes(python.parent, directory=True)
            # POSIX venv interpreters may be links to the base interpreter. The
            # executable's slot-local parent, bootstrap and payload may not be links.
            named, target = python.lstat(), python.stat()
            if (
                not stat.S_ISREG(target.st_mode)
                or getattr(named, "st_file_attributes", 0) & 0x400
                or not os.access(python, os.X_OK)
            ):
                raise ManagerSelectionRefused("invalid retained Manager interpreter")
            digest.update(repr((_stat_identity(named), _stat_identity(target))).encode())
            if python.is_symlink():
                digest.update(os.readlink(python).encode("utf-8"))
            retained_command = (
                str(python), "-I", "-S", "-B", "-X", "utf8",
                str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
                "-m", "worktree_manager",
            )
        contents: dict[Path, bytes] = {}
        for path in files:
            data, info = _regular_bytes(path)
            contents[path] = data
            digest.update(str(path).encode("utf-8"))
            digest.update(repr(_stat_identity(info)).encode())
            digest.update(data)
        expected = (
            RETAINED_WINDOWS_SELECTION_STUB if os.name == "nt" else RETAINED_POSIX_SELECTION_STUB
        )
        supported = {expected.encode("utf-8")}
        if not retained:
            supported.add(
                (WINDOWS_SELECTION_STUB if os.name == "nt" else POSIX_SELECTION_STUB).encode("utf-8")
            )
        if contents[command] not in supported:
            raise ManagerSelectionRefused(
                "Manager binstub does not match the known selection protocol"
            )
        if os.name != "nt" and not os.access(command, os.X_OK):
            raise ManagerSelectionRefused("Manager binstub is not executable")
        init = contents[payload / "src" / "worktree_manager" / "__init__.py"].decode("utf-8")
        declared = re.findall(r"""(?m)^__version__\s*=\s*["']([^"']+)["']\s*$""", init)
        version = re.sub(r"[+.]repair\.[0-9a-f]{32}$", "", generation)
        version = re.sub(r"[+.]source\.(?:[0-9a-f]{40}|[0-9a-f]{64})\.[0-9a-f]{16}$", "", version)
        if declared != [version] or (not retained and version != generation):
            raise ManagerSelectionRefused("Manager payload version disagrees with its selector")
        project = contents[payload / "pyproject.toml"].decode("utf-8")
        if re.findall(r"""(?m)^version\s*=\s*["']([^"']+)["']\s*$""", project) != [version]:
            raise ManagerSelectionRefused("Manager project version disagrees with its payload")
        if retained:
            try:
                complete = json.loads(
                    contents[slot / ".install-complete.json"], object_pairs_hook=_unique_object,
                )
                if (
                    not isinstance(complete, dict)
                    or not {"version", "completed_at", "pid", "payload_hash"} <= set(complete)
                    or set(complete) - {"version", "completed_at", "pid", "payload_hash"}
                    or complete["version"] != generation
                    or not isinstance(complete["completed_at"], str)
                    or type(complete["pid"]) is not int or complete["pid"] < 0
                    or not isinstance(complete["payload_hash"], str) or not complete["payload_hash"]
                ):
                    raise ValueError("invalid attributable completion marker")
            except ValueError as exc:
                raise ManagerSelectionRefused(f"retained Manager completion refused: {exc}") from exc
        again, after = _regular_bytes(marker)
        if again != first or _stat_identity(after) != _stat_identity(marker_info):
            raise ManagerGenerationChanged("Manager selector changed during inspection")
        return ManagerSelection(root, str(command), version, digest.hexdigest(), retained_command)
    except (OSError, UnicodeError) as exc:
        raise ManagerSelectionRefused(f"cannot verify installed Manager selection: {exc}") from exc


def require_same_selection(selection: ManagerSelection, home: Path | None = None) -> None:
    try:
        current = read_manager_selection(home)
    except ManagerSelectionRefused as exc:
        raise ManagerGenerationChanged(f"Manager generation became unavailable: {exc}") from exc
    if current != selection:
        raise ManagerGenerationChanged("Manager generation changed after capability inspection")
