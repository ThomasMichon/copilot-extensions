function Get-BridgePayloadHash {
    param([Parameter(Mandatory)][string]$Payload)
    # Preserve the installer's existing Windows completion fingerprint.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $base = (Resolve-Path -LiteralPath $Payload).Path
        $files = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
        $pp = Join-Path $base 'pyproject.toml'
        if (Test-Path -LiteralPath $pp) { $files.Add((Get-Item -LiteralPath $pp)) }
        foreach ($sub in @('src', 'libs')) {
            $d = Join-Path $base $sub
            if (Test-Path -LiteralPath $d) {
                Get-ChildItem -LiteralPath $d -Recurse -File -Force -ErrorAction Stop |
                    Where-Object {
                        $_.FullName -notmatch '[\\/](__pycache__|\.venv|venv|\.pytest_cache|\.mypy_cache|build|dist|[^\\/]+\.egg-info)[\\/]' -and
                        $_.Extension -ne '.pyc'
                    } | ForEach-Object { $files.Add($_) }
            }
        }
        $entries = foreach ($f in $files) {
            $rel = ($f.FullName.Substring($base.Length).TrimStart('\', '/')) -replace '\\', '/'
            $fh = [BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($f.FullName))).Replace('-', '').ToLower()
            "${rel}:${fh}"
        }
        $joined = [string]::Join("`n", ($entries | Sort-Object))
        return (-join ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($joined)) |
            ForEach-Object { $_.ToString('x2') }))
    } finally {
        $sha.Dispose()
    }
}
