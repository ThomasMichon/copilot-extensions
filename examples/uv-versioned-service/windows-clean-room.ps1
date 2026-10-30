param(
    [string]$Context = 'desktop-windows',
    [string]$Image = 'python:3.12-windowsservercore-ltsc2022',
    [string]$ToolchainDirectory,
    [string]$UvExecutable,
    [switch]$EnvironmentOnly,
    [switch]$TwoPass,
    [Parameter(Mandatory)][string]$SourceCommit
)
$ErrorActionPreference = 'Stop'
if ($TwoPass -and -not $EnvironmentOnly) { throw 'TwoPass is environment-only qualification' }
$dockerExecutable = (Get-Command docker.exe -CommandType Application).Source
$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$run = Join-Path $here ('.runs\windows-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$name = 'uv-slot-example-' + [guid]::NewGuid().ToString('N')
$report = @{
    tier = 'P'; status = 'running'; container = $null; removed = $false
    source_commit = $SourceCommit; image = $Image; context = $Context
    environment_passes = $(if ($TwoPass) { 2 } else { 1 })
    source_transport = 'shallow Git checkout preserving the selected source commit'
    container_name = $name
    image_cache = 'not observed'; runtime_cache = 'empty'
}
function Invoke-ExampleDocker {
    param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    $result = & $dockerExecutable --context $Context @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw ($result -join "`n") }
    return $result
}
try {
    $os = Invoke-ExampleDocker info --format '{{.OSType}}'
    if ($os -ne 'windows') { throw "Required Windows Docker daemon; observed $os" }
    $null = Invoke-ExampleDocker image inspect $Image
    $report.image_cache = 'warm; explicitly selected stock toolchain image already present'
    if ($ToolchainDirectory) {
        if (-not (Test-Path (Join-Path $ToolchainDirectory 'uv.exe')) -or
            -not (Test-Path (Join-Path $ToolchainDirectory 'Git\bin\git.exe'))) {
            throw 'ToolchainDirectory must contain uv.exe and an unconfigured Git\bin\git.exe tree'
        }
    }
    if ($UvExecutable -and -not (Test-Path -LiteralPath $UvExecutable -PathType Leaf)) {
        throw 'UvExecutable must name an existing prerequisite uv.exe binary'
    }
    $selected = & git -C $repo rev-parse --verify "${SourceCommit}^{commit}"
    if ($LASTEXITCODE -ne 0) { throw 'source commit is unavailable' }
    $SourceCommit = "$selected".Trim()
    $report.source_commit = $SourceCommit
    $sourceGit = Join-Path $run 'source-git'
    $sourceUri = ([Uri]($repo + '\')).AbsoluteUri
    & git init --template= --quiet $sourceGit
    if ($LASTEXITCODE -ne 0) { throw 'source Git initialization failed' }
    & git -C $sourceGit fetch --quiet --depth 1 --no-tags --no-write-fetch-head $sourceUri $SourceCommit
    if ($LASTEXITCODE -ne 0) { throw 'selected source Git fetch failed' }
    & git -C $sourceGit update-ref refs/heads/candidate $SourceCommit
    if ($LASTEXITCODE -ne 0) { throw 'source candidate ref publication failed' }
    & git -C $sourceGit symbolic-ref HEAD refs/heads/candidate
    if ($LASTEXITCODE -ne 0) { throw 'source candidate HEAD selection failed' }
    $id = Invoke-ExampleDocker create --name $name --network nat $Image powershell.exe -NoProfile `
        -NonInteractive -Command 'Start-Sleep -Seconds 1800'
    $report.container = "$id"
    Write-Output "OWNED_CONTAINER $id"
    $report | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $run 'windows-results.json') -Encoding utf8
    $null = Invoke-ExampleDocker cp $sourceGit "${id}:C:\fixture"
    if ($ToolchainDirectory) {
        $null = Invoke-ExampleDocker cp $ToolchainDirectory "${id}:C:\toolchain"
    }
    if ($UvExecutable) {
        $null = Invoke-ExampleDocker cp $UvExecutable "${id}:C:\uv.exe"
    }
    $body = @'
$ErrorActionPreference = 'Stop'
$env:PATH = 'C:\;C:\toolchain;C:\toolchain\Git\bin;' + $env:PATH
$env:GIT_CONFIG_NOSYSTEM = '1'
$env:GIT_CONFIG_GLOBAL = 'NUL'
Get-Command python,git,uv -ErrorAction Stop | Select-Object Name,Source
python --version
uv --version
git -C C:\fixture -c core.longpaths=true checkout --quiet --detach SELECTED_COMMIT
if ($LASTEXITCODE -ne 0) { throw 'selected source checkout failed' }
$commit = git -C C:\fixture rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $commit -ne 'SELECTED_COMMIT') { throw 'selected source identity mismatch' }
New-Item -ItemType Directory C:\evidence -Force | Out-Null
git -C C:\fixture rev-parse HEAD 'HEAD^{tree}' |
    Set-Content C:\evidence\source-git.txt -Encoding utf8
python C:\fixture\examples\uv-versioned-service\WITNESS.py --source-commit $commit WITNESS_FLAGS
$code = $LASTEXITCODE
foreach ($validation in (Get-ChildItem C:\fixture\examples\uv-versioned-service\.runs -Directory | Where-Object { $_.Name -match '^(validation|environments)-' })) {
    $target = Join-Path C:\evidence $validation.Name
    New-Item -ItemType Directory $target | Out-Null
    Get-ChildItem $validation.FullName -File | Copy-Item -Destination $target
    foreach ($phase in @('cold','warm','retry','main-style','actual-home')) {
        $source = Join-Path $validation.FullName $phase
        if (Test-Path $source) { Copy-Item $source -Destination $target -Recurse }
    }
}
if ($code -ne 0) { throw 'deterministic whole-flow witness failed' }
'@
    $witness = if ($EnvironmentOnly) { 'validate_environments' } else { 'validate' }
    $body = $body.Replace('WITNESS.py', "$witness.py").Replace('SELECTED_COMMIT', $SourceCommit)
    $body = $body.Replace('WITNESS_FLAGS', $(if ($TwoPass) { '--two-pass' } else { '' }))
    $body | Set-Content -LiteralPath (Join-Path $run 'inside.ps1') -Encoding utf8
    $null = Invoke-ExampleDocker cp (Join-Path $run 'inside.ps1') "${id}:C:\inside.ps1"
    $null = Invoke-ExampleDocker start "$id"
    Invoke-ExampleDocker exec "$id" powershell.exe -NoProfile -NonInteractive -File C:\inside.ps1 |
        Set-Content -LiteralPath (Join-Path $run 'container.log') -Encoding utf8
    $null = Invoke-ExampleDocker stop --time 10 "$id"
    $null = Invoke-ExampleDocker cp "${id}:C:\evidence" (Join-Path $run 'results')
    foreach ($resultFile in (Get-ChildItem (Join-Path $run 'results') -Recurse -Filter results.json)) {
        $result = Get-Content $resultFile.FullName -Raw | ConvertFrom-Json
        if ($result.source_commit -ne $SourceCommit) { throw 'result source identity mismatch' }
    }
    $report.status = 'passed'
} catch {
    $report.status = 'blocked-or-failed'
    $report.error = "$_"
    throw
} finally {
    if ($report.container) {
        if ($report.status -ne 'passed') {
            try {
                $null = Invoke-ExampleDocker stop --time 10 "$($report.container)"
                $null = Invoke-ExampleDocker cp "$($report.container):C:\evidence" (Join-Path $run 'results')
            } catch { $report.capture_error = "$_" }
        }
        try {
            $null = Invoke-ExampleDocker rm --force "$($report.container)"
            $report.removed = $true
        } catch { $report.cleanup_error = "$_" }
    }
    $report | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath (Join-Path $run 'windows-results.json') -Encoding utf8
    Write-Output "RESULTS $(Join-Path $run 'windows-results.json')"
}
