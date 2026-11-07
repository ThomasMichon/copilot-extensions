"""Manager-owned native launchers for retained and compatible legacy runtimes."""

# Reuse the runtime's sealed ``installed-<digest>`` bytecode cache when its
# install evidence is valid; otherwise keep the launcher-minted unsealed prefix.
_CACHE_SELECTOR_SH = (
    "import os, sys\n"
    "try:\n"
    "    from agent_procutil.python_cache import cache_arguments\n"
    "    prefix = cache_arguments(sys.argv[1], runtime=sys.executable)[1].split(\"=\", 1)[1]\n"
    "    assert os.path.basename(prefix).startswith(\"installed-\")\n"
    "except Exception:\n"
    "    prefix = sys.pycache_prefix\n"
    "sys.stdout.write(prefix)\n"
)
_CACHE_SELECTOR_PS = _CACHE_SELECTOR_SH.replace('"', "'").replace(
    "sys.stdout.write(prefix)", "import json; print(json.dumps(prefix))"
)
# Single-line, double-quote-free form for cmd. It prints only an ASCII
# installed-* prefix; on fallback or failure it prints nothing (or exits
# non-zero) so the caller keeps its minted prefix untouched, because ``set /p``
# reads the result in the console code page.
_CACHE_SELECTOR_CMD = (
    "import os,sys,importlib.util as u;"
    "m=u.find_spec('agent_procutil') and __import__('agent_procutil.python_cache',fromlist=['_']);"
    "q=m and m.cache_arguments(sys.argv[1],runtime=sys.executable)[1].split('=',1)[1];"
    "sys.stdout.write(q if q and os.path.basename(q).startswith('installed-') and q.isascii() else '')"
)


def _legacy_runtime_probe() -> str:
    from .self_install import _SLOT_COMPLETE_MARKER, _SLOT_KEY_FILES

    return (
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
        f"{(_SLOT_COMPLETE_MARKER, *_SLOT_KEY_FILES)!r})"
    )


def _sh_binstub() -> str:
    probe = _legacy_runtime_probe()
    return (
        "#!/usr/bin/env bash\n"
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
        'set -euo pipefail\n'
        'unset __PYVENV_LAUNCHER__\n'
        'root="${WORKTREE_MANAGER_ROOT:-$HOME/.worktree-manager}"\n'
        'export WORKTREE_MANAGER_ROOT="$root"\n'
        'ver="$(cat "$root/current-version")"\n'
        'slot="$root/versions/$ver"\n'
        'python="$slot/bin/python"\n'
        '[[ ! -x "$slot/Scripts/python.exe" ]] || python="$slot/Scripts/python.exe"\n'
        'cache_id="$(od -An -N16 -tx1 /dev/urandom | tr -d \' \\n\')"\n'
        '[[ "$cache_id" =~ ^[0-9a-f]{32}$ ]] || exit 1\n'
        'cache="$root/cache/python/unsealed-$cache_id"\n'
        'if [[ "$python" = *.exe || -x "$slot/.venv/Scripts/python.exe" ]]; then\n'
        '    cache="$(cygpath -aw "$cache")"\n'
        "    case \"$cache\" in\n"
        "        '\\\\?\\'*) ;;\n"
        "        '\\\\'*) cache='\\\\?\\UNC\\'\"${cache#'\\\\'}\" ;;\n"
        "        *) cache='\\\\?\\'\"$cache\" ;;\n"
        "    esac\n"
        'else\n'
        '    [[ "$cache" = /* ]] || cache="$PWD/$cache"\n'
        'fi\n'
        'if [[ -f "$slot/.runtime-bootstrap/runtime_launcher.py" && -x "$python" ]]; then\n'
        '    exec "$python" -I -S -B -X utf8 -X "pycache_prefix=$cache" "$slot/.runtime-bootstrap/runtime_launcher.py" '
        '-m worktree_manager "$@"\n'
        'fi\n'
        'python="$slot/.venv/bin/python"\n'
        'if [[ -x "$slot/.venv/Scripts/python.exe" ]]; then\n'
        '    python="$slot/.venv/Scripts/python.exe"\n'
        'fi\n'
        'if [[ -x "$python" ]]; then\n'
        f'    if ! "$python" -I -B -X "pycache_prefix=$cache" -c "{probe}" "$slot" >/dev/null 2>&1; then\n'
        '        echo "invalid constructed worktree-manager runtime: $slot" >&2\n'
        '        exit 1\n'
        '    fi\n'
        '    selector_root="$root"\n'
        '    [[ "$python" != *.exe ]] || selector_root="$(cygpath -aw "$root")"\n'
        f'    selected="$("$python" -I -B -X utf8 -X "pycache_prefix=$cache" -c \'{_CACHE_SELECTOR_SH}\' "$selector_root" 2>/dev/null)" '
        '&& [[ -n "$selected" && "$selected" != *$\'\\n\'* ]] && cache="$selected"\n'
        '    exec "$python" -I -X utf8 -X "pycache_prefix=$cache" -m worktree_manager "$@"\n'
        'fi\n'
        'echo "invalid constructed worktree-manager runtime: $slot; run bootstrap to repair" >&2\n'
        'exit 1\n'
    )


