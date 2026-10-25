function Invoke-Hidden {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgList = @(),
        [ref]$ErrorOutput,
        [string]$InputText
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    if ($psi.PSObject.Properties['ArgumentList']) {
        foreach ($a in $ArgList) { [void]$psi.ArgumentList.Add($a) }
    } else {
        # Windows PowerShell 5.1 uses .NET Framework's quoted command line.
        $psi.Arguments = ($ArgList | ForEach-Object {
            if ($_ -and $_ -notmatch '[\s"]') {
                $_
            } else {
                '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
            }
        }) -join ' '
    }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $PSBoundParameters.ContainsKey('InputText')
    $proc = [System.Diagnostics.Process]::Start($psi)
    try {
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $stderr = $proc.StandardError.ReadToEndAsync()
        if ($psi.RedirectStandardInput) {
            $encoding = New-Object System.Text.UTF8Encoding($false)
            $bytes = $encoding.GetBytes($InputText)
            $proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
            $proc.StandardInput.BaseStream.Flush()
            $proc.StandardInput.Close()
        }
        $proc.WaitForExit()
        [Threading.Tasks.Task]::WaitAll($stdout, $stderr)
        $global:LASTEXITCODE = $proc.ExitCode
        if ($ErrorOutput) {
            $ErrorOutput.Value = $stderr.Result
        } elseif ($stderr.Result) {
            if ($proc.ExitCode -ne 0) { [Console]::Error.Write($stderr.Result) }
            else { Write-Verbose $stderr.Result }
        }
        return $stdout.Result
    } finally {
        $proc.Dispose()
    }
}
