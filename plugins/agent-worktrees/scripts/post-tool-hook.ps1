function ConvertFrom-PostToolJson {
    param([string]$Text)
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        if (-not ${function:ConvertFrom-ManagedWorktreesJson}) {
            . ([IO.Path]::Combine($PSScriptRoot, 'managed-session.ps1'))
        }
        return ,(ConvertFrom-ManagedWorktreesJson $Text)
    }
    if ($script:postToolLegacyJson) {
        return ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    }
    if (-not $script:postToolJson) {
        [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
        $script:postToolJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new()
        $script:postToolJson.MaxJsonLength = [int]::MaxValue
        $script:postToolJson.RecursionLimit = 100
    }
    try {
        return ,($script:postToolJson.DeserializeObject($Text))
    } catch [ArgumentException], [InvalidOperationException] {
        # Reserved metadata such as __type needs the existing JSON compatibility path.
        $script:postToolLegacyJson = $true
        return ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    }
}

function ConvertTo-PostToolJson {
    param([object]$Value)
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        if (-not ${function:ConvertTo-PreparedWorktreesJson}) {
            . ([IO.Path]::Combine($PSScriptRoot, 'managed-session.ps1'))
        }
        return ConvertTo-PreparedWorktreesJson -InputObject $Value -Depth 100
    }
    if ($script:postToolLegacyJson) {
        return ConvertTo-Json -InputObject $Value -Depth 100 -Compress
    }
    try {
        return $script:postToolJson.Serialize($Value)
    } catch [ArgumentException], [InvalidOperationException] {
        $script:postToolLegacyJson = $true
        return ConvertTo-Json -InputObject $Value -Depth 100 -Compress
    }
}

