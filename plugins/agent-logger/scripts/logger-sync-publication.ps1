function Publish-SyncTaskFile {
    [CmdletBinding(DefaultParameterSetName='Source')]
    param(
        [Parameter(Mandatory, ParameterSetName='Source')][string]$Source,
        [Parameter(Mandatory, ParameterSetName='Content')][string]$Content,
        [Parameter(Mandatory)][string]$Destination
    )
    $invalid = [IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint
    if ($PSCmdlet.ParameterSetName -eq 'Source' -and
        ([IO.File]::GetAttributes($Source) -band $invalid) -ne 0) {
        throw "Logger sync source must be an ordinary file: $Source"
    }
    if (Test-Path -LiteralPath $Destination) {
        if (([IO.File]::GetAttributes($Destination) -band $invalid) -ne 0) {
            throw "Logger sync destination must be an ordinary file: $Destination"
        }
        if ($PSCmdlet.ParameterSetName -eq 'Content' -and
            [IO.File]::ReadAllText($Destination) -ceq $Content) { return }
        if ($PSCmdlet.ParameterSetName -eq 'Source' -and
            [Convert]::ToBase64String([IO.File]::ReadAllBytes($Destination)) -ceq
                [Convert]::ToBase64String([IO.File]::ReadAllBytes($Source))) { return }
    }
    $temporary = Join-Path (Split-Path -Parent $Destination) (
        '.' + (Split-Path -Leaf $Destination) + '.' + [Guid]::NewGuid().ToString('N'))
    $created = $false
    try {
        if ($PSCmdlet.ParameterSetName -eq 'Source') {
            [IO.File]::Copy($Source, $temporary, $false)
            $created = $true
        } else {
            $stream = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write, [IO.FileShare]::None)
            $created = $true
            try {
                $bytes = [Text.UTF8Encoding]::new($true).GetPreamble() +
                    [Text.UTF8Encoding]::new($true).GetBytes($Content)
                $stream.Write($bytes, 0, $bytes.Length)
            } finally { $stream.Dispose() }
        }
        if (Test-Path -LiteralPath $Destination) {
            if (([IO.File]::GetAttributes($Destination) -band $invalid) -ne 0) {
                throw "Logger sync destination must be an ordinary file: $Destination"
            }
            [IO.File]::Replace($temporary, $Destination, [NullString]::Value)
        } else {
            [IO.File]::Move($temporary, $Destination)
        }
    } finally {
        if ($created -and [IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}
