"""Pass-through ownership observation for the private canonical-path controls."""

from __future__ import annotations

OBSERVE = r"""
$script:newGuard=${function:New-BootstrapStockFileGuard}
$script:guards=[Collections.Generic.List[object]]::new()
$script:finds=[Collections.Generic.List[object]]::new()
$script:allocations=[Collections.Generic.List[IntPtr]]::new()
$script:frees=[Collections.Generic.List[IntPtr]]::new()
$script:liveMemory=[Collections.Generic.HashSet[IntPtr]]::new()
$script:newMemory=${function:New-BootstrapStockFindAllocation}
$script:freeMemory=${function:Close-BootstrapStockFindAllocation}
function New-BootstrapStockFindAllocation {
    if ($env:FAILURE -eq 'allocation') { throw 'synthetic allocation refusal' }
    $pointer=& $script:newMemory
    if (-not $script:liveMemory.Add($pointer)) { throw 'Long-name query allocated an already-live address.' }
    $script:allocations.Add($pointer)
    return $pointer
}
function Close-BootstrapStockFindAllocation {
    param([IntPtr]$Pointer)
    if (-not $script:liveMemory.Contains($Pointer)) {
        throw 'Long-name query freed unowned memory.'
    }
    & $script:freeMemory -Pointer $Pointer
    [void]$script:liveMemory.Remove($Pointer)
    $script:frees.Add($Pointer)
    if ($env:FAILURE -eq 'release') { throw 'synthetic refusal after actual memory release' }
}
function New-BootstrapStockFileGuard {
    param($Path,[switch]$Directory,$Bindings)
    if (-not $Path.StartsWith('\\?\GLOBALROOT\Device\HarddiskVolume',[StringComparison]::Ordinal)) {
        throw 'Canonical custody used a mutable DOS address.'
    }
    $entry=& $script:newGuard -Path $Path -Directory:$Directory -Bindings $Bindings
    $script:guards.Add($entry)
    return $entry
}
$script:bindingReader=${function:Get-BootstrapStockCanonicalBindings}
function Get-BootstrapStockCanonicalBindings {
    $bindings=& $script:bindingReader
    $script:find=$bindings.Find
    $proxy=[pscustomobject]@{}
    $proxy | Add-Member ScriptMethod Invoke {
        param($target,$arguments)
        if ($arguments.Count -ne 2 -or $arguments[0] -isnot [string] -or
            $script:guards.Count -lt 2 -or $script:guards[$script:guards.Count - 1].Handle.IsClosed -or
            $script:guards[$script:guards.Count - 1].Path -cne $arguments[0]) {
            throw 'Long-name query did not follow its exact retained physical open.'
        }
        $handle=$script:find.Invoke($target,$arguments)
        $script:finds.Add($handle)
        return $handle
    }
    $bindings.Find=$proxy
    return $bindings
}
"""
