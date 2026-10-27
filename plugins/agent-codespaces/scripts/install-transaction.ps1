function Enter-CodespacesInstallTransaction {
    $timeout = 300
    $parsed = 0
    if ([int]::TryParse([string]$env:COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC, [ref]$parsed) -and $parsed -gt 0) {
        $timeout = $parsed
    }
    $lock = Join-Path $InstallDir '.install.lock'
    if (@($InstallDir -split '[\\/]') -contains '..') {
        throw 'Codespaces install transaction refuses parent traversal'
    }
    $full = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
    $homePath = [IO.Path]::GetFullPath($HOME).TrimEnd('\', '/')
    if ($full -eq ([IO.Path]::GetPathRoot($full)).TrimEnd('\', '/') -or
        $homePath -eq $full -or $homePath.StartsWith($full + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Codespaces install transaction refuses a filesystem root or user-home ancestor'
    }
    $paths = @($lock)
    $cursor = [IO.DirectoryInfo]::new($full)
    while ($cursor) { $paths += $cursor.FullName; $cursor = $cursor.Parent }
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Codespaces install transaction refuses redirected ownership'
            }
        }
    }
    [IO.Directory]::CreateDirectory($InstallDir) | Out-Null
    $deadline = (Get-Date).AddSeconds($timeout)
    while ($true) {
        try {
            return [IO.File]::Open($lock, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
        } catch [IO.IOException] {
            if ((Get-Date) -ge $deadline) { throw 'Codespaces install transaction is busy; refusing to proceed unlocked' }
            Start-Sleep -Milliseconds 100
        }
    }
}
