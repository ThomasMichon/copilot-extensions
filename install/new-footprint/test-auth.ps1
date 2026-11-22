$ErrorActionPreference = 'Stop'
$bootstrap = Join-Path $PSScriptRoot 'bootstrap.ps1'
$caller = Join-Path $PSScriptRoot 'invoke-ssh.ps1'
$env:SSH_CONNECTION = 'test'
Remove-Item Env:GH_TOKEN, Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
$global:calls = [Collections.Generic.List[string]]::new()
function gh {
    if ($args[0] -ne 'api') { throw 'Unexpected interactive login' }
    $global:calls.Add("gh:$($args[3])")
    $global:LASTEXITCODE = 0
}
function git {
    if ($args -notcontains 'ls-remote') { throw 'Unexpected Git mutation' }
    $global:calls.Add("git:$($args[-2])")
    $global:LASTEXITCODE = 0
}
function uv { throw 'fixture: ready to install' }
try {
    & $bootstrap -Repo 'example/harness'
    throw 'Missing credential was accepted'
} catch {
    if ($_.Exception.Message -notlike 'Unattended repository onboarding requires*') { throw }
}
$env:GH_TOKEN = 'fixture-value'
$prior = $env:GIT_CONFIG_PARAMETERS
try {
    & $bootstrap -Repo 'example/harness' -KnowledgeRepo 'example/knowledge'
    throw 'Missing stage boundary'
} catch {
    if ($_.Exception.Message -ne 'fixture: ready to install') { throw }
}
$expected = @('gh:repos/example/harness', 'git:https://github.com/example/harness.git',
              'gh:repos/example/knowledge', 'git:https://github.com/example/knowledge.git')
if (($global:calls -join ';') -cne ($expected -join ';') -or
    [string]$env:GIT_CONFIG_PARAMETERS -cne [string]$prior) { throw 'Incorrect auth preflight' }
function ssh {
    $wire = @($input)
    if ($wire.Count -ne 1 -or $wire[0] -cne 'fixture-value' -or
        ($args -join ' ') -match 'fixture-value' -or $args -notcontains 'StrictHostKeyChecking=yes') {
        throw 'Credential not confined to verified SSH stdin'
    }
    $global:LASTEXITCODE = 0
}
& $caller -Target fixture -TokenProvider { 'fixture-value' } -BootstrapRevision ('a' * 40) `
    -Repo example/harness -KnowledgeRepo example/knowledge -SrcRoot example -Root example -Stage
