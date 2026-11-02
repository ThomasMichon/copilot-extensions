# Report-only compatibility contract: provision-status-v1
# Report retained failures without resolving, previewing or constructing runtimes.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$status = Join-Path $env:USERPROFILE '.agent-worktrees\logs\provision-status.json'
try {
    $entry = Get-Item -LiteralPath $status -Force -ErrorAction Stop
    if ($entry -isnot [IO.FileInfo] -or
        ($entry.Attributes -band ([IO.FileAttributes]::Directory -bor
            [IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Device)) -or
        $entry.Length -gt 65536) {
        throw 'Status must be an ordinary bounded file.'
    }
} catch [System.Management.Automation.ItemNotFoundException] {
    exit 0
} catch {
    [Console]::Error.WriteLine("[agent-worktrees] Could not read provisioning status: $status")
    exit 0
}
try {
    $stream = [IO.File]::Open($status, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    try {
        if (-not $stream.CanSeek) { throw 'Status is not an ordinary file stream.' }
        $bytes = New-Object byte[] 65537
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
    } finally { $stream.Dispose() }
    if ($count -gt 65536) { throw 'Status exceeds the report budget.' }
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    $text = $utf8.GetString($bytes, 0, $count).TrimStart([char[]]@([char]0xfeff))
    if (-not $text.TrimStart().StartsWith('{')) { throw 'Status is not an object.' }
    $previous = $text | ConvertFrom-Json
    if ($previous.ok -isnot [bool] -or $previous.ok -or
        $previous.repo -isnot [string] -or -not $previous.repo) { exit 0 }
    $location = Get-Location
    if ($location.Provider.Name -ne 'FileSystem') { exit 0 }
    if ([IO.Path]::GetFullPath($previous.repo) -ne
        [IO.Path]::GetFullPath($location.ProviderPath)) { exit 0 }
    $names = @($previous.failed | Where-Object {
        $_.service -is [string] -and $_.service
    } | Select-Object -First 8 | ForEach-Object {
        $_.service.Substring(0, [Math]::Min(200, $_.service.Length))
    })
    $failed = $names -join ', '
    if (-not $failed) {
        $failed = if ($previous.reason -is [string] -and $previous.reason) {
            $previous.reason.Substring(0, [Math]::Min(512, $previous.reason.Length))
        } else { 'unknown failure' }
    }
    [Console]::Error.WriteLine(
        "[agent-worktrees] Previous background provisioning failed: $failed. " +
        "Inspect $(Split-Path -Parent $status)\provision-*.log* and rerun reconcile-plugins."
    )
} catch {
    [Console]::Error.WriteLine("[agent-worktrees] Could not read provisioning status: $status")
}
exit 0