def _cmd_binstub() -> str:
    probe = _legacy_runtime_probe()
    # cmd resumes batch files by byte offset. Read the exit on the invocation
    # line before a long-running command can update this file; CALL expands
    # ERRORLEVEL after the child exits without enabling delayed argument expansion.
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "ERRORLEVEL="\r\n'
        'if not defined CE_TERMINAL_LAUNCH goto terminal_host\r\n'
        'goto runtime\r\n'
        ':terminal_host\r\n'
        'for %%P in (pwsh.exe) do if "%%~$PATH:P"=="" goto runtime\r\n'
        'pwsh.exe -NoProfile -NoLogo -File "%~dp0worktree-manager.ps1" %* & call exit /b %%errorlevel%%\r\n'
        ':runtime\r\n'
        'set "PYTHONUTF8=1"\r\n'
        'set "__PYVENV_LAUNCHER__="\r\n'
        'if "%WORKTREE_MANAGER_ROOT%"=="" set "WORKTREE_MANAGER_ROOT=%USERPROFILE%\\.worktree-manager"\r\n'
        'for %%I in ("%WORKTREE_MANAGER_ROOT%\\cache\\python") do set "CACHE=%%~fI"\r\n'
        'if "%CACHE:~0,4%"=="\\\\?\\" goto cache_ready\r\n'
        'if "%CACHE:~0,2%"=="\\\\" (set "CACHE=\\\\?\\UNC\\%CACHE:~2%") else (set "CACHE=\\\\?\\%CACHE%")\r\n'
        ':cache_ready\r\n'
        'if not exist "%CACHE%" mkdir "%CACHE%" >nul 2>&1\r\n'
        'if not exist "%CACHE%" exit /b 1\r\n'
        'set "CACHE_ROOT=%CACHE%"\r\n'
        'set "CACHE_ATTEMPTS=0"\r\n'
        ':reserve_cache\r\n'
        'set /a CACHE_ATTEMPTS+=1 >nul\r\n'
        'if %CACHE_ATTEMPTS% GTR 16 exit /b 1\r\n'
        'set "CACHE=%CACHE_ROOT%\\unsealed-%RANDOM%-%RANDOM%-%RANDOM%-%RANDOM%"\r\n'
        'mkdir "%CACHE%" >nul 2>&1\r\n'
        'if errorlevel 1 goto reserve_cache\r\n'
        'set "VER="\r\n'
        'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
        'if "%VER%"=="" (rmdir "%CACHE%" >nul 2>&1 & exit /b 1)\r\n'
        'set "SLOT=%WORKTREE_MANAGER_ROOT%\\versions\\%VER%"\r\n'
        'if exist "%SLOT%\\.runtime-bootstrap\\runtime_launcher.py" goto retained\r\n'
        'if not exist "%SLOT%\\.venv\\Scripts\\python.exe" goto invalid\r\n'
        f'"%SLOT%\\.venv\\Scripts\\python.exe" -I -B -X "pycache_prefix=%CACHE%" -c "{probe}" "%SLOT%" >nul 2>&1\r\n'
        'if errorlevel 1 goto invalid\r\n'
        'set "MINTED=%CACHE%"\r\n'
        'set "SELECTED="\r\n'
        f'"%SLOT%\\.venv\\Scripts\\python.exe" -I -B -X utf8 -X "pycache_prefix=%MINTED%" -c "{_CACHE_SELECTOR_CMD}" '
        '"%WORKTREE_MANAGER_ROOT%" >"%MINTED%\\.selected" 2>nul\r\n'
        'if not errorlevel 1 set /p SELECTED=<"%MINTED%\\.selected"\r\n'
        'del /f /q "%MINTED%\\.selected" >nul 2>&1\r\n'
        'if defined SELECTED set "CACHE=%SELECTED%"\r\n'
        'if not "%CACHE%"=="%MINTED%" rmdir /s /q "%MINTED%" >nul 2>&1\r\n'
        '"%SLOT%\\.venv\\Scripts\\python.exe" -I -X utf8 -X "pycache_prefix=%CACHE%" -m worktree_manager %* '
        '& call set "CACHE_RC=%%errorlevel%%" & rmdir /s /q "%MINTED%" >nul 2>&1 & call exit /b %%CACHE_RC%%\r\n'
        ':invalid\r\n'
        'rmdir /s /q "%CACHE%" >nul 2>&1\r\n'
        '>&2 echo invalid constructed worktree-manager runtime: "%SLOT%"\r\n'
        'exit /b 1\r\n'
        ':retained\r\n'
        '"%SLOT%\\Scripts\\python.exe" -I -S -B -X utf8 -X "pycache_prefix=%CACHE%" "%SLOT%\\.runtime-bootstrap\\runtime_launcher.py" '
        '-m worktree_manager %* & call set "CACHE_RC=%%errorlevel%%" & rmdir /s /q "%CACHE%" >nul 2>&1 '
        '& call exit /b %%CACHE_RC%%\r\n'
    )


