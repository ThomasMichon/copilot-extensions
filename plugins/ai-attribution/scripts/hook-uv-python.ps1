# Canonical source: libs/hook-uv-python/hook-uv-python.ps1. The copy in each
# adopting plugin's scripts/ directory is vendored byte-identically by
# tools/sync-hook-uv-python.py; edit only the canonical file.
#
# Dot-source this file, then call
# `Invoke-HookUvPython -ArgumentList @(<python arguments>) [-InputText <stdin>]`.
# It runs the uv-managed CPython named below through `uv run`. A session hook
# never acquires Python: explicit setup (the plugin installer or bootstrap)
# prepares it, so when it is missing uv reports the missing interpreter and the
# hook keeps its own no-op result. A system Python is never used as a fallback.
# When uv is not available, it writes a clear message and sets $LASTEXITCODE to
# 127. Compatible with Windows PowerShell 5.1. The version
# matches the repository's .python-version.
$HookUvPythonVersion = '3.11'

function Invoke-HookUvPython {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$ArgumentList,
        $InputText = $null
    )

    $uvPath = $null
    $found = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { $uvPath = $found.Source }
    if (-not $uvPath) {
        $userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
        if ($userHome) {
            foreach ($name in @('uv.exe', 'uv')) {
                foreach ($dir in @('.local', '.cargo')) {
                    $candidate = Join-Path (Join-Path (Join-Path $userHome $dir) 'bin') $name
                    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                        $uvPath = $candidate
                        break
                    }
                }
                if ($uvPath) { break }
            }
        }
    }
    if (-not $uvPath) {
        [Console]::Error.WriteLine(
            "uv is required to run this hook's Python, but 'uv' was not found on PATH. " +
            'Install uv (https://docs.astral.sh/uv/getting-started/installation/). ' +
            'A system Python is never used.'
        )
        $global:LASTEXITCODE = 127
        return
    }

    # Inherited selectors must not redirect the interpreter or its imports.
    $names = @(
        'UV_PYTHON', 'UV_PYTHON_PREFERENCE', 'UV_MANAGED_PYTHON', 'UV_NO_MANAGED_PYTHON',
        'VIRTUAL_ENV', 'CONDA_PREFIX', 'PYTHONHOME', 'PYTHONPATH', 'UV_WORKING_DIR',
        'UV_WORKING_DIRECTORY', 'UV_ISOLATED', 'UV_ENV_FILE', 'UV_PROJECT', 'UV_FROZEN',
        'UV_LOCKED', 'UV_NO_SYNC', 'UV_CONFIG_FILE'
    )
    $saved = @{}
    foreach ($name in $names) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name)
        # PowerShell turns $null into '', which would set an empty value.
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    try {
        # No project, configuration, env file, ambient .venv or Python download.
        $uvArguments = @(
            'run', '--quiet', '--isolated', '--no-project', '--no-config', '--no-env-file',
            '--no-python-downloads', '--managed-python', '--python', $HookUvPythonVersion, 'python'
        ) + $ArgumentList
        if ($null -ne $InputText) {
            $InputText | & $uvPath @uvArguments
        } else {
            & $uvPath @uvArguments
        }
    } finally {
        foreach ($name in $names) {
            if ($null -eq $saved[$name]) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($name, $saved[$name])
            }
        }
    }
}