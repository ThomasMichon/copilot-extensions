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
$savedAuthEnvironment = @{}
try {
    if (-not $ClearTrial -and ($Repo -or $KnowledgeRepo)) {
        $headless = $env:SSH_CONNECTION -or $env:SSH_CLIENT -or $env:SSH_TTY -or
            $env:COPILOT_AGENT_SESSION_ID -or [Console]::IsInputRedirected -or
            [Console]::IsOutputRedirected -or -not [Environment]::UserInteractive
        $provided = $env:GH_TOKEN -or $env:GITHUB_TOKEN
        if ($headless -and -not $provided) {
            throw 'Unattended repository onboarding requires a caller-supplied GitHub credential before bootstrap.'
        }
        # Scope the helper to GitHub HTTPS for this invocation; never write a global helper.
        $settings = @{
            GIT_CONFIG_PARAMETERS = ($env:GIT_CONFIG_PARAMETERS + " 'credential.https://github.com.helper=' 'credential.https://github.com.helper=!gh auth git-credential'").Trim()
            GIT_TERMINAL_PROMPT = '0'
        }
        foreach ($key in $settings.Keys) {
            $savedAuthEnvironment[$key] = [Environment]::GetEnvironmentVariable($key)
            [Environment]::SetEnvironmentVariable($key, $settings[$key])
        }
        function Test-SelectedRepositoryAccess {
            $ErrorActionPreference = 'Continue'
            foreach ($value in @($Repo, $KnowledgeRepo)) {
                if (-not $value -or (Test-Path -LiteralPath $value -PathType Container)) { continue }
                $slug = ($value -creplace '^https://github\.com/', '') -creplace '\.git$', ''
                if ($slug -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$') {
                    throw 'Invalid GitHub repository locator.'
                }
                & gh api --hostname github.com "repos/$slug" --silent 2>$null
                if ($LASTEXITCODE) { return $false }
                & git -c credential.interactive=never ls-remote --exit-code "https://github.com/$slug.git" HEAD 2>$null | Out-Null
                if ($LASTEXITCODE) { return $false }
            }
            return $true
        }
        if (-not (Test-SelectedRepositoryAccess)) {
            if ($headless -or $provided) {
                throw 'Supplied GitHub identity could not verify API and Git read access to the selected repositories.'
            }
            & gh auth login --hostname github.com --web --git-protocol https
            if ($LASTEXITCODE -or -not (Test-SelectedRepositoryAccess)) {
                throw 'GitHub authorization incomplete: verify access and organization SSO for the selected repositories.'
            }
        }
    }
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
    foreach ($key in $savedAuthEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $savedAuthEnvironment[$key])
    }
    if ($temporary -and (Test-Path -LiteralPath $temporary)) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}
