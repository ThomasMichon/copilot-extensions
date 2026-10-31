function Resolve-EvaluatorBootstrapPython {
    # Retained for older payload callers; setup, not discovery, owns PATH hygiene.
    param([string[]]$ExcludedRoots = @())
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
