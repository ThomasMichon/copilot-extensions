function Invoke-ResidentPostTool {
    param(
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Endpoint,
        [Parameter(Mandatory = $true)][string]$PayloadJson
    )
    if ($Endpoint.hook_transport -cne 'tcp' -or
        $Endpoint.hook_endpoint -cnotmatch '^127\.0\.0\.1:([0-9]+)$' -or
        $Endpoint.hook_token -isnot [string] -or
        [string]::IsNullOrEmpty($Endpoint.hook_token) -or
        $Endpoint.installReceipt -or $Endpoint.marketplaceId -or $Endpoint.pluginRoot) {
        return $null
    }
    $port = 0
    if (-not [int]::TryParse($Matches[1], [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        return $null
    }
    $payload = ConvertFrom-Json -InputObject $PayloadJson -ErrorAction Stop
    if ($null -eq $payload -or $payload -isnot [pscustomobject]) {
        $payload = @{}
    }
    $request = @{
        version = 1
        token = $Endpoint.hook_token
        kind = 'postToolUse'
        payload = $payload
        deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 + 0.5
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $bytes = $utf8.GetBytes((ConvertTo-Json -InputObject $request -Depth 100 -Compress) + "`n")
    $client = New-Object System.Net.Sockets.TcpClient
    $response = New-Object System.IO.MemoryStream
    try {
        $connect = $client.ConnectAsync('127.0.0.1', $port)
        if (-not $connect.Wait(500)) { return $null }
        $stream = $client.GetStream()
        $stream.ReadTimeout = 500
        $stream.WriteTimeout = 500
        $stream.Write($bytes, 0, $bytes.Length)
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $buffer = New-Object byte[] 4096
        $complete = $false
        while ($response.Length -lt 65536 -and -not $complete) {
            $remaining = 500 - [int]$timer.ElapsedMilliseconds
            if ($remaining -le 0) { return $null }
            $stream.ReadTimeout = $remaining
            $count = $stream.Read($buffer, 0, [Math]::Min($buffer.Length, 65536 - $response.Length))
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
        $reply = ConvertFrom-Json -InputObject $utf8.GetString($response.ToArray()) -ErrorAction Stop
        if ($reply.version -ne 1 -or $reply.fallback -eq $true -or
            $reply.result -isnot [pscustomobject]) {
            return $null
        }
        return ConvertTo-Json -InputObject $reply.result -Depth 100 -Compress
    } finally {
        $response.Dispose()
        $client.Dispose()
    }
}

function Invoke-PostToolHook {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Raw)
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $OutputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    try {
        Set-Location -LiteralPath $env:USERPROFILE -ErrorAction Stop
    } catch {
        Set-Location -LiteralPath ([IO.Path]::GetPathRoot($PSScriptRoot)) -ErrorAction Stop
    }
    [IO.Directory]::SetCurrentDirectory((Get-Location).Path)
    $contextual = -not [string]::IsNullOrWhiteSpace($env:COPILOT_EXTENSIONS_CONTEXT)
    if (-not $contextual) {
        try {
            $path = Join-Path $env:USERPROFILE '.agent-worktrees\status-monitor.lock'
            $value = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($path)) -ErrorAction Stop
            if ($value -isnot [pscustomobject]) {
                throw 'invalid resident rendezvous'
            }
            $endpoint = @{}
            foreach ($property in $value.PSObject.Properties) {
                $endpoint[$property.Name] = $property.Value
            }
            $payload = if ([string]::IsNullOrWhiteSpace($Raw)) { '{}' } else { $Raw }
            $result = Invoke-ResidentPostTool -Endpoint $endpoint -PayloadJson $payload
            if ($null -ne $result) {
                if ($result -ne '{}') {
                    [Console]::Out.Write($result)
                }
                return
            }
        } catch {
            [Console]::Error.WriteLine('agent-worktrees postToolUse: resident request unavailable; using local advisory fallback')
        }
    }
    $script = Join-Path $PSScriptRoot 'hook_client.py'
    $python = Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
        Select-Object -First 1 -ExpandProperty Source
    if (-not $python -or -not (Test-Path -LiteralPath $script)) {
        [Console]::Error.WriteLine('agent-worktrees postToolUse: advisory fallback unavailable')
        return
    }
    if ($contextual) {
        $Raw | & $python $script postToolUse
    } else {
        # The advisory fallback must not resend a possibly delivered request.
        $Raw | & $python $script postToolUse --local-fallback
    }
}