function Invoke-ResidentPostTool {
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Endpoint,
        [Parameter(Mandatory = $true)][string]$PayloadJson,
        [ValidateSet('preToolUse', 'postToolUse')][string]$Kind = 'postToolUse',
        [switch]$ValidatedContext
    )
    $timer = [Diagnostics.Stopwatch]::StartNew()
    if ($Endpoint.hook_transport -cne 'tcp' -or
        $Endpoint.hook_endpoint -cnotmatch '^127\.0\.0\.1:([0-9]+)$' -or
        $Endpoint.hook_token -isnot [string] -or
        [string]::IsNullOrEmpty($Endpoint.hook_token) -or
        $Endpoint.hook_generation -isnot [string] -or -not $Endpoint.hook_generation -or
        $Endpoint.hook_capabilities -isnot [Collections.IList] -or
        $Endpoint.hook_capabilities -cnotcontains 'native-tool-policy-v1' -or
        (-not $ValidatedContext -and ($Endpoint.installReceipt -or $Endpoint.marketplaceId -or $Endpoint.pluginRoot))) {
        return $null
    }
    $port = 0
    if (-not [int]::TryParse($Matches[1], [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        return $null
    }
    $payload = ConvertFrom-PostToolJson $PayloadJson
    if ($null -eq $payload -or
        ($payload -isnot [pscustomobject] -and $payload -isnot [Collections.IDictionary])) {
        return $null
    }
    $hasSession = if ($payload -is [Collections.IDictionary]) {
        $payload.ContainsKey('sessionId')
    } else { $null -ne $payload.PSObject.Properties['sessionId'] }
    if (-not $hasSession) {
        $session = [Environment]::GetEnvironmentVariable('COPILOT_AGENT_SESSION_ID')
        if ($payload -is [Collections.IDictionary]) { $payload['sessionId'] = $session }
        else { $payload | Add-Member -NotePropertyName 'sessionId' -NotePropertyValue $session }
    }
    $hasCwd = if ($payload -is [Collections.IDictionary]) {
        $payload.ContainsKey('cwd')
    } else { $null -ne $payload.PSObject.Properties['cwd'] }
    if (-not $hasCwd) {
        $hasWorkingDirectory = if ($payload -is [Collections.IDictionary]) {
            $payload.ContainsKey('workingDirectory')
        } else { $null -ne $payload.PSObject.Properties['workingDirectory'] }
        $cwd = if ($hasWorkingDirectory) { $payload.workingDirectory }
        else { [Environment]::GetEnvironmentVariable('COPILOT_PROJECT_DIR') }
        if ($payload -is [Collections.IDictionary]) { $payload['cwd'] = $cwd }
        else { $payload | Add-Member -NotePropertyName 'cwd' -NotePropertyValue $cwd }
    }
    if ($Kind -ceq 'postToolUse') {
        # Accounting and target invalidation consume the invocation, never its output.
        foreach ($key in @('toolResult', 'tool_result')) {
            if ($payload -is [Collections.IDictionary]) { [void]$payload.Remove($key) }
            else { [void]$payload.PSObject.Properties.Remove($key) }
        }
    }
    # Forward only event policy switches; the selected resident still owns installation roots.
    $forwarded = @{}
    foreach ($key in @('AGENT_WORKTREES_NUDGE', 'AGENT_WORKTREES_AGENDA_NUDGE',
            'AGENT_WORKTREES_NUDGE_CALLS', 'AGENT_WORKTREES_NUDGE_MINUTES',
            'AGENT_WORKTREES_STATELESS_GUARD', 'CROSS_REPO_GUARD', 'CROSS_REPO_GUARD_MODE',
            'ANCHOR_WRITE_GUARD', 'ANCHOR_WRITE_GUARD_MODE', 'PR_SUPERSEDE_GUARD', 'PR_SUPERSEDE_GUARD_MODE')) {
        $value = [Environment]::GetEnvironmentVariable($key)
        $forwarded[$key] = $value
    }
    $metadata = @{ nativePolicy = 1; environment = $forwarded }
    if ($Kind -ceq 'preToolUse') {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        try {
            $metadata['callerElevated'] = ([Security.Principal.WindowsPrincipal]::new(
                $identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        } finally { $identity.Dispose() }
    }
    if ($payload -is [Collections.IDictionary]) { $payload['_agentWorktrees'] = $metadata }
    else { $payload | Add-Member -Force -NotePropertyName '_agentWorktrees' -NotePropertyValue $metadata }
    $request = @{
        version = 1
        token = $Endpoint.hook_token
        kind = $Kind
        hook_generation = $Endpoint.hook_generation
        payload = $payload
        deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 +
            [Math]::Max(0, 500 - [int]$timer.ElapsedMilliseconds) / 1000.0
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $bytes = $utf8.GetBytes((ConvertTo-PostToolJson $request) + "`n")
    $requestLimit = if ($Kind -ceq 'preToolUse') { 2 * 1024 * 1024 } else { 65536 }
    if ($bytes.Length -gt $requestLimit) { return $null }
    $socket = [Net.Sockets.Socket]::new(
        [Net.Sockets.AddressFamily]::InterNetwork,
        [Net.Sockets.SocketType]::Stream, [Net.Sockets.ProtocolType]::Tcp)
    $response = [IO.MemoryStream]::new()
    try {
        if ($timer.ElapsedMilliseconds -ge 500) { return $null }
        $socket.Blocking = $false
        try {
            $socket.Connect([Net.IPAddress]::Loopback, $port)
        } catch {
            $failure = $_.Exception.GetBaseException()
            if ($failure -isnot [Net.Sockets.SocketException] -or
                $failure.SocketErrorCode -notin @(
                    [Net.Sockets.SocketError]::WouldBlock, [Net.Sockets.SocketError]::InProgress,
                    [Net.Sockets.SocketError]::AlreadyInProgress)) { throw }
        }
        $remaining = 500 - [int]$timer.ElapsedMilliseconds
        if ($remaining -le 0 -or -not $socket.Poll(
                $remaining * 1000, [Net.Sockets.SelectMode]::SelectWrite) -or
            [int]$socket.GetSocketOption(
                [Net.Sockets.SocketOptionLevel]::Socket, [Net.Sockets.SocketOptionName]::Error) -ne 0) {
            return $null
        }
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $remaining = 500 - [int]$timer.ElapsedMilliseconds
            if ($remaining -le 0 -or -not $socket.Poll(
                    $remaining * 1000, [Net.Sockets.SelectMode]::SelectWrite)) { return $null }
            try {
                $count = $socket.Send($bytes, $offset, $bytes.Length - $offset, [Net.Sockets.SocketFlags]::None)
            } catch {
                $failure = $_.Exception.GetBaseException()
                if ($failure -is [Net.Sockets.SocketException] -and
                    $failure.SocketErrorCode -eq [Net.Sockets.SocketError]::WouldBlock) { continue }
                throw
            }
            if ($count -le 0) { return $null }
            $offset += $count
        }
        $buffer = [byte[]]::new(4096)
        $complete = $false
        while ($response.Length -lt 65536 -and -not $complete) {
            $remaining = 500 - [int]$timer.ElapsedMilliseconds
            if ($remaining -le 0 -or -not $socket.Poll(
                    $remaining * 1000, [Net.Sockets.SelectMode]::SelectRead)) { return $null }
            try {
                $count = $socket.Receive(
                    $buffer, 0, [int][Math]::Min($buffer.Length, 65536 - $response.Length),
                    [Net.Sockets.SocketFlags]::None)
            } catch {
                $failure = $_.Exception.GetBaseException()
                if ($failure -is [Net.Sockets.SocketException] -and
                    $failure.SocketErrorCode -eq [Net.Sockets.SocketError]::WouldBlock) { continue }
                throw
            }
            if ($count -eq 0) { break }
            for ($index = 0; $index -lt $count; $index++) {
                if ($buffer[$index] -eq 10) {
                    $count = $index
                    $complete = $true
                    break
                }
            }
            $response.Write($buffer, 0, $count)
        }
        if (-not $complete) { return $null }
        $reply = ConvertFrom-PostToolJson $utf8.GetString($response.ToArray())
        if (($reply.version -isnot [int] -and $reply.version -isnot [long]) -or
            $reply.version -ne 1 -or $reply.fallback -eq $true -or
            $reply.capabilities -isnot [Collections.IList] -or
            $reply.capabilities -cnotcontains 'native-tool-policy-v1' -or
            ($reply.result -isnot [pscustomobject] -and
             $reply.result -isnot [Collections.IDictionary])) {
            return $null
        }
        if ($Kind -ceq 'postToolUse') {
            if ($reply.result -is [Collections.IDictionary]) {
                if ($reply.result.ContainsKey('additionalContext') -and
                    $reply.result['additionalContext'] -isnot [string]) { return $null }
            } else {
                $context = $reply.result.PSObject.Properties['additionalContext']
                if ($null -ne $context -and $context.Value -isnot [string]) { return $null }
            }
        }
        $resultJson = ConvertTo-PostToolJson $reply.result
        if ($timer.ElapsedMilliseconds -gt 500) { return $null }
        return $resultJson
    } finally {
        $response.Dispose()
        $socket.Dispose()
    }
}

function Invoke-SelectedResidentToolHook {
    param(
        [ValidateSet('preToolUse', 'postToolUse')][string]$Kind,
        [AllowEmptyString()][string]$Raw,
        [bool]$PreparedMembership = $false
    )
    . ([IO.Path]::Combine($PSScriptRoot, 'hook-facts.ps1'))
    if ($env:COPILOT_EXTENSIONS_CONTEXT -and -not $PreparedMembership) {
        throw 'Prepared installation-context admission is unavailable'
    }
    $runtime = Get-PostToolRuntimeRoot
    $snapshot = $null
    $endpoint = Read-PostToolBoundedJson ([IO.Path]::Combine($runtime, 'status-monitor.lock')) 16384 ([ref]$snapshot)
    if ($null -eq $endpoint) { throw 'Selected resident rendezvous is unavailable' }
    $endpoint = ConvertFrom-ManagedWorktreesJson $snapshot
    if (-not (Test-PostToolDiscoveryContext -Discovery $endpoint -RuntimeRoot $runtime)) {
        throw 'Selected resident installation does not match'
    }
    $payload = if ([string]::IsNullOrWhiteSpace($Raw)) { '{}' } else { $Raw }
    return Invoke-ResidentPostTool -Endpoint $endpoint -PayloadJson $payload -Kind $Kind -ValidatedContext
}

function Invoke-PostToolHook {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $OutputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    $admission = [IO.Path]::Combine($PSScriptRoot, 'managed-session.ps1')
    if (-not [IO.File]::Exists($admission)) {
        [Console]::Error.WriteLine('agent-worktrees postToolUse: native admission reader unavailable; completion is unconfirmed')
        return
    }
    . $admission
    $prepared = Test-PreparedManagedWorktreesSession -Raw $Raw
    $factsReader = [IO.Path]::Combine($PSScriptRoot, 'hook-facts.ps1')
    if ($prepared -and [IO.File]::Exists($factsReader)) {
        . $factsReader
        $readResult = Invoke-PreparedPostToolRead -Raw $Raw -PreparedMembership $prepared
        if ($null -ne $readResult) {
            if ($readResult.Count) { [Console]::Out.Write((ConvertTo-PreparedWorktreesJson -InputObject $readResult)) }
            return
        }
    }
    try {
        $result = Invoke-SelectedResidentToolHook -Kind postToolUse -Raw $Raw -PreparedMembership $prepared
        if ($null -ne $result) {
            if ($result -ne '{}') { [Console]::Out.Write($result) }
            return
        }
    } catch {
        # A timed-out request may have been delivered; never replay it.
    }
    [Console]::Error.WriteLine('agent-worktrees postToolUse: selected native resident unavailable; advisory/accounting completion is unconfirmed; no local fallback or retry was run')
}
