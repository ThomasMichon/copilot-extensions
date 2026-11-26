"""Isolated process boundaries for cell dependency installation and package probes."""
from __future__ import annotations

import os
import importlib.util
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable, cast

_HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _prepared_process_helpers() -> tuple[ModuleType, ModuleType]:
    capsule = Path(__file__).with_name("daemon_instance_runtime.pyz")
    if not capsule.is_file():
        raise FileNotFoundError("prepared process-helper capsule is unavailable")
    sys.path.insert(0, str(capsule))
    try:
        from agent_procutil import managed_python, owned_process
        return managed_python, owned_process
    finally:
        sys.path.pop(0)


def cleanup_pending(error: BaseException) -> bool:
    """Keep native cleanup custody distinct from an ordinary failed probe."""
    if not isinstance(error, subprocess.SubprocessError):
        return False
    owner = sys.modules.get("agent_procutil.owned_process")
    return owner is not None and isinstance(error, owner.OwnedCleanupError)


def runtime_probe(
    interpreter: Path, slot: Path, code: str, *, environment: dict[str, str] | None,
    error_type: type[Exception],
    isolated_environment: Callable[..., dict[str, str]],
) -> subprocess.CompletedProcess[str]:
    """Admit the selected interpreter through prepared UV, then own its probe."""
    try:
        managed, owner = _prepared_process_helpers()
        selected = managed.resolve_runtime_python(interpreter)
        return cast(subprocess.CompletedProcess[str], owner.run_caller_owned(
            [selected, "-I", "-B", "-X", "utf8", "-c", code],
            cwd=slot, env=isolated_environment(environment),
            input_text="", timeout=10.0, max_stdout_bytes=65536, max_stderr_bytes=65536,
        ))
    except (ImportError, OSError, RuntimeError, subprocess.SubprocessError, UnicodeError) as exc:
        if cleanup_pending(exc):
            raise
        raise error_type(
            "runtime probe requires prepared UV and process support; "
            "use explicit installation management"
        ) from exc


def runtime_site_packages(
    interpreter: Path, slot: Path, *, error_type: type[Exception],
    isolated_environment: Callable[..., dict[str, str]],
) -> Path:
    """Resolve the explicit smoke build's site directory through shared admission."""
    result = runtime_probe(
        interpreter, slot,
        "import site; print(next(p for p in site.getsitepackages() "
        "if p.endswith(('site-packages','dist-packages'))))",
        environment=None, error_type=error_type, isolated_environment=isolated_environment,
    )
    if result.returncode != 0:
        raise error_type("smoke runtime could not resolve site-packages")
    return Path(result.stdout.strip())


def isolated_environment(environment: dict[str, str] | None = None) -> dict[str, str]:
    value = dict(os.environ if environment is None else environment)
    value.pop("PYTHONPATH", None)
    value.pop("PYTHONHOME", None)
    value["PYTHONUTF8"] = "1"
    value["PYTHONDONTWRITEBYTECODE"] = "1"
    return value


def load_versioned_runtime(
    payload_root: Path, *, error_type: type[Exception],
    validate_chain: Callable[[Path, Path, str], None], validate_file: Callable[[Path, str], None],
) -> ModuleType:
    path = payload_root / "scripts/versioned_runtime.py"
    validate_chain(path, payload_root, "versioned runtime")
    validate_file(path, "versioned runtime")
    spec = importlib.util.spec_from_file_location("cell_versioned_runtime", path)
    if spec is None or spec.loader is None:
        raise error_type("canonical versioned runtime cannot be loaded")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    return runtime


def run_checked(command: list[str], *, error_type: type[Exception],
                environment: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    result = subprocess.run(
        command, env=environment, cwd=cwd, text=True, encoding="utf-8",
        errors="replace", check=False, creationflags=_HEADLESS,
    )
    if result.returncode != 0:
        raise error_type(
            f"command failed with exit {result.returncode}: "
            + " ".join(shlex.quote(part) for part in command)
        )


def install_dependency(
    payload_root: Path, uv: str, interpreter: Path, source: Path | str, cwd: Path, *,
    error_type: type[Exception], validate_file: Callable[[Path, str], None],
    quote: Callable[[str], str], isolated_environment: Callable[..., dict[str, str]],
    execute: Callable[..., None],
) -> None:
    suffix = "ps1" if os.name == "nt" else "sh"
    engine = payload_root / "scripts" / f"installer-engine.{suffix}"
    if not engine.is_file():
        engine = payload_root.parent.parent / "libs" / "installer-engine" / engine.name
    validate_file(engine, "canonical installer engine")
    arguments = ["--python", str(interpreter), str(source), "--quiet"]
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise error_type("PowerShell is required for the canonical dependency wrapper")
        quoted = ",".join(quote(argument) for argument in arguments)
        body = (
            "$ErrorActionPreference='Stop'; "
            f". {quote(str(engine))}; "
            "function Write-Warn($message) { [Console]::Error.WriteLine($message) }; "
            f"$result=Invoke-UvPipInstallResilient -UvCommand {quote(uv)} "
            f"-Arguments @({quoted}); "
            "[Console]::WriteLine($result.Output); exit $result.ExitCode"
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-Command", body]
    else:
        command = [
            "bash", "-c",
            'source "$1"; shift; _warn() { printf "%s\\n" "$*" >&2; }; '
            'invoke_uv_pip_install_resilient "$@"',
            "cell-dependency", str(engine), uv, *arguments,
        ]
    execute(command, environment=isolated_environment(), cwd=cwd)


def runtime_module_path(
    interpreter: Path, slot: Path, *, environment: dict[str, str] | None,
    label: str, error_type: type[Exception],
    isolated_environment: Callable[..., dict[str, str]], absolute_path: Callable[..., Path],
    validate_chain: Callable[[Path, Path, str], None], validate_file: Callable[[Path, str], None],
) -> Path:
    try:
        result = runtime_probe(
            interpreter, slot,
            "from pathlib import Path; import agent_index; "
            "print(Path(agent_index.__file__).resolve())",
            environment=environment, error_type=error_type,
            isolated_environment=isolated_environment,
        )
    except OSError as exc:
        raise error_type(f"{label} is unusable; ownership-checked repair is required") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise error_type(
            f"{label} is unusable; ownership-checked repair is required"
            + (f": {detail}" if detail else "")
        )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise error_type(
            f"{label} did not report its package origin; ownership-checked repair is required"
        )
    module_path = absolute_path(lines[-1])
    try:
        module_path.relative_to(absolute_path(slot))
    except ValueError as exc:
        raise error_type(
            f"{label} imported agent_index outside the selected runtime slot"
        ) from exc
    validate_chain(module_path, slot, f"{label} package origin")
    validate_file(module_path, f"{label} package origin")
    return module_path
