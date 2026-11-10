param([string]$Root, [string]$Plugin, [string]$Label)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command python -CommandType Application -ErrorAction SilentlyContinue)) {
    throw 'Cold native admission requires the setup-provided Python prerequisite.'
}
foreach ($name in @('python3', 'py', 'uv')) {
    if (Get-Command $name -CommandType Application -ErrorAction SilentlyContinue) {
        throw "Cold fixture unexpectedly discovered $name."
    }
}
. (Join-Path $Plugin 'src\update_driver\foundation.ps1')
$worker = Start-NativeFoundationProducer -Root $Root -Plugin $Plugin
$published = $false
try {
    $null = $worker.Handle
    $document = @{
        pid = $worker.Id
        created = $worker.StartTime.ToUniversalTime().ToFileTimeUtc()
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText((Join-Path $Root ("fixture-$Label.json")), $document)
    $published = $true
} finally {
    try {
        if (-not $published -and -not $worker.HasExited) {
            $worker.Kill()
            if (-not $worker.WaitForExit(15000)) { throw 'Unpublished fixture worker did not retire.' }
        }
    } finally {
        $worker.Dispose()
    }
}
