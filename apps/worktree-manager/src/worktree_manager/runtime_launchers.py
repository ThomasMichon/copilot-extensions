"""Manager-owned native launchers for retained and compatible legacy runtimes."""

import base64

_CACHE_SELECTOR_SH = (
    "import sys\n"
    "try:\n"
    "    from agent_procutil.python_cache import POLICY_VERSION, policy_arguments\n"
    "    assert POLICY_VERSION == 2\n"
    "    arguments = policy_arguments(sys.argv[1], runtime=sys.executable)\n"
    "    assert arguments == [\"-B\"] or (len(arguments) == 2 and arguments[0] == \"-X\"\n"
    "        and arguments[1].startswith(\"pycache_prefix=\") and len(arguments[1]) > 15\n"
    "        and not any(c in arguments[1] for c in \"\\r\\n\\0\"))\n"
    "except Exception:\n"
    "    arguments = [\"-B\"]\n"
    'sys.stdout.buffer.write("\\n".join(arguments).encode("utf-8"))\n'
)
_CACHE_SELECTOR_PS = _CACHE_SELECTOR_SH.replace('"', "'").replace(
    "sys.stdout.buffer.write('\\n'.join(arguments).encode('utf-8'))",
    "import json; print(json.dumps(arguments))",
)
# CMD's command-output parser cannot safely carry arbitrary paths. Unsupported
# code-page or metacharacter spellings take -B without a temporary output file.
_CACHE_SELECTOR_CMD_BODY = (
    "import sys,importlib.util as u;"
    "m=u.find_spec('agent_procutil') and __import__('agent_procutil.python_cache',fromlist=['_']);"
    "a=m.policy_arguments(sys.argv[1],runtime=sys.executable) if m and getattr(m,'POLICY_VERSION',0)==2 else ['-B'];"
    "q=a[1] if len(a)==2 and a[0]=='-X' and a[1].startswith('pycache_prefix=') else '';"
    "safe=q and q.isascii() and all(ord(c)>=32 and ord(c) not in "
    "(33,34,37,38,39,40,41,60,62,94,124) for c in q);"
    "sys.stdout.write('-X '+chr(34)+q+chr(34) if safe else '-B')"
)
_CACHE_SELECTOR_CMD = (
    "import base64;exec(base64.b64decode('"
    + base64.b64encode(_CACHE_SELECTOR_CMD_BODY.encode("utf-8")).decode("ascii") + "'))"
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
        'if [[ -f "$slot/.runtime-bootstrap/runtime_launcher.py" && -x "$python" ]]; then\n'
        '    exec "$python" -I -S -B -X utf8 "$slot/.runtime-bootstrap/runtime_launcher.py" '
        '-m worktree_manager "$@"\n'
        'fi\n'
        'python="$slot/.venv/bin/python"\n'
        'if [[ -x "$slot/.venv/Scripts/python.exe" ]]; then\n'
        '    python="$slot/.venv/Scripts/python.exe"\n'
        'fi\n'
        'if [[ -x "$python" ]]; then\n'
        f'    if ! "$python" -I -B -c "{probe}" "$slot" >/dev/null 2>&1; then\n'
        '        echo "invalid constructed worktree-manager runtime: $slot" >&2\n'
        '        exit 1\n'
        '    fi\n'
        '    selector_root="$root"\n'
        '    if [[ "$python" == *.exe ]]; then selector_root="$(cygpath -aw "$root")" || selector_root=""; fi\n'
        '    cache_args=(-B)\n'
        f'    selected="$("$python" -I -B -X utf8 -c \'{_CACHE_SELECTOR_SH}\' "$selector_root" 2>/dev/null)" || selected=""\n'
        '    if [[ "$selected" == $\'-X\\npycache_prefix=\'* ]]; then\n'
        '        cache_args=(-X "${selected#*$\'\\n\'}")\n'
        '    fi\n'
        '    exec "$python" -I -X utf8 "${cache_args[@]}" -m worktree_manager "$@"\n'
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
        "setlocal DisableDelayedExpansion\r\n"
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
        'set "VER="\r\n'
        'for /L %%R in (1,1,4096) do (\r\n'
        '    (set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version") 2>nul\r\n'
        '    if defined VER goto selector_read\r\n'
        '    if errorlevel 1 goto selector_final\r\n'
        ')\r\n'
        ':selector_final\r\n'
        'set /p VER=<"%WORKTREE_MANAGER_ROOT%\\current-version"\r\n'
        ':selector_read\r\n'
        'if "%VER%"=="" exit /b 1\r\n'
        'set "SLOT=%WORKTREE_MANAGER_ROOT%\\versions\\%VER%"\r\n'
        'if exist "%SLOT%\\.runtime-bootstrap\\runtime_launcher.py" goto retained\r\n'
        'if not exist "%SLOT%\\.venv\\Scripts\\python.exe" goto invalid\r\n'
        f'"%SLOT%\\.venv\\Scripts\\python.exe" -I -B -c "{probe}" "%SLOT%" >nul 2>&1\r\n'
        'if errorlevel 1 goto invalid\r\n'
        'set "CACHE_ARGS=-B"\r\n'
        'for /f "delims=" %%C in (\'call "%SLOT%\\.venv\\Scripts\\python.exe" -I -B -X utf8 '
        f'-c "{_CACHE_SELECTOR_CMD}" "%WORKTREE_MANAGER_ROOT%" 2^>nul\') do set "CACHE_ARGS=%%C"\r\n'
        '"%SLOT%\\.venv\\Scripts\\python.exe" -I -X utf8 %CACHE_ARGS% -m worktree_manager %* '
        '& call exit /b %%errorlevel%%\r\n'
        ':invalid\r\n'
        '>&2 echo invalid constructed worktree-manager runtime: "%SLOT%"\r\n'
        'exit /b 1\r\n'
        ':retained\r\n'
        '"%SLOT%\\Scripts\\python.exe" -I -S -B -X utf8 "%SLOT%\\.runtime-bootstrap\\runtime_launcher.py" '
        '-m worktree_manager %* & call exit /b %%errorlevel%%\r\n'
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
        '    Invoke-ManagerTerminalPython (Join-Path $slot "Scripts\\python.exe") (@("-I", "-S", "-B", "-X", "utf8", $bootstrap, "-m", "worktree_manager") + $args) @("-I", "-S", "-B", "-X", "utf8", $bootstrap, "-m", "worktree_manager", "--version")\n'
        '    exit $LASTEXITCODE\n'
        '}\n'
        '$python = Join-Path $slot ".venv\\Scripts\\python.exe"\n'
        'if (Test-Path -LiteralPath $python -PathType Leaf) {\n'
        f'    & $python -I -B -c "{probe}" $slot 2>$null\n'
        '    if ($LASTEXITCODE -ne 0) {\n'
        '        [Console]::Error.WriteLine("invalid constructed worktree-manager runtime: $slot")\n'
        '        exit 1\n'
        '    }\n'
        '    $cacheArguments = @("-B")\n'
        "    $selector = @'\n"
        f'{_CACHE_SELECTOR_PS}'
        "'@\n"
        '    $selected = & $python -I -B -X utf8 -c $selector $root\n'
        '    if ($LASTEXITCODE -eq 0 -and @($selected).Count -eq 1) {\n'
        '        try {\n'
        '            $candidate = ConvertFrom-Json -InputObject ([string]$selected)\n'
        '            if ($candidate.Count -eq 2 -and $candidate[0] -ceq "-X" -and\n'
        '                $candidate[1] -is [string] -and $candidate[1].StartsWith("pycache_prefix=") -and\n'
        '                $candidate[1].Length -gt 15 -and $candidate[1] -notmatch "[`r`n]" -and\n'
        '                $candidate[1].IndexOf([char]0) -lt 0) { $cacheArguments = $candidate }\n'
        '        } catch {}\n'
        '    }\n'
        '    Invoke-ManagerTerminalPython $python (@("-I", "-X", "utf8") + $cacheArguments + @("-m", "worktree_manager") + $args) @("-I", "-B", "-m", "worktree_manager", "--version")\n'
        '    $code = $LASTEXITCODE\n'
        '    exit $code\n'
        '}\n'
        '[Console]::Error.WriteLine("invalid constructed worktree-manager runtime: $slot; run bootstrap to repair")\n'
        "exit 1\n"
    )
