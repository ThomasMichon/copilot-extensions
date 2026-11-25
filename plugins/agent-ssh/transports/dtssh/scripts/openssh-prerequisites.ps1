#Requires -Version 7.0

function Get-AgentSshOpenSSHRelease {
    $architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
    $releases = @{
        X64 = @('Win64', '23f50f3458c4c5d0b12217c6a5ddfde0137210a30fa870e98b29827f7b43aba5')
        Arm64 = @('ARM64', '698c6aec31c1dd0fb996206e8741f4531a97355686b5431ef347d531b07fcd42')
    }
    if (-not $releases.ContainsKey($architecture)) { throw 'OpenSSH requires native x64 or ARM64 Windows.' }
    $selected = $releases[$architecture]
    return @{
        Directory = "OpenSSH-$($selected[0])"
        Uri = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.0.0.0p2-Preview/OpenSSH-$($selected[0]).zip"
        Sha256 = $selected[1]
    }
}

function Assert-AgentSshOrdinaryPath([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if ([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) {
            if ([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) {
                throw 'OpenSSH prerequisite paths must not traverse reparse points.'
            }
        } elseif (Test-Path -LiteralPath $current) {
            throw 'OpenSSH prerequisite path is not an ordinary filesystem path.'
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Get-AgentSshOpenSSHFiles([string]$Directory) {
    Assert-AgentSshOrdinaryPath $Directory
    foreach ($name in @('ssh.exe', 'sshd.exe', 'ssh-keygen.exe')) {
        $path = Join-Path $Directory $name
        if (-not [IO.File]::Exists($path)) { throw "OpenSSH prerequisite is missing: $name" }
        Assert-AgentSshOrdinaryPath $path
    }
    return [pscustomobject]@{
        Directory = $Directory
        Ssh = Join-Path $Directory 'ssh.exe'
        Sshd = Join-Path $Directory 'sshd.exe'
        SshKeygen = Join-Path $Directory 'ssh-keygen.exe'
    }
}

function Save-AgentSshOpenSSHArchive([string]$Uri, [string]$Path) {
    $client = [Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(90)
    $response = $null
    $inputStream = $null
    $outputStream = $null
    try {
        $response = $client.GetAsync($Uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $response.EnsureSuccessStatusCode() | Out-Null
        if ($response.Content.Headers.ContentLength -gt 100MB) { throw 'OpenSSH archive exceeds its size limit.' }
        $inputStream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $outputStream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $buffer = [byte[]]::new(65536)
        $total = 0L
        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        while ($true) {
            $remaining = $deadline - [DateTime]::UtcNow
            if ($remaining.TotalMilliseconds -le 0) { throw 'OpenSSH archive download timed out.' }
            $read = $inputStream.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $read.Wait([int][Math]::Min(90000, $remaining.TotalMilliseconds))) {
                throw 'OpenSSH archive download timed out.'
            }
            $count = $read.GetAwaiter().GetResult()
            if (-not $count) { break }
            $total += $count
            if ($total -gt 100MB) { throw 'OpenSSH archive exceeds its size limit.' }
            $outputStream.Write($buffer, 0, $count)
        }
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Dispose() }
        $client.Dispose()
    }
}

function Expand-AgentSshOpenSSHArchive([string]$Archive, [hashtable]$Release, [string]$Destination) {
    $stream = [IO.File]::Open($Archive, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $zip = $null
    $hash = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
        if ($digest -cne $Release.Sha256) { throw 'OpenSSH archive digest mismatch.' }
        $stream.Position = 0
        $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        if ($zip.Entries.Count -gt 4096) { throw 'OpenSSH archive has too many entries.' }
        $total = 0L
        $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            $parts = $name.TrimEnd('/').Split('/')
            if ($parts[0] -cne $Release.Directory -or -not $names.Add($name.TrimEnd('/')) -or
                (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
                throw 'OpenSSH archive has an invalid or duplicate entry.'
            }
            foreach ($part in $parts) {
                if (-not $part -or $part -in @('.', '..') -or $part -match '[<>:"|?*\x00-\x1f]' -or
                    $part -ne $part.TrimEnd('.', ' ') -or
                    $part -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)') {
                    throw 'OpenSSH archive has an unsafe path.'
                }
            }
            $total += $entry.Length
            if ($total -gt 300MB) { throw 'OpenSSH expanded archive exceeds its size limit.' }
        }
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            $path = [IO.Path]::Combine($Destination, $name.Replace('/', [IO.Path]::DirectorySeparatorChar))
            if ($name.EndsWith('/')) {
                [IO.Directory]::CreateDirectory($path) | Out-Null
            } else {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($path)) | Out-Null
                $inputStream = $entry.Open()
                $outputStream = $null
                try {
                    $outputStream = [IO.File]::Open($path, [IO.FileMode]::CreateNew)
                    $inputStream.CopyTo($outputStream)
                    if ($outputStream.Length -ne $entry.Length) { throw 'OpenSSH archive entry length mismatch.' }
                } finally {
                    if ($outputStream) { $outputStream.Dispose() }
                    $inputStream.Dispose()
                }
            }
        }
    } finally {
        if ($zip) { $zip.Dispose() }
        $hash.Dispose()
        $stream.Dispose()
    }
}

function Install-AgentSshOpenSSH {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Destination)

    if (-not $IsWindows) { throw 'Portable OpenSSH preparation is Windows-only.' }
    if (-not [IO.Path]::IsPathFullyQualified($Destination) -or $Destination.StartsWith('\\')) {
        throw 'Select an absolute local OpenSSH prerequisite directory.'
    }
    $Destination = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
    $parent = [IO.Path]::GetDirectoryName($Destination)
    if (-not $parent -or -not [IO.Directory]::Exists($parent)) {
        throw 'Select an OpenSSH prerequisite directory with an existing parent.'
    }
    Assert-AgentSshOrdinaryPath $Destination
    if (Test-Path -LiteralPath $Destination) {
        return Get-AgentSshOpenSSHFiles $Destination
    }
    $release = Get-AgentSshOpenSSHRelease
    $staging = Join-Path $parent ('.openssh-prepare-' + [Guid]::NewGuid().ToString('N'))
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $security.SetOwner($sid)
    $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit, ObjectInherit', 'None', 'Allow'))
    [IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new($staging), $security)
    try {
        $archive = Join-Path $staging 'release.zip'
        Save-AgentSshOpenSSHArchive $release.Uri $archive
        Expand-AgentSshOpenSSHArchive $archive $release $staging
        $expanded = Join-Path $staging $release.Directory
        $null = Get-AgentSshOpenSSHFiles $expanded
        [IO.Directory]::Move($expanded, $Destination)
        return Get-AgentSshOpenSSHFiles $Destination
    } finally {
        [IO.Directory]::Delete($staging, $true)
    }
}
