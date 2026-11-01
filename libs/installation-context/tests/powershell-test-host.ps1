[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ScriptPath,
    [int]$TimeoutSeconds = 30
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$OutputEncoding = $utf8NoBom
try {
    [Console]::InputEncoding = $utf8NoBom
    [Console]::OutputEncoding = $utf8NoBom
} catch {}
$protocolOutput = [Console]::Out

function Write-ProtocolResponse([hashtable]$Response) {
    $protocolOutput.WriteLine(($Response | ConvertTo-Json -Depth 8 -Compress))
    $protocolOutput.Flush()
}

while ($null -ne ($line = [Console]::In.ReadLine())) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        try {
            $request = $line | ConvertFrom-Json
            if ($request.shutdown -eq $true) {
                break
            }
            $requestId = [int]$request.id
            $arguments = @($request.arguments | ForEach-Object { [string]$_ })
            if ($null -eq $request.PSObject.Properties['arguments']) {
                throw 'A request must declare its arguments.'
            }
        }
        catch {
            Write-ProtocolResponse @{
                id = -1
                returncode = 1
                stdout = ''
                stderr = "powershell-test-host: $($_.Exception.Message)"
            }
            continue
        }

        $environment = @{}
        if ($null -ne $request.environment) {
            foreach ($property in $request.environment.PSObject.Properties) {
                $environment[$property.Name] = [string]$property.Value
            }
        }
        $environmentNames = @(
            @('COPILOT_EXTENSIONS_CONTEXT', 'COPILOT_PLUGIN_ROOT') +
            @($environment.Keys)
        ) | Select-Object -Unique
        $originalEnvironment = @{}
        foreach ($name in $environmentNames) {
            $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable(
                $name,
                [EnvironmentVariableTarget]::Process
            )
            [Environment]::SetEnvironmentVariable(
                $name,
                $null,
                [EnvironmentVariableTarget]::Process
            )
        }
        foreach ($entry in $environment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable(
                $entry.Key,
                $entry.Value,
                [EnvironmentVariableTarget]::Process
            )
        }

        $runner = [PowerShell]::Create()
        $errorWriter = New-Object IO.StringWriter
        $outputWriter = New-Object IO.StringWriter
        $originalError = [Console]::Error
        $originalOutput = [Console]::Out
        try {
            $builder = $runner.AddCommand($ScriptPath)
            if ($arguments.Count -gt 0) {
                $builder = $builder.AddArgument($arguments[0])
            }
            for ($index = 1; $index -lt $arguments.Count;) {
                $name = $arguments[$index].TrimStart('-')
                if ($index + 1 -ge $arguments.Count) {
                    [void]$builder.AddParameter($name)
                    $index += 1
                    continue
                }
                [void]$builder.AddParameter($name, $arguments[$index + 1])
                $index += 2
            }

            [Console]::SetError($errorWriter)
            [Console]::SetOut($outputWriter)
            $asyncResult = $runner.BeginInvoke()
            if (-not $asyncResult.AsyncWaitHandle.WaitOne($TimeoutSeconds * 1000)) {
                $runner.Stop()
                Write-ProtocolResponse @{
                    id = $requestId
                    returncode = 124
                    stdout = ''
                    stderr = "PowerShell invocation exceeded ${TimeoutSeconds}s."
                }
                continue
            }
            $output = @($runner.EndInvoke($asyncResult) | ForEach-Object { [string]$_ })
            $returnCode = $runner.Runspace.SessionStateProxy.GetVariable('LASTEXITCODE')
            if ($null -eq $returnCode) {
                $returnCode = $(if ($runner.HadErrors) { 1 } else { 0 })
            }
            Write-ProtocolResponse @{
                id = $requestId
                returncode = [int]$returnCode
                stdout = $outputWriter.ToString() + ($output -join [Environment]::NewLine)
                stderr = $errorWriter.ToString()
            }
        }
        catch {
            $stderr = $errorWriter.ToString()
            if ($stderr) {
                $stderr += [Environment]::NewLine
            }
            $stderr += "powershell-test-host: $($_.Exception.Message)"
            Write-ProtocolResponse @{
                id = $requestId
                returncode = 1
                stdout = ''
                stderr = $stderr
            }
        }
        finally {
            [Console]::SetError($originalError)
            [Console]::SetOut($originalOutput)
            $errorWriter.Dispose()
            $outputWriter.Dispose()
            $runner.Dispose()
            foreach ($name in $environmentNames) {
                [Environment]::SetEnvironmentVariable(
                    $name,
                    $originalEnvironment[$name],
                    [EnvironmentVariableTarget]::Process
                )
            }
        }
}
