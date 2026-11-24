"""Trusted runner admission and retained runtime invocation construction."""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import TYPE_CHECKING, Callable, Mapping, Protocol, Sequence, Union

if TYPE_CHECKING:
    import subprocess

    from agent_procutil.daemon_broker import WindowsDaemonProcess

    from .core import RequestSpec, TrustedRunner
    from .participant import Witness as Witness

    WorkerProcess = Union["subprocess.Popen[bytes]", WindowsDaemonProcess]


class Pollable(Protocol):
    def poll(self) -> int | None: ...


def validate_spec(spec: RequestSpec) -> RequestSpec:
    from . import core

    if (
        not isinstance(spec, core.RequestSpec) or not isinstance(spec.backend, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", spec.backend)
    ):
        raise core.InvalidRequestError("backend must be a bounded adapter identifier")
    if not isinstance(spec.context, (list, tuple)):
        raise core.InvalidRequestError("safe context must contain bounded key/value pairs")
    context = tuple(spec.context)
    if len(context) > 64 or any(
        not isinstance(item, (list, tuple)) or len(item) != 2 for item in context
    ):
        raise core.InvalidRequestError("safe context must contain bounded key/value pairs")
    pairs = tuple(sorted((core._text(k), core._text(v)) for k, v in context))
    if type(spec.repair) is not bool:
        raise core.InvalidRequestError("repair selection must be an explicit boolean")
    if spec.authority not in ("", "standalone", "project") or (
        spec.authority == "project" and not spec.project
    ):
        raise core.InvalidRequestError("update authority must name a selected project or standalone")
    killswitch_path = core._text(spec.killswitch_path)
    if killswitch_path and not Path(killswitch_path).is_absolute():
        raise core.InvalidRequestError("request KillSwitch path must be absolute")
    if len({k for k, _v in pairs}) != len(pairs):
        raise core.InvalidRequestError("safe context keys must be unique")
    cwd = core._text(spec.cwd)
    if cwd:
        if not Path(cwd).is_absolute():
            raise core.InvalidRequestError("request cwd must be absolute")
        cwd = os.path.normcase(os.path.normpath(cwd))
    return core.RequestSpec(
        spec.backend, core._vector(spec.argv), core._text(spec.project), cwd,
        core._text(spec.source), pairs, spec.repair, killswitch_path, spec.authority,
    )


def validate_runner(runner: TrustedRunner) -> TrustedRunner:
    from . import core

    if not isinstance(runner, core.TrustedRunner):
        raise core.InvalidRequestError("trusted adapter runner is required")
    argv = core._vector(runner.argv)
    cwd = core._text(runner.cwd)
    if not argv or not Path(argv[0]).is_absolute() or not Path(cwd).is_absolute():
        raise core.InvalidRequestError("trusted runner executable and cwd must be absolute")
    if (
        isinstance(runner.timeout, bool) or not isinstance(runner.timeout, (int, float))
        or not math.isfinite(runner.timeout) or runner.timeout <= 0
    ):
        raise core.InvalidRequestError("runner timeout must be finite and positive")
    env = runner.env.items() if isinstance(runner.env, Mapping) else runner.env
    try:
        pairs = tuple(sorted((core._text(k), core._text(v)) for k, v in env))
    except (TypeError, ValueError) as error:
        raise core.InvalidRequestError("runner environment must contain key/value pairs") from error
    if any(k not in {"__PYVENV_LAUNCHER__", "PYTHONPATH"} for k, _v in pairs):
        raise core.InvalidRequestError("runner environment accepts only Python import mapping")
    if len({k for k, _v in pairs}) != len(pairs):
        raise core.InvalidRequestError("runner environment keys must be unique")
    context_keys = core._vector(runner.context_keys)
    if len(context_keys) > 128 or len(set(context_keys)) != len(context_keys) or any(
        not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,127}", key)
        or key.startswith("UPDATE_DRIVER_")
        or key in {"PYTHONPATH", "__PYVENV_LAUNCHER__"}
        for key in context_keys
    ):
        raise core.InvalidRequestError("runner context keys must be unique environment identifiers")
    return core.TrustedRunner(argv, cwd, runner.timeout, pairs, tuple(sorted(context_keys)))


