[CmdletBinding()]
param(
    [string]$Repo = $env:HARNESS_REPO,
    [string]$KnowledgeRepo = $env:KNOWLEDGE_REPO,
    [string]$SrcRoot = (Join-Path $HOME 'src'),
    [string]$Root = (Join-Path $HOME '.copilot-extensions'),
    [ValidatePattern('^(main|[0-9a-f]{40})$')][string]$BootstrapRevision = 'main',
    [switch]$Stage,
    [switch]$ClearTrial
)
$ErrorActionPreference = 'Stop'
$argsForDriver = @('--root', $Root, '--srcroot', $SrcRoot)
if ($Repo) { $argsForDriver += @('--repo', $Repo) }
if ($KnowledgeRepo) { $argsForDriver += @('--knowledge-repo', $KnowledgeRepo) }
if ($Stage) { $argsForDriver += '--stage' }
if ($ClearTrial) { $argsForDriver += '--clear-trial' }
$temporary = $null
try {
    if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'driver.py'))) {
        $driver = Join-Path $PSScriptRoot 'driver.py'
    } else {
        $temporary = Join-Path ([IO.Path]::GetTempPath()) ('ce-bootstrap-' + [guid]::NewGuid().ToString('N'))
        & git -c core.hooksPath=NUL init --quiet $temporary
        if ($LASTEXITCODE) { throw 'Could not create bootstrap workspace' }
        & git -C $temporary -c core.hooksPath=NUL fetch --quiet --depth 1 --no-tags https://github.com/ThomasMichon/copilot-extensions.git $BootstrapRevision
        if ($LASTEXITCODE) { throw 'Could not download bootstrap tools' }
        & git -C $temporary -c core.hooksPath=NUL checkout --quiet --detach FETCH_HEAD
        if ($LASTEXITCODE) { throw 'Could not materialize bootstrap tools' }
        $driver = Join-Path $temporary 'install\new-footprint\driver.py'
    }
    & uv run --no-project --python 3.14 --managed-python python -I -B $driver @argsForDriver
    if ($LASTEXITCODE) { throw "New-footprint bootstrap exited $LASTEXITCODE" }
} finally {
    if ($temporary -and (Test-Path -LiteralPath $temporary)) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
