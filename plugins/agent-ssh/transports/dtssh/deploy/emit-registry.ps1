#Requires -Version 7.0
<#!
.SYNOPSIS
    Emit an agent-ssh normalized dtssh registry from machines.yaml + live dtssh state.
#>
param(
    [string]$MachinesYaml = (Join-Path (Get-Location) 'machines.yaml'),
    [string]$OutFile,
    [string]$DtsshBin,
    [switch]$SkipDiscover,
    [switch]$KeepInline,
    [switch]$AllowStaticFallback,
    [switch]$NoProxyBinaryPath
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'emit-registry.py'
$argsList = @('--machines', $MachinesYaml)
if ($OutFile) { $argsList += @('--out', $OutFile) }
if ($DtsshBin) { $argsList += @('--dtssh-bin', $DtsshBin) }
if ($SkipDiscover) { $argsList += '--skip-discover' }
if ($KeepInline) { $argsList += '--keep-inline' }
if ($AllowStaticFallback) { $argsList += '--allow-static-fallback' }
if ($NoProxyBinaryPath) { $argsList += '--no-proxy-binary-path' }
python $script @argsList
exit $LASTEXITCODE