def runtime_command(
    module: str, args: Sequence[str] = (),
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Construct this runtime's retained module invocation, not a latest-slot lookup."""
    from . import core

    if not isinstance(module, str) or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", module):
        raise core.InvalidRequestError("runtime module must be an import identifier")
    return core._runtime_command(("-m", module), args)


def runtime_script_command(
    path: str | os.PathLike[str], args: Sequence[str] = (),
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Construct this runtime's retained absolute-script invocation."""
    from . import core

    try:
        script = os.fspath(path)
    except TypeError as error:
        raise core.InvalidRequestError("runtime script must be an absolute path") from error
    if (
        not isinstance(script, str) or not script
        or any(character in script for character in "\0\r\n")
        or not Path(script).is_absolute()
    ):
        raise core.InvalidRequestError("runtime script must be an absolute path")
    return core._runtime_command((script,), args)


def retained_runtime_command(
    entry: tuple[str, ...], args: Sequence[str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    from . import core
    from agent_procutil import direct_python

    executable, env = direct_python(sys.executable)
    prefix = Path(sys.prefix)
    application = prefix.name == ".venv" and prefix.parent.parent.name == "versions"
    slot = prefix.parent if application else prefix
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    installed = (
        (slot / ".install-complete.json").exists()
        or (slot / ".install-complete.json").is_symlink()
        or bootstrap.parent.exists() or bootstrap.parent.is_symlink()
        or not application and slot.parent.name == "versions"
    )
    if bootstrap.exists() or bootstrap.is_symlink():
        info = bootstrap.lstat()
        if (
            not stat.S_ISREG(info.st_mode) or bootstrap.is_symlink()
            or getattr(info, "st_file_attributes", 0) & 0x400
            or bootstrap.parent.resolve() != bootstrap.parent
        ):
            raise core.StateError("retained runtime bootstrap is linked or unsafe")
    if bootstrap.is_file():
        return (
            executable, "-I", "-S", "-B", "-X", "utf8", str(bootstrap),
            *entry, *core._vector(args),
        ), env
    if installed:
        raise core.StateError("installed runtime is missing its retention bootstrap")
    if entry[0] != "-m" and not Path(entry[0]).is_file():
        raise core.InvalidRequestError("runtime script must be an absolute script file")
    if entry[0] == "-m":
        env = {**env, **source_import_mapping(entry[1], env.get("PYTHONPATH", ""))}
    return (executable, *entry, *core._vector(args)), env


def source_import_mapping(module: str, inherited: str = "") -> dict[str, str]:
    """Carry this process's source import roots to a fresh ``-m`` child.

    A source-mode caller (for example a recovery toolkit checkout) can import
    the module only through runtime ``sys.path`` entries the base interpreter
    does not know, so a bare ``python -m module`` child fails to import it.
    """
    import importlib.util

    try:
        spec = importlib.util.find_spec(module.partition(".")[0])
    except (ImportError, ValueError):
        return {}
    if spec is None:
        return {}
    origin = spec.origin
    found = [origin] if origin and Path(origin).is_absolute() else []
    found += [p for p in spec.submodule_search_locations or () if Path(p).is_absolute()]
    owned = _interpreter_owned()
    if not found or all(owned(Path(p).resolve()) for p in found):
        return {}
    roots: list[str] = []
    for item in sys.path:
        if not item or not Path(item).is_absolute() or not Path(item).exists():
            continue
        resolved = Path(item).resolve()
        if not owned(resolved) and str(resolved) not in roots:
            roots.append(str(resolved))
    roots += [p for p in inherited.split(os.pathsep) if p and p not in roots]
    return {"PYTHONPATH": os.pathsep.join(roots)} if roots else {}


def _interpreter_owned() -> Callable[[Path], bool]:
    """Match interpreter library directories, not everything under a prefix."""
    import site
    import sysconfig

    prefixes = {Path(p).resolve() for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}
    libraries = {
        Path(path).resolve()
        for name in ("stdlib", "platstdlib", "purelib", "platlib")
        if (path := sysconfig.get_paths().get(name))
    }
    try:
        libraries.update(Path(p).resolve() for p in site.getsitepackages([str(p) for p in prefixes]))
    except AttributeError:
        pass
    if user_site := getattr(site, "getusersitepackages", lambda: "")():
        libraries.add(Path(user_site).resolve())
    # Windows lists a bare prefix as a site directory; only its direct
    # entries are interpreter-owned, handled below.
    libraries -= prefixes
    archive = f"python{sys.version_info[0]}{sys.version_info[1]}.zip"
    libdirs = {"lib", getattr(sys, "platlibdir", "lib")}
    libraries.update(root / libdir / archive for root in prefixes for libdir in libdirs)

    def owned(path: Path) -> bool:
        # Entries placed directly in a prefix (DLLs, the stdlib zip, the
        # prefix itself) belong to the interpreter; deeper checkouts do not.
        if path in prefixes or path.parent in prefixes:
            return True
        return any(path == root or root in path.parents for root in libraries)

    return owned


def log_size(log_path: Path) -> int:
    try:
        return log_path.stat().st_size
    except OSError:
        return 0


_MISSING_MODULE = re.compile(r"No module named '?([A-Za-z_][\w.]{0,63})'?\s*$")
_RAISED = re.compile(r"^([A-Za-z_][\w.]{0,63}(?:Error|Exception|Exit|Interrupt))(?::|$)")


def worker_exit_diagnosis(log_path: Path, offset: int, limit: int = 8192) -> str:
    """Describe an early worker exit without copying its output.

    Worker output can carry credentials, so only an identifier-shaped exception
    type (and missing module name) is surfaced, plus a pointer to the private log.
    The log is shared by overlapping launches, so the evidence is not attributed.
    """
    try:
        with log_path.open("rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            if end <= offset:
                return ""
            handle.seek(max(offset, end - limit))
            lines = handle.read(limit).decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    pointer = f"see {log_path} from byte {offset}"
    for line in reversed([item.strip() for item in lines if " spawn_daemon" not in item]):
        if missing := _MISSING_MODULE.search(line):
            return f"; shared worker log since launch records ModuleNotFoundError ({missing[1]}); {pointer}"
        if raised := _RAISED.match(line):
            return f"; shared worker log since launch records {raised[1]}; {pointer}"
    return f"; {pointer}"
