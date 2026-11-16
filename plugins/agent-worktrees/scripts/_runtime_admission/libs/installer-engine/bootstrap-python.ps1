function Resolve-EvaluatorBootstrapPython {
    param(
        [string[]]$ExcludedRoots = @(),
        [string]$Python = $env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON
    )
    if ($Python -or $PSBoundParameters.ContainsKey('Python')) {
        $root = if ([string]::IsNullOrWhiteSpace($Python)) { '' } else { [IO.Path]::GetPathRoot($Python) }
        if (-not $root -or -not [IO.Path]::IsPathRooted($Python) -or
            ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
                ($root -in @('\', '/') -or $root -match '^[A-Za-z]:$'))) {
            throw 'Bootstrap Python must be an absolute executable path.'
        }
        return [pscustomobject]@{
            Status = 'selected'; Reason = 'explicit-python'; Python = $Python; Guard = $null
        }
    }
    try {
        $command = @(Get-Command -Name python -CommandType Application -ErrorAction Stop)[0]
    } catch [System.Management.Automation.CommandNotFoundException] {
        return [pscustomobject]@{
            Status = 'unavailable'; Reason = 'python-not-on-path'; Python = $null; Guard = $null
        }
    }
    return [pscustomobject]@{
        Status = 'selected'; Reason = 'path-python'; Python = $command.Path; Guard = $null
    }
}
