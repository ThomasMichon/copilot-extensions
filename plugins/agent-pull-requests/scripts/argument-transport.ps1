function Remove-PullRequestsArgumentFile {
    param([string]$Path)
    if (-not $Path) { return }
    $directory = [IO.Path]::GetDirectoryName($Path)
    if ([IO.Path]::GetFileName($Path) -cne 'arguments.json' -or
        -not [IO.Path]::GetFileName($directory).StartsWith('ce-pull-requests-arguments-')) {
        throw 'Argument transport cleanup requires its owned temporary path.'
    }
    if ([IO.File]::Exists($Path)) { [IO.File]::Delete($Path) }
    if ([IO.Directory]::Exists($directory)) { [IO.Directory]::Delete($directory) }
}

function New-PullRequestsArgumentFile {
    param([string]$Json)
    $directory = Join-Path ([IO.Path]::GetTempPath()) (
        'ce-pull-requests-arguments-' + [Guid]::NewGuid().ToString('N')
    )
    $path = $null
    $stream = $null
    $fileCreated = $false
    $directoryCreated = $false
    try {
        if ([IO.Directory]::Exists($directory) -or [IO.File]::Exists($directory)) {
            throw 'Argument transport temporary path already exists.'
        }
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            $payload = Split-Path -Parent $PSScriptRoot
            $helper = Join-Path $payload 'libs/agent-procutil/src/agent_procutil/private_paths.ps1'
            if (-not [IO.File]::Exists($helper)) {
                $helper = Join-Path $payload '../../libs/agent-procutil/src/agent_procutil/private_paths.ps1'
            }
            . $helper
            $security = New-CEPrivateSecurity -Directory
            if ($PSVersionTable.PSEdition -eq 'Core') {
                [void][IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new($directory), $security)
            } else {
                [void][IO.Directory]::CreateDirectory($directory, $security)
            }
            $directoryCreated = $true
            Test-CEPrivatePath $directory
        } else {
            $modeType = 'System.IO.UnixFileMode' -as [type]
            $nativeMode = @([IO.Directory].GetMethods() | Where-Object {
                $_.Name -eq 'CreateDirectory' -and $_.GetParameters().Count -eq 2 -and
                $_.GetParameters()[1].ParameterType -eq $modeType
            })
            if ($modeType -and $nativeMode.Count) {
                $mode = [Enum]::Parse($modeType, 'UserRead,UserWrite,UserExecute')
                [void][IO.Directory]::CreateDirectory($directory, $mode)
            } else {
                $mktemp = (Get-Command mktemp -CommandType Application -ErrorAction Stop |
                    Select-Object -First 1).Source
                $temporaryRoot = [IO.Path]::GetTempPath().TrimEnd('/')
                $selected = @(& $mktemp -d (Join-Path $temporaryRoot 'ce-pull-requests-arguments-XXXXXXXXXX'))
                if ($LASTEXITCODE -ne 0 -or $selected.Count -ne 1 -or
                    -not [IO.Path]::IsPathRooted([string]$selected[0]) -or
                    [IO.Path]::GetDirectoryName([string]$selected[0]) -cne $temporaryRoot -or
                    -not [IO.Path]::GetFileName([string]$selected[0]).StartsWith('ce-pull-requests-arguments-')) {
                    throw 'Private argument transport requires an admitted temporary directory.'
                }
                $directory = [string]$selected[0]
            }
            $directoryCreated = $true
        }
        $path = Join-Path $directory 'arguments.json'
        $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write, [IO.FileShare]::Read)
        $fileCreated = $true
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Json)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Dispose()
        $stream = $null
        return $path
    } catch {
        $failure = $_
        try {
            if ($stream) { $stream.Dispose() }
            if ($fileCreated -and [IO.File]::Exists($path)) { [IO.File]::Delete($path) }
            if ($directoryCreated) { [IO.Directory]::Delete($directory) }
        } catch {
            $failure.Exception.Data['ArgumentTransportCleanupPath'] = $directory
        }
        throw $failure
    }
}
