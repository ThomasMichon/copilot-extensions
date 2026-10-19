"""Isolated process boundaries for cell dependency installation and package probes."""
from __future__ import annotations

import os
import importlib.util
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Callable

_HEADLESS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


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
):
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
    payload_root: Path, uv: str, interpreter: Path, source: Path, cwd: Path, *,
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
        result = subprocess.run(
            [
                str(interpreter), "-I", "-X", "utf8", "-c",
                "from pathlib import Path; import agent_index; "
                "print(Path(agent_index.__file__).resolve())",
            ],
            cwd=slot, env=isolated_environment(environment), capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False, creationflags=_HEADLESS,
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
