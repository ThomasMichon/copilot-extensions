param(
    [Parameter(Position = 0)][string]$SourceId,
    [Parameter(Position = 1)][string]$ContributorId,
    [Parameter(Position = 2)][string]$RelativeScript,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ContributorArgs
)

$utf8 = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

function Write-EmptyResult {
    [Console]::Out.Write('{}')
}

function Test-JsonObject {
    param([string]$Text)

    if (-not $Text) { return $false }
    $trimmed = $Text.Trim()
    if (-not $trimmed.StartsWith('{') -or -not $trimmed.EndsWith('}')) {
        return $false
    }
    try {
        $value = $trimmed | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return $false
    }
    return $null -ne $value -and $value -isnot [Array] -and $value -isnot [ValueType]
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)

    if ($null -eq $Value) { $Value = '' }
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }

    $builder = [Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes += 1
            continue
        }
        if ($character -eq '"') {
            [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
            continue
        }
        if ($backslashes) {
            [void]$builder.Append(('\' * $backslashes))
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes) {
        [void]$builder.Append(('\' * ($backslashes * 2)))
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-BufferedProcess {
    param(
        [string]$FileName,
        [string[]]$Arguments,
        [string]$InputText,
        [string]$WorkingDirectory
    )

    try {
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $FileName
        if ($start.PSObject.Properties.Name -contains 'ArgumentList') {
            foreach ($argument in $Arguments) {
                [void]$start.ArgumentList.Add([string]$argument)
            }
        }
        else {
            $start.Arguments = (($Arguments | ForEach-Object {
                ConvertTo-NativeArgument ([string]$_)
            }) -join ' ')
        }
        if ($WorkingDirectory) {
            $start.WorkingDirectory = $WorkingDirectory
        }
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $start.RedirectStandardInput = $true
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        if ($start.PSObject.Properties.Name -contains 'StandardOutputEncoding') {
            $start.StandardOutputEncoding = $utf8
            $start.StandardErrorEncoding = $utf8
        }

        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $start
        if (-not $process.Start()) {
            throw 'Process failed to start'
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $bytes = $utf8.GetBytes($InputText)
        try {
            $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
            $process.StandardInput.BaseStream.Flush()
        }
        catch [IO.IOException] { }
        try {
            $process.StandardInput.Close()
        }
        catch { }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($stderr) {
            [Console]::Error.Write($stderr)
        }
        return [pscustomobject]@{
            Success = $process.ExitCode -eq 0
            Output = $stdout
        }
    }
    catch {
        return [pscustomobject]@{
            Success = $false
            Output = ''
        }
    }
    finally {
        if ($process) { $process.Dispose() }
    }
}

$root = $env:COPILOT_PLUGIN_ROOT
if (-not $root) { $root = $env:PLUGIN_ROOT }
if (-not $root) { $root = $env:CLAUDE_PLUGIN_ROOT }
if (-not $root -or -not $SourceId -or -not $ContributorId -or -not $RelativeScript) {
    Write-EmptyResult
    exit 0
}

try {
    $resolvedRoot = [IO.Path]::GetFullPath($root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $script = [IO.Path]::GetFullPath((Join-Path $resolvedRoot $RelativeScript))
    $prefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (
        -not $script.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($script) -ne '.ps1'
    ) {
        throw 'Contributor script escapes its payload root'
    }
}
catch {
    Write-EmptyResult
    exit 0
}

$env:COPILOT_PLUGIN_ROOT = $resolvedRoot
try {
    $payload = [Console]::In.ReadToEnd()
    $hook = $payload | ConvertFrom-Json -ErrorAction Stop
    $rawCwd = [string]$hook.cwd
    if (-not $rawCwd -or -not [IO.Path]::IsPathRooted($rawCwd)) {
        throw 'Hook payload cwd is unavailable'
    }
    $launchCwd = (Resolve-Path -LiteralPath $rawCwd -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $launchCwd -PathType Container)) {
        throw 'Hook payload cwd is not a directory'
    }
}
catch {
    Write-EmptyResult
    exit 0
}

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }

$resolver = Join-Path (Join-Path $resolvedRoot 'scripts') 'resolve_context_authority.py'
$authority = ''
if ($python -and (Test-Path -LiteralPath $resolver -PathType Leaf)) {
    $resolved = Invoke-BufferedProcess `
        -FileName $python.Source `
        -Arguments @($resolver) `
        -InputText $payload `
        -WorkingDirectory $launchCwd
    if ($resolved.Success) {
        $authority = $resolved.Output.Trim()
    }
}

$engine = if ($authority) {
    Join-Path (Join-Path $authority 'scripts') 'aggregate_context.py'
}
else {
    ''
}
if ($engine -and (Test-Path -LiteralPath $engine -PathType Leaf)) {
    $result = Invoke-BufferedProcess `
        -FileName $python.Source `
        -Arguments @($engine, '--producer', "$SourceId/$ContributorId") `
        -InputText $payload `
        -WorkingDirectory $launchCwd
    if ($result.Success -and (Test-JsonObject $result.Output)) {
        [Console]::Out.Write($result.Output)
    }
    else {
        Write-EmptyResult
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
    Write-EmptyResult
    exit 0
}

$hostExecutable = $null
try {
    $processPathProperty = [Environment].GetProperty('ProcessPath')
    if ($processPathProperty) {
        $hostExecutable = $processPathProperty.GetValue($null, $null)
    }
}
catch { }
if (-not $hostExecutable) {
    try {
        $hostExecutable = (Get-Process -Id $PID -ErrorAction Stop).Path
    }
    catch { }
}
if (-not $hostExecutable) {
    Write-EmptyResult
    exit 0
}

$arguments = @('-NoProfile', '-File', $script)
if ($null -ne $ContributorArgs -and $ContributorArgs.Count -gt 0) {
    $arguments += @($ContributorArgs)
}
$result = Invoke-BufferedProcess `
    -FileName $hostExecutable `
    -Arguments $arguments `
    -InputText $payload `
    -WorkingDirectory $launchCwd
if ($result.Success -and (Test-JsonObject $result.Output)) {
    [Console]::Out.Write($result.Output)
}
else {
    Write-EmptyResult
}
exit 0