def _ps1_binstub() -> str:
    probe = _legacy_runtime_probe()
    return (
        "# worktree-manager binstub (versioned) — resolves the current-version marker.\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$PSNativeCommandUseErrorActionPreference = $false\n"
        "$env:PYTHONUTF8 = '1'\n"
        "$env:__PYVENV_LAUNCHER__ = $null\n"
        '$root = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } '
        'else { Join-Path $env:USERPROFILE ".worktree-manager" }\n'
        '$env:WORKTREE_MANAGER_ROOT = $root\n'
        '$cacheId = [Guid]::NewGuid().ToString("N")\n'
        '$cache = [IO.Path]::GetFullPath((Join-Path $root "cache\\python\\unsealed-$cacheId"))\n'
        'if (-not $cache.StartsWith("\\\\?\\")) {\n'
        '    $cache = if ($cache.StartsWith("\\\\")) { "\\\\?\\UNC\\" + $cache.Substring(2) }\n'
        '    else { "\\\\?\\" + $cache }\n'
        '}\n'
        '$ver = (Get-Content (Join-Path $root "current-version") -Raw -ErrorAction Stop).Trim()\n'
        '$slot = Join-Path (Join-Path $root "versions") $ver\n'
        '$terminalHost = Join-Path $slot "payload\\bin\\terminal-launch.ps1"\n'
        'if (-not (Test-Path -LiteralPath $terminalHost)) { $terminalHost = Join-Path $slot "bin\\terminal-launch.ps1" }\n'
        'function Invoke-ManagerTerminalPython([string]$Python, [string[]]$PythonArguments, [string[]]$QueryArguments) {\n'
        '    if (Test-Path -LiteralPath $terminalHost -PathType Leaf) {\n'
        '        . $terminalHost\n'
        '        Invoke-WorktreeTerminalLaunch -Executable $Python -Arguments $PythonArguments -QueryArguments $QueryArguments -Package worktree_manager\n'
        '    } else { & $Python @PythonArguments }\n'
        '}\n'
        '$bootstrap = Join-Path $slot ".runtime-bootstrap\\runtime_launcher.py"\n'
        'if (Test-Path -LiteralPath $bootstrap -PathType Leaf) {\n'
        '    Invoke-ManagerTerminalPython (Join-Path $slot "Scripts\\python.exe") (@("-I", "-S", "-B", "-X", "utf8", "-X", "pycache_prefix=$cache", $bootstrap, "-m", "worktree_manager") + $args) @("-I", "-S", "-B", "-X", "utf8", $bootstrap, "-m", "worktree_manager", "--version")\n'
        '    exit $LASTEXITCODE\n'
        '}\n'
        '$python = Join-Path $slot ".venv\\Scripts\\python.exe"\n'
        'if (Test-Path -LiteralPath $python -PathType Leaf) {\n'
        f'    & $python -I -B -X "pycache_prefix=$cache" -c "{probe}" $slot 2>$null\n'
        '    if ($LASTEXITCODE -ne 0) {\n'
        '        [Console]::Error.WriteLine("invalid constructed worktree-manager runtime: $slot")\n'
        '        exit 1\n'
        '    }\n'
        '    $minted = $cache\n'
        "    $selector = @'\n"
        f'{_CACHE_SELECTOR_PS}'
        "'@\n"
        '    $selected = & $python -I -B -X "pycache_prefix=$cache" -c $selector $root\n'
        '    if ($LASTEXITCODE -eq 0 -and @($selected).Count -eq 1) { try { $cache = [string]($selected | ConvertFrom-Json) } catch { $cache = $minted } }\n'
        '    Invoke-ManagerTerminalPython $python (@("-I", "-X", "utf8", "-X", "pycache_prefix=$cache", "-m", "worktree_manager") + $args) @("-I", "-B", "-m", "worktree_manager", "--version")\n'
        '    $code = $LASTEXITCODE\n'
        '    if ($cache -eq $minted) { try { [IO.Directory]::Delete($cache, $true) } catch {} }\n'
        '    exit $code\n'
        '}\n'
        '[Console]::Error.WriteLine("invalid constructed worktree-manager runtime: $slot; run bootstrap to repair")\n'
        "exit 1\n"
    )
