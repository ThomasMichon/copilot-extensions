param([string]$Root, [string]$Plugin, [string]$Library, [string]$Mode)
$ErrorActionPreference = 'Stop'
. (Join-Path $Library 'single-instance-lease\src\single_instance_lease\operation.ps1')
. (Join-Path $Library 'update-driver\src\update_driver\foundation.ps1')
$utf8 = New-Object Text.UTF8Encoding($false)
function Save([string]$Name, [string]$Value) {
    [IO.File]::WriteAllText((Join-Path $Root $Name), $Value, $utf8)
}
if ($Mode -eq 'prerequisite-helpers') {
    . (Join-Path $Library 'installer-engine\installer-engine.ps1')
    $script:probed = New-Object 'Collections.Generic.List[string]'
    $script:legacy = Join-Path $Root 'signed-310-fixture'
    $script:modern = Join-Path $Root 'signed-311-fixture'
    Save 'signed-310-fixture' 'fixture, never executed'
    Save 'signed-311-fixture' 'fixture, never executed'
    function py {
        param([string]$Version)
        $script:probed.Add($Version)
        $global:LASTEXITCODE = 0
        if ($Version -eq '-3.10') { return $script:legacy }
        if ($Version -eq '-3.11' -and $script:allowModern) { return $script:modern }
    }
    function Get-AuthenticodeSignature { return @{ Status = 'Valid' } }
    if ((Get-SignedBasePython) -ne $script:legacy) { throw 'legacy signed-base default changed' }
    $script:probed.Clear()
    if ($null -ne (Get-SignedBasePython -MinimumVersion '3.11') -or $script:probed.Contains('-3.10')) {
        throw 'signed-base floor admitted or probed an older interpreter'
    }
    $script:allowModern = $true
    if ((Get-SignedBasePython -MinimumVersion '3.11') -ne $script:modern) {
        throw 'compatible signed base was not preferred'
    }
    function Get-SignedBasePython {
        param([version]$MinimumVersion)
        if ($MinimumVersion -ne [version]'3.11') { throw 'venv lost the signed-base floor' }
        return $null
    }
    $venv = Join-Path $Root 'fixture-venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    function Invoke-UvVenvResilient {
        param([string]$VenvDir, [string[]]$Arguments, [string]$UvCommand)
        if ($UvCommand -ne 'never-executed' -or ($Arguments -join ',') -ne '--python,3.11,--allow-existing') {
            throw 'canonical venv selection options changed'
        }
        [IO.Directory]::CreateDirectory((Join-Path $VenvDir 'Scripts')) | Out-Null
        [IO.File]::WriteAllText((Join-Path $VenvDir 'Scripts\python.exe'), 'fixture, never executed')
        [IO.File]::WriteAllText((Join-Path $VenvDir 'pyvenv.cfg'), 'fixture')
        return @{ ExitCode = 0; Output = '' }
    }
    if (-not (New-SignedVenv -VenvDir $venv -VenvPython $python -PythonVersion '3.11' -MinimumSignedBaseVersion '3.11' -UvCommand 'never-executed')) {
        throw 'canonical managed fallback did not retain its requested interpreter'
    }
    Save 'prerequisite-helpers-ok' 'legacy default, signed preference and managed fallback verified'
    exit 0
}
if ($Mode -eq 'directory') {
    . (Join-Path $Library 'update-driver\src\update_driver\directory.ps1')
    $before = [IO.Directory]::GetAccessControl($Root).GetSecurityDescriptorSddlForm('All')
    New-NativeExecutionDirectory -Root $Root
    if ([IO.Directory]::GetAccessControl($Root).GetSecurityDescriptorSddlForm('All') -ne $before) {
        throw 'existing execution ACL was rewritten'
    }
    foreach ($fresh in @((Join-Path $Root 'nested\fresh'), ((Join-Path $Root 'trailing\fresh') + '\'))) {
        New-NativeExecutionDirectory -Root $fresh
        $security = [IO.Directory]::GetAccessControl($fresh)
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
        if ($security.GetOwner([Security.Principal.SecurityIdentifier]) -ne $sid -or -not $security.AreAccessRulesProtected) {
            throw 'new execution directory owner or protection differs from TokenUser policy'
        }
        $allowed = @($sid.Value, 'S-1-5-18', 'S-1-5-32-544') | Sort-Object -Unique
        $actual = @($security.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) |
            ForEach-Object { $_.IdentityReference.Value }) | Sort-Object -Unique
        if (($allowed -join ',') -ne ($actual -join ',')) { throw 'new execution access policy differs' }
    }
    Save 'directory-ok' 'new owner/protected ACL and unchanged existing ACL verified'
    exit 0
}
if ($Mode -eq 'protocol') {
    $results = @()
    foreach ($path in (Get-ChildItem -LiteralPath (Join-Path $Root 'corpus') -File | Sort-Object Name)) {
        [IO.File]::Copy($path.FullName, (Join-Path $Root 'foundation-operation.json'), $true)
        $result = @{ name = $path.Name; accepted = $false; document = $null }
        try {
            $result.document = Get-NativeFoundationProgress -Root $Root
            $result.accepted = $true
        } catch {
            $result.error = $_.Exception.Message
        }
        $results += $result
    }
    Save 'report.json' (ConvertTo-Json -InputObject $results -Depth 5 -Compress)
    exit 0
}
$owned = Enter-NativeOperationLock ([IO.Path]::Combine($Root, 'execution.lock'))
try {
    if ($Mode -eq 'refuse-freeze') {
        $digest = [CopilotExtensions.NativeFoundationState]::SourceDigest($Plugin)
        $assertion = $owned.CreateWitnessAssertion([IO.Path]::Combine($Root, 'execution.lock'))
        foreach ($name in @('update-driver', 'agent-procutil', 'single-instance-lease')) {
            $destination = Join-Path ([IO.Path]::GetDirectoryName($Plugin)) ($name + '\frozen')
            $refused = $false
            try { $null = [CopilotExtensions.NativeFoundationState]::FreezeSource($Plugin, $destination, $digest, $assertion) }
            catch { $refused = $true }
            if (-not $refused) { throw 'source-overlapping freeze was accepted' }
            if (Test-Path -LiteralPath $destination) { throw 'refused freeze mutated source' }
        }
        exit 0
    }
    if ($Mode -eq 'recovery') {
        try {
            Save 'recovered.json' (Repair-NativeFoundationAfterRestart -Custody $owned -Root $Root)
        } catch {
            Save 'recovery-refused' $_.Exception.Message
        }
        exit 0
    }
    if ($Mode -eq 'refuse') {
        $refused = $false
        try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
        catch { $refused = $true; Save 'error.txt' $_.Exception.Message }
        if (-not $refused) { throw 'expected admission refusal' }
        exit 0
    }
    $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin
    Save 'admitted.json' $operation
    if ($Mode -eq 'hold') {
        Save 'native-ready' 'actual native progress persisted under custody'
        $elapsed = [Diagnostics.Stopwatch]::StartNew()
        while ($elapsed.ElapsedMilliseconds -lt 120000) {
            $owned.AssertOwned()
            Start-Sleep -Milliseconds 20
        }
        throw 'native progress fixture deadline expired'
    }
    $duplicate = $false
    try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
    catch { $duplicate = $true }
    if (-not $duplicate) { throw 'duplicate admission replaced active progress' }
    if ($null -ne (Get-NativeFoundationProgress -Root (Join-Path $Root 'absent'))) {
        throw 'absent status returned progress'
    }
    if (Test-Path -LiteralPath (Join-Path $Root 'absent')) { throw 'absent status created state' }
    if ($null -ne (Get-NativeFoundationProgress -Root (Join-Path $Root 'absent-parent\state'))) {
        throw 'nested-absent status returned progress'
    }
    if (Test-Path -LiteralPath (Join-Path $Root 'absent-parent')) { throw 'nested-absent status created state' }
    Save 'file-parent' 'ordinary file is not an absent directory'
    $refused = $false
    try { $null = Get-NativeFoundationProgress -Root (Join-Path $Root 'file-parent\state') }
    catch { $refused = $true }
    if (-not $refused) { throw 'file ancestor was treated as absent state' }
    Save 'observed.json' (Get-NativeFoundationProgress -Root $Root)
    $foreign = Enter-NativeOperationLock ([IO.Path]::Combine($Root, 'other.lock'))
    try {
        $refused = $false
        try { $null = Complete-NativeFoundationProgress -Custody $foreign -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0 }
        catch { $refused = $true }
        if (-not $refused) { throw 'foreign custody completed progress' }
    } finally { $foreign.Dispose() }
    $inputPath = Join-Path $Plugin 'source.py'
    $original = [IO.File]::ReadAllBytes($inputPath)
    try {
        [IO.File]::WriteAllText($inputPath, 'changed after admission', $utf8)
        $refused = $false
        try { $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0 }
        catch { $refused = $true }
        if (-not $refused) { throw 'source substitution completed successfully' }
    } finally { [IO.File]::WriteAllBytes($inputPath, $original) }
    $reader = [IO.File]::Open((Join-Path $Root 'foundation-operation.json'), 'Open', 'Read', 'Read')
    try {
        $refused = $false
        try { $null = Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0 }
        catch { $refused = $true }
        if (-not $refused) { throw 'unshareable reader did not refuse bounded publication' }
        if ((Get-NativeFoundationProgress -Root $Root) -ne $operation) { throw 'failed publication discarded progress' }
    } finally { $reader.Dispose() }
    Save 'completed.json' (Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 0)
    $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin
    Save 'failed.json' (Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -ExitCode 7 -Reason builder_failed)
    $operation = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin
    Save 'interrupted.json' (Complete-NativeFoundationProgress -Custody $owned -Root $Root -Operation $operation -Plugin $Plugin -Reason builder_settlement_unknown)
    $fenced = $false
    try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
    catch { $fenced = $true }
    if (-not $fenced) { throw 'uncertain progress was replayed' }
} finally {
    $owned.Dispose()
}
$refused = $false
try { $null = Start-NativeFoundationProgress -Custody $owned -Root $Root -Plugin $Plugin }
catch { $refused = $true }
if (-not $refused) { throw 'disposed custody admitted progress' }
