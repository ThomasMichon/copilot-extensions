<#
Explicit process-only qualification. Inherits the original caller's Job;
does not create containment, observe a desktop or acquire an interpreter.
#>
[CmdletBinding()]
param(
    [switch]$ExecuteAcceptedScope,
    [Parameter(Mandatory = $true)][int]$ExecutionScope,
    [Parameter(Mandatory = $true)][string]$ParentAcceptanceReference,
    [Parameter(Mandatory = $true)][string]$ExpectedPreparedManifestSha256,
    [Parameter(Mandatory = $true)][string]$OriginalRuntimeRoot,
    [Parameter(Mandatory = $true)][string]$OriginalStartMonotonic,
    [Parameter(Mandatory = $true)][string]$OriginalStartUtc
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ExecuteAcceptedScope -or $ExecutionScope -le 0 -or
    [string]::IsNullOrWhiteSpace($ParentAcceptanceReference)) {
    throw 'Process qualification requires its explicit accepted scope.'
}
$stage = Join-Path $PSScriptRoot 'native_process_stage.py'
& python -I -B -X utf8 $stage `
    --scope $ExecutionScope --acceptance $ParentAcceptanceReference `
    --manifest-sha256 $ExpectedPreparedManifestSha256 `
    --runtime-root $OriginalRuntimeRoot `
    --original-start-monotonic $OriginalStartMonotonic `
    --original-start-utc $OriginalStartUtc
exit $LASTEXITCODE
