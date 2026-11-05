param([switch]$Probe)

function Invoke-NativeCapture {
    <# Windows PowerShell 5.1 promotes native stderr to NativeCommandError.
       Scope ErrorActionPreference to Continue so callers can inspect the real
       exit code and output, then restore the installer's fail-fast behavior. #>
    param([Parameter(Mandatory)][scriptblock]$Command)

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = (& $Command 2>&1 | Out-String -Width 4096).Trim()
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
}

function Get-SignedBasePython {
    <# Return the path to a SAC-trusted (Authenticode-signed) base Python
       (>=3.11), or $null. Smart App Control blocks the unsigned uv-managed
       Python and the console-script trampoline .exe; building the venv from a
       signed base with `--copies` embeds a signed python.exe in the venv
       (Authenticode survives the copy), which SAC allows.

       Candidates are gathered from several sources because none is reliable on
       its own: the `py` launcher is absent on some Cloud PCs, so
       we also scan the well-known all-users / per-user install roots (where a
       signed `C:\Program Files\Python312\python.exe` lives) and any
       `python`/`python3` on PATH -- skipping the WindowsApps App Execution
       Alias, which is a 0-byte reparse stub, not a real interpreter. Each
       candidate is verified to be a real interpreter >=3.11 before its
       signature is checked. #>
    $cands = @()

    # 1. py launcher (when present) -- newest first.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11') {
            $result = Invoke-NativeCapture {
                & py "-$v" -I -S -B -c 'import sys;print(sys.executable)'
            }
            if ($result.ExitCode -eq 0 -and $result.Output) {
                $cands += $result.Output
            }
        }
    }

    # 2. Well-known install roots (all-users + per-user); newest version first.
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs\Python'))
    foreach ($root in $roots) {
        if (-not $root) { continue }
        Get-ChildItem -Path $root -Filter 'Python3*' -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path $exe) { $cands += $exe }
            }
    }

    # 3. python / python3 on PATH, minus the WindowsApps alias reparse stub.
    foreach ($name in 'python', 'python3') {
        Get-Command $name -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -notmatch '\\WindowsApps\\' } |
            ForEach-Object { $cands += $_.Path }
    }

    foreach ($c in ($cands | Select-Object -Unique)) {
        if (-not (Test-Path $c)) { continue }
        # Must be a real interpreter >= 3.11 ...
        $result = Invoke-NativeCapture {
            & $c -I -S -B -c 'import sys;print("%d.%d" % sys.version_info[:2])'
        }
        $ver = if ($result.ExitCode -eq 0) { $result.Output } else { '' }
        if (-not ($ver -match '^(\d+)\.(\d+)$')) { continue }
        if ([int]$Matches[1] -lt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -lt 11)) { continue }
        # ... and SAC-trusted (Authenticode Valid).
        try {
            if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') { return $c }
        } catch {}
    }
    return $null
}

if ($Probe) {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    @{ python = (Get-SignedBasePython) } | ConvertTo-Json -Compress
}
