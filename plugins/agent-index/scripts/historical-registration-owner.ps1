# Native Index registration effects. Never dot-source the installer entrypoint.
. (Join-Path $PSScriptRoot 'index-transaction.ps1')

function Get-IndexHistoricalTaskSnapshot {
    param([string]$Name)
    if ($Name -notin @('agent-index', 'agent-index-engine')) { throw 'Unsupported Index task family' }
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    try { $task = $folder.GetTask($Name) }
    catch {
        $errorObject = $_.Exception
        while ($null -ne $errorObject) {
            if ($errorObject.HResult -eq -2147024894) { return $null }
            $errorObject = $errorObject.InnerException
        }
        throw
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $security = $task.GetSecurityDescriptor(7)
    $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new($security)
    if ($null -eq $descriptor.Owner -or $descriptor.Owner.Value -cne $identity.User.Value) {
        throw 'Task security owner is not the selected current user'
    }
    @{
        name = $Name
        path = $task.Path
        xml = $task.Xml
        securityDescriptor = $security
        sid = $identity.User.Value
        user = $identity.Name
        state = [int]$task.State
        enabled = [bool]$task.Enabled
        instances = [int]$task.GetInstances(0).Count
    }
}

function Get-IndexHistoricalTaskFingerprint {
    param($Snapshot)
    $content = "$($Snapshot.xml)`n$($Snapshot.securityDescriptor)`n$($Snapshot.sid)`n$($Snapshot.state)"
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [Convert]::ToHexString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($content))).ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Remove-IndexHistoricalTask {
    param([string]$Name)
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $scheduler.GetFolder('\').DeleteTask($Name, 0)
}


function Test-IndexHistoricalTaskConfiguration {
    param($Expected, $Current)
    foreach ($key in @('name', 'path', 'securityDescriptor', 'sid')) {
        if ($Expected[$key] -cne $Current[$key]) { return $false }
    }
    $documents = @()
    foreach ($snapshot in @($Expected, $Current)) {
        $document = [xml]$snapshot.xml
        $namespace = [Xml.XmlNamespaceManager]::new($document.NameTable)
        $namespace.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
        # Disabling changes only this scheduler-owned setting; all remaining
        # XML and native security/identity must stay exactly as admitted.
        foreach ($node in $document.SelectNodes('/t:Task/t:Settings/t:Enabled', $namespace)) {
            $null = $node.ParentNode.RemoveChild($node)
        }
        $documents += $document.DocumentElement.OuterXml
    }
    return $documents[0] -ceq $documents[1]
}

function Invoke-IndexHistoricalTaskRetirement {
    param([string]$Name, $Expected)
    $current = Get-IndexHistoricalTaskSnapshot -Name $Name
    if ($null -eq $current) { return @{status='already-absent'; reason='native-registration-absent'} }
    if ((Get-IndexHistoricalTaskFingerprint $current) -cne (Get-IndexHistoricalTaskFingerprint $Expected) -or
        $current.state -notin @(1, 3) -or $current.instances -ne 0) {
        return @{status='preserved'; reason='native-registration-changed-or-unsettled'}
    }
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $task = $folder.GetTask($Name)
    $wasEnabled = [bool]$task.Enabled
    if ($wasEnabled -ne $current.enabled) {
        return @{status='preserved'; reason='native-registration-scheduling-changed'}
    }
    $changed = $false
    $removed = $false
    try {
        if ($wasEnabled) {
            $changed = $true
            $task.Enabled = $false
        }
        $quiescent = Get-IndexHistoricalTaskSnapshot -Name $Name
        if ($null -eq $quiescent) {
            $removed = $true
            return @{status='already-absent'; reason='native-registration-absent'}
        }
        if ($quiescent.enabled -or -not (Test-IndexHistoricalTaskConfiguration $Expected $quiescent)) {
            return @{status='failed'; reason='native-registration-scheduling-quiescence-unconfirmed'}
        }
        if ($quiescent.state -ne 1 -or $quiescent.instances -ne 0) {
            return @{status='preserved'; reason='native-registration-has-outstanding-instances'}
        }
        Remove-IndexHistoricalTask -Name $Name
        if ($null -ne (Get-IndexHistoricalTaskSnapshot -Name $Name)) {
            return @{status='failed'; reason='native-registration-removal-unconfirmed'}
        }
        $removed = $true
        return @{status='applied'; reason='selected-legacy-registration-retired'}
    } catch {
        return @{status='failed'; reason=$_.Exception.Message}
    } finally {
        if ($changed -and -not $removed) {
            $remaining = Get-IndexHistoricalTaskSnapshot -Name $Name
            if ($null -ne $remaining) {
                if (-not (Test-IndexHistoricalTaskConfiguration $Expected $remaining)) {
                    throw 'native-registration-enabled-restoration-unconfirmed: configuration changed'
                }
                $folder.GetTask($Name).Enabled = $true
                $restored = Get-IndexHistoricalTaskSnapshot -Name $Name
                if ($null -eq $restored -or -not $restored.enabled -or
                    -not (Test-IndexHistoricalTaskConfiguration $Expected $restored)) {
                    throw 'native-registration-enabled-restoration-unconfirmed'
                }
            }
        }
    }
}

function Invoke-IndexHistoricalRegistration {
    param($Request, [scriptblock]$Admit)
    if ($Request.name -notin @('agent-index', 'agent-index-engine') -or
        -not [IO.Path]::IsPathFullyQualified([string]$Request.legacyRoot)) {
        throw 'Unsupported historical Index registration selection'
    }
    # The same mutex as install/update/register-tasks/uninstall, with no unsafe
    # Local fallback for destructive historical cleanup.
    $mutex = Enter-IndexStampLock -Scope 'Transaction' -LockRoot $Request.legacyRoot -RequireGlobal
    try {
        $observed = Get-IndexHistoricalTaskSnapshot -Name $Request.name
        if ($null -eq $observed) { return @{status='already-absent'; reason='native-registration-absent'} }
        $decision = & $Admit $observed
        if ($decision.action -ne 'remove') { return @{status='preserved'; reason='inspection-only'} }
        if ($decision.sha256 -cne (Get-IndexHistoricalTaskFingerprint $observed)) {
            return @{status='preserved'; reason='native-observation-changed'}
        }
        $current = Get-IndexHistoricalTaskSnapshot -Name $Request.name
        if ($null -eq $current) { return @{status='already-absent'; reason='native-registration-absent'} }
        if ($current.path -cne ('\' + $Request.name) -or $current.state -notin @(1, 3) -or
            (Get-IndexHistoricalTaskFingerprint $current) -cne $decision.sha256) {
            return @{status='preserved'; reason='native-registration-changed-or-unsettled'}
        }
        return Invoke-IndexHistoricalTaskRetirement -Name $Request.name -Expected $current
    } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    try {
        $line = [Console]::In.ReadLine()
        if ($null -eq $line -or $line.Length -gt 16384) { throw 'Invalid native owner request' }
        $request = $line | ConvertFrom-Json
        $result = Invoke-IndexHistoricalRegistration -Request $request -Admit {
            param($snapshot)
            [Console]::Out.WriteLine((@{stage='observed'; snapshot=$snapshot} | ConvertTo-Json -Compress -Depth 8))
            $decision = [Console]::In.ReadLine()
            if ($null -eq $decision -or $decision.Length -gt 16384) { throw 'Native admission missing' }
            return ($decision | ConvertFrom-Json)
        }
        [Console]::Out.WriteLine((@{stage='result'; result=$result} | ConvertTo-Json -Compress -Depth 8))
    } catch {
        [Console]::Out.WriteLine((@{stage='result'; result=@{status='failed'; reason=$_.Exception.Message}} | ConvertTo-Json -Compress -Depth 8))
        exit 1
    }
}
