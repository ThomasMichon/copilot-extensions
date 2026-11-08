# Installation-local Windows scheduling controls. No runtime imports or builds.
function Initialize-IndexSchedulingPolicy {
    $script:IndexTaskPolicy = 'preserve'
    $script:IndexStartService = $true
    $script:IndexStartEngine = $true
    $path = if ($env:AGENT_INDEX_INSTALLATION_CONFIG) { $env:AGENT_INDEX_INSTALLATION_CONFIG }
        else { Join-Path $InstallDir 'installation.json' }
    if (Test-Path -LiteralPath $path) {
        $document = Get-Content -LiteralPath $path -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($document.version -ne 1 -or -not [IO.Path]::IsPathRooted([string]$document.installation.root) -or
            [IO.Path]::GetFullPath([string]$document.installation.root).TrimEnd('\') -ine $InstallDir.TrimEnd('\')) {
            throw 'Index installation policy must name version 1 and this exact installation root'
        }
        $local = $document.installation.local
        foreach ($property in @($local.PSObject.Properties)) {
            switch -CaseSensitive ($property.Name) {
                'windows_scheduled_task' {
                    if ($property.Value -isnot [string] -or $property.Value -cnotin @('preserve', 'enabled', 'absent')) {
                        throw 'Invalid Index windows_scheduled_task policy'
                    }
                    $script:IndexTaskPolicy = $property.Value
                }
                'start_service' {
                    if ($property.Value -isnot [bool]) { throw 'Index start_service must be boolean' }
                    $script:IndexStartService = $property.Value
                }
                'start_engine' {
                    if ($property.Value -isnot [bool]) { throw 'Index start_engine must be boolean' }
                    $script:IndexStartEngine = $property.Value
                }
                default { throw "Unknown Index installation-local policy: $($property.Name)" }
            }
        }
    } elseif ($env:AGENT_INDEX_INSTALLATION_CONFIG) {
        throw 'Selected Index installation policy is unavailable'
    }
    $script:IndexObservedTasks = @{}
    # schtasks is an inbox read adapter, independent of the ScheduledTasks module.
    # A successful census distinguishes absence from permission/transport failures.
    $nativeError = ''
    $listing = Invoke-Hidden -FilePath 'schtasks.exe' -ArgList @('/Query', '/FO', 'CSV', '/NH') -ErrorOutput ([ref]$nativeError)
    if ($LASTEXITCODE -ne 0) { throw "Index task query refused: $nativeError" }
    $rows = @("$listing" | ConvertFrom-Csv -Header 'Name', 'NextRun', 'Status')
    foreach ($name in @($TaskName, $EngineTaskName)) {
        $matches = @($rows | Where-Object { $_.Name -ieq "\$name" })
        if ($matches.Count -gt 1) { throw "Ambiguous Index task identity: $name" }
        if ($matches.Count -eq 0) { $script:IndexObservedTasks[$name] = $null; continue }
        $raw = Invoke-Hidden -FilePath 'schtasks.exe' -ArgList @('/Query', '/TN', "\$name", '/XML') -ErrorOutput ([ref]$nativeError)
        if ($LASTEXITCODE -ne 0) { throw "Index task XML query refused for ${name}: $nativeError" }
        if ("$raw".Length -gt 524288 -or "$raw".Contains('<!')) { throw "Unsupported Index task XML for $name" }
        try { $xml = [xml]"$raw" } catch { throw "Invalid Index task XML for $name" }
        $manager = New-Object Xml.XmlNamespaceManager($xml.NameTable)
        $manager.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
        $enablement = @($xml.SelectNodes('/t:Task/t:Settings/t:Enabled', $manager))
        if ($enablement.Count -ne 1 -or $enablement[0].InnerText -cnotin @('true', 'false')) {
            throw "Unknown Index task enablement: $name"
        }
        $script:IndexObservedTasks[$name] = @{ Xml = $xml; Namespace = $manager; Enabled = $enablement[0].InnerText -ceq 'true' }
    }
}

function Test-IndexStartAllowed {
    param([string]$Name, [switch]$Refresh)
    if ($Refresh) { Initialize-IndexSchedulingPolicy }
    if ($Name -eq $TaskName -and -not $script:IndexStartService) { return $false }
    if ($Name -eq $EngineTaskName -and -not $script:IndexStartEngine) { return $false }
    $observed = $script:IndexObservedTasks[$Name]
    return ($null -eq $observed -or $observed.Enabled)
}

function Assert-IndexTaskOwned {
    param([string]$Name, $Observed)
    if ($Name -cnotin @('agent-index', 'agent-index-engine')) { throw 'Unknown Index task family' }
    $xml = $Observed.Xml
    $ns = $Observed.Namespace
    $uri = $xml.SelectSingleNode('/t:Task/t:RegistrationInfo/t:URI', $ns)
    if ($uri -and $uri.InnerText -cne "\$Name") { throw "Foreign Index task folder/name: $Name" }
    $principals = @($xml.SelectNodes('/t:Task/t:Principals/t:Principal', $ns))
    if ($principals.Count -ne 1) { throw "Ambiguous Index task principal: $Name" }
    $user = $principals[0].SelectSingleNode('t:UserId', $ns)
    $logon = $principals[0].SelectSingleNode('t:LogonType', $ns)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $user -or -not $logon -or $logon.InnerText -cne 'InteractiveToken' -or
        $user.InnerText -inotin @($identity.Name, $identity.User.Value)) {
        throw "Foreign Index task principal: $Name"
    }
    $exec = @($xml.SelectNodes('/t:Task/t:Actions/*', $ns))
    if ($exec.Count -ne 1 -or $exec[0].LocalName -ne 'Exec') { throw "Ambiguous Index task actions: $Name" }
    $command = $exec[0].SelectSingleNode('t:Command', $ns)
    $arguments = $exec[0].SelectSingleNode('t:Arguments', $ns)
    if (-not $command -or -not $arguments -or
        @($exec[0].SelectNodes('t:Command', $ns)).Count -ne 1 -or
        @($exec[0].SelectNodes('t:Arguments', $ns)).Count -ne 1) { throw "Incomplete Index task action: $Name" }
    # Parse the shell's actual -File operand; a matching path in forwarded
    # arguments to a foreign script never proves ownership.
    $rawTokens = @([regex]::Matches($arguments.InnerText, '"[^"]*"|[^\s"]+') | ForEach-Object { $_.Value })
    $tokens = @($rawTokens | ForEach-Object { $_.Trim('"') })
    $residual = [regex]::Replace($arguments.InnerText, '"[^"]*"|[^\s"]+', '')
    if ($residual.Trim()) {
        throw "Unsupported Index task shell syntax: $Name"
    }
    $image = $command.InnerText
    $conhosts = @('conhost.exe', (Join-Path $env:SystemRoot 'System32\conhost.exe'))
    if ($image -iin $conhosts) {
        if ($tokens.Count -lt 3 -or $tokens[0] -cne '--headless') { throw "Unsupported Index task wrapper: $Name" }
        $image = $tokens[1]
        $tokens = @($tokens | Select-Object -Skip 2)
        $rawTokens = @($rawTokens | Select-Object -Skip 2)
    }
    $shells = @('powershell', 'powershell.exe', 'pwsh', 'pwsh.exe',
        (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'),
        (Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'))
    if ($image -inotin $shells) {
        throw "Foreign Index task executable: $Name"
    }
    $operand = $null
    for ($i = 0; $i -lt $tokens.Count; $i++) {
        switch -Regex ($tokens[$i]) {
            '^(?i)-NoProfile$' { continue }
            '^(?i)-NonInteractive$' { continue }
            '^(?i)-(ExecutionPolicy|WindowStyle)$' {
                if (++$i -ge $tokens.Count -or $tokens[$i] -notin @('Bypass', 'Hidden')) {
                    throw "Unsupported Index shell option: $Name"
                }
                continue
            }
            '^(?i)-File$' {
                if ($i + 2 -ne $tokens.Count) { throw "Ambiguous Index script operand: $Name" }
                $operand = $tokens[$i + 1]
                $literal = $rawTokens[$i + 1]
                if ($operand -match "[``;&|<>']" -and
                    -not ($literal.StartsWith('"') -and $literal.EndsWith('"'))) {
                    throw "Unsupported Index task shell syntax: $Name"
                }
                $i = $tokens.Count
                continue
            }
            default { throw "Unsupported Index shell option: $Name" }
        }
    }
    $expected = if ($Name -eq $TaskName) { $Launcher } else { $EngineLauncher }
    if (-not $operand -or -not [IO.Path]::IsPathRooted($operand) -or
        ($operand -split '[/\\]') -contains '..' -or
        [IO.Path]::GetFullPath($operand) -ine [IO.Path]::GetFullPath($expected)) {
        throw "Foreign Index task installation: $Name"
    }
}

function Remove-IndexScheduledTasks {
    param([string[]]$Names = @($TaskName, $EngineTaskName))
    if ($script:IndexTaskPolicy -cne 'absent') { throw 'Index task retirement requires windows_scheduled_task: absent' }
    if (-not $Names -or @($Names | Select-Object -Unique).Count -ne $Names.Count) { throw 'Ambiguous Index retirement names' }
    foreach ($name in $Names) {
        if ($name -cnotin @($TaskName, $EngineTaskName)) { throw "Foreign Index retirement task: $name" }
        $observed = $script:IndexObservedTasks[$name]
        if ($null -eq $observed) { continue }
        Assert-IndexTaskOwned -Name $name -Observed $observed
        $intent = if ($name -eq $TaskName) { $script:IndexStartService } else { $script:IndexStartEngine }
        if (-not $observed.Enabled -and $intent) {
            throw "Disabled Index task $name requires its durable start policy false before retirement"
        }
    }
    foreach ($name in $Names) {
        if ($null -eq $script:IndexObservedTasks[$name]) { continue }
        # Re-read immediately before mutation, including enablement/ownership.
        $previous = $script:IndexObservedTasks[$name].Xml.OuterXml
        Initialize-IndexSchedulingPolicy
        if ($script:IndexTaskPolicy -cne 'absent') { throw 'Index task retirement policy changed during admission' }
        $observed = $script:IndexObservedTasks[$name]
        if ($null -eq $observed -or $observed.Xml.OuterXml -cne $previous) {
            throw "Index task changed during retirement: $name"
        }
        Assert-IndexTaskOwned -Name $name -Observed $observed
        $intent = if ($name -eq $TaskName) { $script:IndexStartService } else { $script:IndexStartEngine }
        if (-not $observed.Enabled -and $intent) { throw "Index disabled-task hold changed during retirement: $name" }
        $nativeError = ''
        $null = Invoke-Hidden -FilePath 'schtasks.exe' -ArgList @('/Delete', '/TN', "\$name", '/F') -ErrorOutput ([ref]$nativeError)
        if ($LASTEXITCODE -ne 0) { throw "Index task removal refused for ${name}: $nativeError" }
        Initialize-IndexSchedulingPolicy
        if ($null -ne $script:IndexObservedTasks[$name]) { throw "Index task removal was not confirmed: $name" }
    }
}

function Invoke-IndexTaskScheduling {
    if ($script:IndexTaskPolicy -eq 'absent') { throw 'Index task scheduling contradicts absent policy' }
    foreach ($name in @($TaskName, $EngineTaskName)) {
        $observed = $script:IndexObservedTasks[$name]
        if ($null -ne $observed) { Assert-IndexTaskOwned -Name $name -Observed $observed }
    }
    foreach ($name in @($TaskName, $EngineTaskName)) {
        $observed = $script:IndexObservedTasks[$name]
        if ($null -ne $observed -and -not $observed.Enabled) {
            Write-Skip "Disabled task retained without changes: $name"
            continue
        }
        if (-not (Test-IndexStartAllowed -Name $name)) {
            Write-Skip "Task scheduling held by explicit capability start policy: $name"
            continue
        }
        $path = if ($name -eq $TaskName) { $Launcher } else { $EngineLauncher }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            if ($name -eq $EngineTaskName -and $null -eq $observed) {
                Write-Skip 'Engine launcher not provisioned -- no engine task registered'
                continue
            }
            throw "Index task launcher is unavailable: $path (install the capability first)"
        }
        $spec = New-TaskSpec -LauncherPath $path
        if (-not (Register-UserModeTask -TaskName $name -Spec $spec)) { throw "Index task scheduling failed: $name" }
    }
}
