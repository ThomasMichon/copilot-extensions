param(
    [ValidateSet('install','update','start','stop','status','uninstall','import-client',
                 'config-authorize','identity','describe-keys','ssh-registry','describe-provider')]
    [string]$Action = 'install',
    [string]$Root = (Join-Path $HOME '.gateway-ssh-mesh'),
    [string]$Config = (Join-Path $PSScriptRoot 'windows.yaml'),
    [string]$Python,
    [switch]$RemoveConfig,
    [switch]$AcceptConfig,
    [switch]$RepairGeneratedKeyAcls
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -or
    $identity.IsSystem -or [Diagnostics.Process]::GetCurrentProcess().SessionId -eq 0) {
    throw 'Requires a non-elevated interactive user, not SYSTEM or session 0.'
}
if (-not $Python) {
    $candidate = Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
    if (-not $candidate) { throw 'Existing Python 3.9+ required; pass -Python. No toolchains are installed.' }
    $Python = $candidate.Source
}
$Python = (Resolve-Path -LiteralPath $Python).ProviderPath
$entrypoint = Join-Path $PSScriptRoot 'windows.py'
if (-not (Test-Path -LiteralPath $entrypoint)) {
    $version = (Get-Content -LiteralPath (Join-Path $Root 'active') -Raw).Trim()
    if ($version -notmatch '^[a-f0-9]{64}$') { throw 'Invalid active marker' }
    $entrypoint = Join-Path $Root ("versions\" + $version + "\windows.py")
}
$arguments = @($entrypoint, '--root', $Root, $Action,
               '--config', $Config)
if ($RemoveConfig) { $arguments += '--remove-config' }
if ($AcceptConfig) { $arguments += '--accept-config' }
if ($RepairGeneratedKeyAcls) { $arguments += '--repair-generated-key-acls' }
function Quote-NativeArgument([string]$Value) {
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = $Python
$info.Arguments = (($arguments | ForEach-Object { Quote-NativeArgument $_ }) -join ' ')
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$info.RedirectStandardInput = $true
$process = [Diagnostics.Process]::new()
$process.StartInfo = $info
$started = $false
try {
    if (-not $process.Start()) { throw 'Could not start bounded lifecycle command.' }
    $started = $true
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if ($Action -in @('import-client', 'config-authorize')) {
        $payload = if ($MyInvocation.ExpectingInput) { ($input | Out-String) } else { [Console]::In.ReadToEnd() }
        if ($payload.Length -gt 32768) { throw 'Enrollment/authorization JSON exceeds size bound.' }
        $process.StandardInput.Write($payload)
        $payload = $null
    }
    $process.StandardInput.Close()
    if (-not $process.WaitForExit(600000)) {
        $process.Kill()
        throw 'Bounded lifecycle command timed out.'
    }
    [Console]::Out.Write($stdout.GetAwaiter().GetResult())
    [Console]::Error.Write($stderr.GetAwaiter().GetResult())
    $exitCode = $process.ExitCode
} finally {
    if ($started -and -not $process.HasExited) { $process.Kill() }
    $process.Dispose()
}
exit $exitCode
