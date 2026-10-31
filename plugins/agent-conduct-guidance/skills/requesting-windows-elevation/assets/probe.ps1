param([switch]$Fail)
[Console]::Out.WriteLine('agent-conduct-elevation payload entered')
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
@{
    probe = 'agent-conduct-elevation'
    elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
} | ConvertTo-Json -Compress
[Console]::Error.WriteLine('agent-conduct-elevation stderr probe')
if ($Fail) { exit 7 }
exit 0
