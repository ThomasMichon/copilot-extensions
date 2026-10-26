function Resolve-LegacyTransactionRoot {
    param([string]$Root)
    if ($Root -split '[\\/]' -contains '..') {
        throw 'Legacy runtime paths must not contain raw .. components'
    }
    $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Root)
    if ($full.TrimEnd('\', '/') -eq [IO.Path]::GetPathRoot($full).TrimEnd('\', '/') -or
        $full.TrimEnd('\', '/') -eq [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\', '/')) {
        throw 'Legacy runtime requires a dedicated directory, not a root or user home'
    }
    $cursor = $full
    while ($cursor) {
        $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($item -and (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
            throw 'Legacy runtime path must have ordinary directory ancestors'
        }
        $parent = Split-Path -Parent $cursor
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    $lock = Get-Item -LiteralPath (Join-Path $full '.install.lock') -Force -ErrorAction SilentlyContinue
    if ($lock -and ($lock.PSIsContainer -or ($lock.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
        throw 'Install transaction lock must be an ordinary file'
    }
    return $full
}

function Enter-LegacyTransaction {
    param([string]$Root, [int]$MaximumWaitSec = 0)
    [void][IO.Directory]::CreateDirectory($Root)
    $lock = Join-Path $Root '.install.lock'
    $item = Get-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
    if ($item -and ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint))) {
        throw 'Install transaction lock must be an ordinary file'
    }
    $timeout = 300
    $parsed = 0
    if ([int]::TryParse([string]$env:COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC, [ref]$parsed) -and $parsed -gt 0) {
        $timeout = $parsed
    }
    if ($MaximumWaitSec -gt 0) { $timeout = [Math]::Min($timeout, $MaximumWaitSec) }
    $deadline = (Get-Date).AddSeconds($timeout)
    while ($true) {
        try {
            return [IO.File]::Open($lock, 'OpenOrCreate', 'ReadWrite', 'None')
        } catch [IO.IOException] {
            $code = $_.Exception.HResult -band 0xFFFF
            if ($env:OS -eq 'Windows_NT' -and $code -notin @(32, 33)) { throw }
            if ((Get-Date) -ge $deadline) {
                throw 'Could not acquire the install transaction lock; refusing to proceed unlocked'
            }
            Start-Sleep -Milliseconds 100
        }
    }
}
