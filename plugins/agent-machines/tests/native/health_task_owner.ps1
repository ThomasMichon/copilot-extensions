param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('identity', 'register', 'read', 'run', 'remove')]
    [string]$Operation,
    [string]$Name,
    [string]$Definition
)
$ErrorActionPreference = 'Stop'
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
if ($Operation -eq 'identity') {
    @{
        sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        interactive = [Environment]::UserInteractive
    } | ConvertTo-Json -Compress
    exit 0
}
if ($Name -cnotmatch '^ce-health-fixture-[a-f0-9]{32}$') {
    throw 'Only an exact test-owned task name is admitted'
}
$uri = "urn:copilot-extensions:health-fixture:$Name"
function Get-OwnedTask {
    try { $task = $folder.GetTask($Name) }
    catch {
        if ($_.Exception.GetBaseException().HResult -eq -2147024894) { return $null }
        throw
    }
    [xml]$xml = $task.Xml
    $ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    if ($xml.SelectSingleNode('/t:Task/t:RegistrationInfo/t:Description', $ns).InnerText -cne $uri) {
        throw 'Task ownership differs; no mutation performed'
    }
    return $task
}
if ($Operation -eq 'register') {
    [xml]$xml = Get-Content -LiteralPath $Definition -Raw
    $ns = New-Object Xml.XmlNamespaceManager($xml.NameTable)
    $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
    if ($xml.SelectSingleNode('/t:Task/t:RegistrationInfo/t:Description', $ns).InnerText -cne $uri) {
        throw 'Definition does not belong to this exact fixture'
    }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    # TASK_CREATE refuses an existing name; never use CREATE_OR_UPDATE.
    $null = $folder.RegisterTask($Name, $xml.OuterXml, 2, $sid, $null, 3, $null)
}
$task = Get-OwnedTask
if ($Operation -eq 'remove') {
    if ($null -ne $task) {
        $task.Enabled = $false
        $task.Stop(0)
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        while ($task.GetInstances(0).Count -ne 0) {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Owned instances did not settle' }
            Start-Sleep -Milliseconds 50
        }
        $folder.DeleteTask($Name, 0)
    }
    if ($null -ne (Get-OwnedTask)) { throw 'Owned task deletion not confirmed' }
    @{ removed = $true; name = $Name; instances = 0 } | ConvertTo-Json -Compress
    exit 0
}
if ($null -eq $task) { throw 'Exact fixture task is absent' }
$run = $null
$runError = $null
if ($Operation -eq 'run') {
    try { $run = $task.Run($null).InstanceGuid }
    catch {
        $runError = $_.Exception.GetBaseException().HResult
        if ($runError -ne -2147216609) { throw }
    }
}
@{
    configured = $true
    enabled = [bool]$task.Enabled
    state = [int]$task.State
    last_result = [long]$task.LastTaskResult
    last_run = $task.LastRunTime.ToUniversalTime().ToString('o')
    next_run = $task.NextRunTime.ToUniversalTime().ToString('o')
    instances = @($task.GetInstances(0) | ForEach-Object { $_.InstanceGuid })
    requested_instance = $run
    run_error = $runError
    xml = $task.Xml
} | ConvertTo-Json -Compress -Depth 4
