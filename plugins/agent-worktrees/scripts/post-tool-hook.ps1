function ConvertFrom-PostToolJson {
    param([string]$Text)
    if ($PSVersionTable.PSVersion.Major -ge 6 -or $script:postToolLegacyJson) {
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
    if ($PSVersionTable.PSVersion.Major -ge 6 -or $script:postToolLegacyJson) {
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
    $payload = ConvertFrom-PostToolJson $PayloadJson
    if ($null -eq $payload -or
        ($payload -isnot [pscustomobject] -and $payload -isnot [Collections.IDictionary])) {
        $payload = @{}
    }
    $request = @{
        version = 1
        token = $Endpoint.hook_token
        kind = 'postToolUse'
        payload = $payload
        deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 + 0.5
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $bytes = $utf8.GetBytes((ConvertTo-PostToolJson $request) + "`n")
    $client = [Net.Sockets.TcpClient]::new()
    $response = [IO.MemoryStream]::new()
    try {
        $connect = $client.ConnectAsync('127.0.0.1', $port)
        if (-not $connect.Wait(500)) { return $null }
        $stream = $client.GetStream()
        $stream.ReadTimeout = 500
        $stream.WriteTimeout = 500
        $stream.Write($bytes, 0, $bytes.Length)
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $buffer = [byte[]]::new(4096)
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
        $reply = ConvertFrom-PostToolJson $utf8.GetString($response.ToArray())
        if ($reply.version -ne 1 -or $reply.fallback -eq $true -or
            ($reply.result -isnot [pscustomobject] -and
             $reply.result -isnot [Collections.IDictionary])) {
            return $null
        }
        return ConvertTo-PostToolJson $reply.result
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
            $value = ConvertFrom-PostToolJson ([IO.File]::ReadAllText($path))
            if ($value -is [Collections.IDictionary]) {
                $endpoint = $value
            } elseif ($value -is [pscustomobject]) {
                $endpoint = @{}
                foreach ($property in $value.PSObject.Properties) {
                    $endpoint[$property.Name] = $property.Value
                }
            } else {
                throw 'invalid resident rendezvous'
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
