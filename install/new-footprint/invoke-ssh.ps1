[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9._-]*$')][string]$Target,
    [Parameter(Mandatory)][scriptblock]$TokenProvider,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{40}$')][string]$BootstrapRevision,
    [Parameter(Mandatory)][string]$Repo,
    [Parameter(Mandatory)][string]$KnowledgeRepo,
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$SrcRoot,
    [switch]$Stage
)
$ErrorActionPreference = 'Stop'
$options = @{Repo=$Repo; KnowledgeRepo=$KnowledgeRepo; Root=$Root; SrcRoot=$SrcRoot;
             BootstrapRevision=$BootstrapRevision; Stage=[bool]$Stage}
$payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($options | ConvertTo-Json -Compress)))
$remote = @'
$ErrorActionPreference = 'Stop'
$settings = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__OPTIONS__')))
$arguments = @{}
foreach ($property in $settings.PSObject.Properties) { $arguments[$property.Name] = $property.Value }
$url = "https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/$($settings.BootstrapRevision)/install/new-footprint/bootstrap.ps1"
$entry = [scriptblock]::Create((Invoke-RestMethod $url))
try {
    $env:GH_TOKEN = [Console]::In.ReadLine()
    if ([string]::IsNullOrWhiteSpace($env:GH_TOKEN)) { throw 'Credential provider supplied no token' }
    & $entry @arguments
} finally {
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
}
'@
$command = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remote.Replace('__OPTIONS__', $payload)))
# Only nonsecret settings are in argv. Obtain the credential from the caller's
# provider, then send it on stdin of the host-key-verified, non-PTY connection.
$credential = @(& $TokenProvider)
try {
    if ($credential.Count -ne 1 -or $credential[0] -isnot [string] -or
        $credential[0] -notmatch '^[!-~]{1,4096}$') { throw 'Invalid provider response' }
    $credential[0] | & ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no `
        -o ConnectTimeout=8 $Target "powershell -NoLogo -NoProfile -NonInteractive -EncodedCommand $command"
    if ($LASTEXITCODE) { throw "Remote bootstrap exited $LASTEXITCODE" }
} finally {
    $credential = $null
}
