param(
    [Parameter(Position=0)]
    [ValidateSet('install','uninstall','start','stop','status','update-config','update')]
    [string]$Action = 'status',
    [switch]$RemoveConfig,
    [switch]$Force
)

# Installer skeleton for a deployed service (reference example). See the
# service-lifecycle SKILL.md for the full lifecycle contract (install/uninstall/
# start/stop/status/update-config/update) and the drift-confirmation rules.

switch ($Action) {
    'install'       { Install-Service }
    'uninstall'     { Uninstall-Service -RemoveConfig:$RemoveConfig }
    'start'         { Start-Service }
    'stop'          { Stop-Service }
    'status'        { Get-ServiceStatus }
    'update-config' { Update-ServiceConfig -Force:$Force }
    'update'        { Update-Service -Force:$Force }
}
