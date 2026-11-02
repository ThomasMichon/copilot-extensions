"""Two-layer application launchers; installation and service policy stay app-owned."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_ENVIRONMENT = re.compile(r"[A-Z][A-Z0-9_]*")
_CLEAR = (
    "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC", "UV_NO_PROJECT",
    "UV_WORKING_DIR", "UV_FROZEN", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
)
_FLAGS = "run --locked --no-dev --no-editable --link-mode hardlink"


def slot_binstubs(name: str, module: str) -> dict[str, str]:
    """Return relocatable app/install stubs without publishing any of them."""
    package, separator, attribute = module.partition(":")
    if (
        name == "install" or not _NAME.fullmatch(name) or not _MODULE.fullmatch(package)
        or (separator and not _MODULE.fullmatch(attribute))
    ):
        raise ValueError("application launchers require an ordinary name and Python module")
    result = {}
    for command, target in (
        (name, f"-I -X utf8 -B -m {module}" if not separator else None),
        ("install", None),
    ):
        script = "install.py" if command == "install" else name + ".py"
        ps_target = target or f"-I -X utf8 -B (Join-Path $slot '{script}')"
        cmd_target = target or f'-I -X utf8 -B "%SLOT%\\{script}"'
        sh_target = target or f'-I -X utf8 -B "$slot/{script}"'
        result[command + ".ps1"] = (
            "$ErrorActionPreference = 'Stop'\n"
            "$slot = $PSScriptRoot\n"
            "foreach ($key in @(" + ",".join("'" + key + "'" for key in _CLEAR) + ")) {\n"
            '    Remove-Item "Env:$key" -ErrorAction SilentlyContinue\n'
            "}\n"
            "if (-not $env:UV_CACHE_DIR) {\n"
            "    $root = Split-Path -Parent (Split-Path -Parent $slot)\n"
            "    $env:UV_CACHE_DIR = Join-Path $root 'cache\\uv'\n"
            "}\n"
            "$forwarded = @($args)\n"
            "if (Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue) {\n"
            "    $PSNativeCommandArgumentPassing = 'Standard'\n"
            "} else {\n"
            "    $forwarded = @($args | ForEach-Object { if ($_ -eq '') { '\"\"' } else { $_ } })\n"
            "}\n"
            f"& uv {_FLAGS} --project $slot python {ps_target} @forwarded\n"
            "exit $LASTEXITCODE\n"
        )
        result[command + ".cmd"] = (
            "@echo off\r\nsetlocal EnableExtensions DisableDelayedExpansion\r\n"
            'for %%I in ("%~dp0.") do set "SLOT=%%~fI"\r\n'
            + "".join(f'set "{key}="\r\n' for key in _CLEAR)
            + 'if not defined UV_CACHE_DIR for %%I in ("%SLOT%\\..\\..") do '
            'set "UV_CACHE_DIR=%%~fI\\cache\\uv"\r\n'
            + f'uv {_FLAGS} --project "%SLOT%" python {cmd_target} %*\r\n'
            + "exit /b %errorlevel%\r\n"
        )
        shell = (
            "#!/bin/sh\n"
            'slot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit\n'
            "unset " + " ".join(_CLEAR) + "\n"
            'if [ -z "${UV_CACHE_DIR:-}" ]; then\n'
            '    root=$(CDPATH= cd -- "$slot/../.." && pwd) || exit\n'
            '    UV_CACHE_DIR="$root/cache/uv"\n'
            "fi\n"
            "if command -v cygpath >/dev/null 2>&1; then\n"
            '    slot=$(cygpath -w "$slot") || exit\n'
            '    UV_CACHE_DIR=$(cygpath -w "$UV_CACHE_DIR") || exit\n'
            "fi\n"
            "export UV_CACHE_DIR\n"
            f'exec uv {_FLAGS} --project "$slot" python {sh_target} "$@"\n'
        )
        result[command + ".sh"] = shell
        result[command] = shell
    if separator:
        result[name + ".py"] = (
            "import importlib, sys\n"
            f"sys.argv[0] = {name!r}\n"
            f"command = importlib.import_module({package!r})\n"
            f"for part in {attribute!r}.split('.'):\n"
            "    command = getattr(command, part)\n"
            "raise SystemExit(command())\n"
        )
    return result


def global_binstubs(
    name: str, root_environment: str, default_directory: str, *, root: Path | None = None,
) -> dict[str, str]:
    """Globals only resolve the current marker and invoke the slot-local app."""
    if (
        not _NAME.fullmatch(name) or not _ENVIRONMENT.fullmatch(root_environment)
        or not re.fullmatch(r"\.[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*", default_directory)
    ):
        raise ValueError("global launchers require an ordinary app and root binding")
    shell_root = (
        f'root="${{{root_environment}:-$HOME/{default_directory}}}"\n' if root is None else
        f'root="${{{root_environment}:-}}"\n'
        f'[ -n "$root" ] || root={shlex.quote(str(root))}\n'
    )
    ps_root = (
        f"Join-Path $env:USERPROFILE '{default_directory}'" if root is None else
        "'" + str(root).replace("'", "''") + "'"
    )
    cmd_root = (
        "%USERPROFILE%\\" + default_directory.replace("/", "\\")
        if root is None else str(root).replace("%", "%%")
    )
    shell = (
        "#!/bin/sh\n"
        + shell_root +
        'version=$(cat "$root/current-version") || exit\n'
        'case "$version" in [0-9]*) ;; *) '
        "echo 'Invalid current application version' >&2; exit 1;; esac\n"
        'case "$version" in ""|*[!0-9A-Za-z._+-]*) '
        "echo 'Invalid current application version' >&2; exit 1;; esac\n"
        f'exec "$root/versions/$version/{name}.sh" "$@"\n'
    )
    return {
        name: shell,
        name + ".sh": shell,
        name + ".ps1": (
            "$ErrorActionPreference = 'Stop'\n"
            f"$root = if ($env:{root_environment}) {{ $env:{root_environment} }} "
            f"else {{ {ps_root} }}\n"
            "$version = [IO.File]::ReadAllText((Join-Path $root 'current-version')).Trim()\n"
            "if ($version -notmatch '^[0-9][0-9A-Za-z._+-]*$') { throw 'Invalid current application version' }\n"
            "$slot = Join-Path (Join-Path $root 'versions') $version\n"
            f"& (Join-Path $slot '{name}.ps1') @args\n"
            "exit $LASTEXITCODE\n"
        ),
        name + ".cmd": (
            "@echo off\r\nsetlocal EnableExtensions DisableDelayedExpansion\r\n"
            f'if not defined {root_environment} set "{root_environment}={cmd_root}"\r\n'
            f'set "ROOT=%{root_environment}%"\r\n'
            'set "VERSION="\r\nset /p "VERSION="<"%ROOT%\\current-version"\r\n'
            "if errorlevel 1 exit /b 1\r\n"
            "setlocal EnableDelayedExpansion\r\n"
            'if "!VERSION!"=="" exit /b 1\r\n'
            'for /f "delims=0123456789" %%I in ("!VERSION:~0,1!") do exit /b 1\r\n'
            'for /f "delims=0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ._+-" '
            '%%I in ("!VERSION!") do exit /b 1\r\n'
            'for /f "delims=" %%I in ("!VERSION!") do (\r\n'
            '    endlocal\r\n    set "VERSION=%%I"\r\n)\r\n'
            f'"%ROOT%\\versions\\%VERSION%\\{name}.cmd" %*\r\n'
        ),
    }


def write_slot_binstubs(slot: Path, name: str, module: str) -> None:
    launchers = slot_binstubs(name, module)
    for filename in launchers:
        path = slot / filename
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"application launcher already exists: {path}")
    for filename, text in launchers.items():
        path = slot / filename
        path.write_text(text, encoding="utf-8", newline="")
        if path.suffix == ".sh" or not path.suffix:
            path.chmod(0o700)
