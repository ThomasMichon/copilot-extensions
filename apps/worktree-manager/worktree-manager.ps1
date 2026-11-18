$ErrorActionPreference = 'Stop'
$slot = $PSScriptRoot
foreach ($key in @('UV_PROJECT','UV_PROJECT_ENVIRONMENT','UV_NO_SYNC','UV_NO_PROJECT','UV_WORKING_DIR','UV_FROZEN','PYTHONPATH','PYTHONHOME','VIRTUAL_ENV')) {
    Remove-Item "Env:$key" -ErrorAction SilentlyContinue
}
if (-not $env:UV_CACHE_DIR) {
    $root = Split-Path -Parent (Split-Path -Parent $slot)
    $env:UV_CACHE_DIR = Join-Path $root 'cache\uv'
}
$forwarded = @($args)
if (Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue) {
    $PSNativeCommandArgumentPassing = 'Standard'
} else {
    $forwarded = @($args | ForEach-Object { if ($_ -eq '') { '""' } else { $_ } })
}
foreach ($file in @('uv.lock', '.venv\pyvenv.cfg', '.venv\Scripts\python.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $slot $file) -PathType Leaf)) {
        throw 'Application is not staged; run bin/setup --stage'
    }
}
& uv run --locked --no-dev --no-editable --link-mode hardlink --managed-python --no-sync --no-python-downloads --project $slot python -I -X utf8 -B (Join-Path $slot 'worktree-manager.py') @forwarded
exit $LASTEXITCODE
