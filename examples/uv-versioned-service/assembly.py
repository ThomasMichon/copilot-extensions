"""Opt-in example composition, called by the real main materializer."""

import os
import shutil
import subprocess
from pathlib import Path


def slot_wrappers(project: Path, kind: str) -> None:
    """Keep the uv invocation inside the complete, relocatable project."""
    binary = project / "bin"
    binary.mkdir()
    clear = (
        "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC", "UV_NO_PROJECT", "UV_WORKING_DIR",
        "UV_FROZEN", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    )
    flags = "run --locked --no-dev --no-editable --link-mode hardlink"
    ps1 = (
        "$slot = Split-Path -Parent $PSScriptRoot\n"
        "$appHome = $slot\n"
        "foreach ($index in 1..4) { $appHome = Split-Path -Parent $appHome }\n"
        "foreach ($key in @(" + ",".join("'" + key + "'" for key in clear) + ")) {\n"
        '    Remove-Item "Env:$key" -ErrorAction SilentlyContinue\n}\n'
        "$env:UV_CACHE_DIR = Join-Path $appHome 'cache\\uv'\n"
        f"& uv {flags} --project $slot python -m example_{kind} @args\n"
        "exit $LASTEXITCODE\n"
    )
    cmd = (
        "@echo off\nsetlocal EnableExtensions DisableDelayedExpansion\n"
        'for %%I in ("%~dp0..") do set "SLOT=%%~fI"\n'
        'for %%I in ("%SLOT%\\..\\..\\..\\..") do set "APP_HOME=%%~fI"\n'
        + "".join(f'set "{key}="\n' for key in clear)
        + 'set "UV_CACHE_DIR=%APP_HOME%\\cache\\uv"\n'
        + f'uv {flags} --project "%SLOT%" python -m example_{kind} %*\n'
        + "exit /b %errorlevel%\n"
    )
    sh = (
        "#!/bin/sh\n"
        'slot=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit\n'
        'app_home=$(CDPATH= cd -- "$slot/../../../.." && pwd) || exit\n'
        "unset " + " ".join(clear) + "\n"
        'UV_CACHE_DIR="$app_home/cache/uv"\n'
        'if command -v cygpath >/dev/null 2>&1; then\n'
        '    UV_CACHE_DIR=$(cygpath -w "$UV_CACHE_DIR") || exit\nfi\n'
        "export UV_CACHE_DIR\n"
        f'exec uv {flags} --project "$slot" python -m example_{kind} "$@"\n'
    )
    for extension, text in (("ps1", ps1), ("cmd", cmd), ("sh", sh)):
        path = binary / ("invoke." + extension)
        path.write_text(text, encoding="utf-8", newline="\n")
        if extension == "sh":
            path.chmod(0o700)


def assemble(destination: Path, source: Path, materialize_lib) -> list[str]:
    """Produce complete relocatable projects; never construct an environment."""
    output = destination / "examples" / "uv-versioned-service" / "install-source"
    if output.exists():
        raise ValueError(f"example output already exists: {output}")
    canonical = source / "examples" / "uv-versioned-service" / "dev"
    log = []
    for kind, name in (("services", "example-service"), ("tools", "example-tool")):
        original = canonical / kind / name
        project = output / kind / name
        shutil.copytree(
            original, project,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.egg-info", "uv.lock"),
        )
        entries = materialize_lib(
            source_consumer_dir=original, dest_consumer_dir=project, canonical_root=canonical,
        )
        log.extend(entries)
        if any(line.startswith("SKIP") for line in entries):
            raise ValueError("example library materialization failed")
        slot_wrappers(project, "service" if kind == "services" else "tool")
        uv = shutil.which("uv")
        if uv is None:
            raise ValueError("the opt-in example requires an already available uv")
        subprocess.run(
            [uv, "lock", "--project", str(project)],
            check=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if (project / ".venv").exists():
            raise ValueError("example composition must not construct an environment")
        log.append(f"OK   {project}: vendored and locked, no environment")
    return log
