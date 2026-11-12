function ConvertTo-PostToolFactMap {
    param([object]$Value, [string[]]$Names)
    $map = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
    if ($PSVersionTable.PSVersion.Major -ge 6 -and
        $Value -is [Newtonsoft.Json.Linq.JObject]) {
        foreach ($property in $Value.Properties()) {
            $datum = $property.Value
            if ($datum -is [Newtonsoft.Json.Linq.JValue]) { $datum = $datum.Value }
            $map.Add($property.Name, $datum)
        }
    } elseif ($Value -is [Collections.IDictionary]) {
        foreach ($key in $Value.Keys) {
            if ($key -isnot [string]) { throw 'invalid fact field' }
            $map.Add($key, $Value[$key])
        }
    } elseif ($Value -is [pscustomobject]) {
        foreach ($property in $Value.PSObject.Properties) {
            $map.Add($property.Name, $property.Value)
        }
    } else {
        throw 'invalid fact object'
    }
    if ($null -ne $Names) {
        if ($map.Count -ne $Names.Count) { throw 'invalid fact fields' }
        foreach ($name in $Names) {
            if (-not $map.ContainsKey($name)) { throw 'invalid fact fields' }
        }
    }
    return ,$map
}

function Read-PostToolHookFacts {
    param(
        [Collections.IDictionary]$Discovery,
        [string]$RuntimeRoot,
        [string]$ProjectRoot,
        [string]$Project,
        [string]$WorktreeId,
        [string]$CreationNonce,
        [string]$Workspace
    )
    $stream = $null
    $jsonReader = $null
    try {
        if (($Discovery.hook_facts_version -isnot [int] -and
             $Discovery.hook_facts_version -isnot [long]) -or
            $Discovery.hook_facts_version -notin @(1, 2) -or
            $Discovery.hook_facts_generation -isnot [string] -or
            $Discovery.hook_facts_generation -cnotmatch '^[0-9a-f]{32}$') { return $null }
        foreach ($segment in @($Project, $WorktreeId, $CreationNonce)) {
            if ($segment -cnotmatch '^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$' -or
                $segment.EndsWith('.') -or
                $segment -match '^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)') { return $null }
        }
        foreach ($root in @($RuntimeRoot, $ProjectRoot, $Workspace)) {
            if (-not [IO.Path]::IsPathRooted($root)) { return $null }
        }
        $factsHome = [IO.Path]::Combine($ProjectRoot, 'worktrees')
        foreach ($segment in @('', $WorktreeId, 'hook-facts')) {
            if ($segment) { $factsHome = [IO.Path]::Combine($factsHome, $segment) }
            $attributes = [IO.File]::GetAttributes($factsHome)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -or
                -not ($attributes -band [IO.FileAttributes]::Directory)) { return $null }
        }
        $path = [IO.Path]::Combine($factsHome, $Discovery.hook_facts_generation + '.json')
        $attributes = [IO.File]::GetAttributes($path)
        if ($attributes -band ([IO.FileAttributes]::ReparsePoint -bor [IO.FileAttributes]::Directory)) {
            return $null
        }
        $stream = [IO.FileStream]::new(
            $path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        )
        $bytes = [byte[]]::new(16385)
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -gt 16384) { return $null }
        $utf8 = [Text.UTF8Encoding]::new($false, $true)
        $text = $utf8.GetString($bytes, 0, $count)
        if ($PSVersionTable.PSVersion.Major -ge 6) {
            $jsonReader = [Newtonsoft.Json.JsonTextReader]::new([IO.StringReader]::new($text))
            $jsonReader.CloseInput = $true
            $jsonReader.MaxDepth = 4
            $jsonReader.DateParseHandling = [Newtonsoft.Json.DateParseHandling]::None
            $settings = [Newtonsoft.Json.Linq.JsonLoadSettings]::new()
            $settings.DuplicatePropertyNameHandling = [Newtonsoft.Json.Linq.DuplicatePropertyNameHandling]::Error
            $document = [Newtonsoft.Json.Linq.JObject]::Load($jsonReader, $settings)
            if ($jsonReader.Read()) { return $null }
        } else {
            [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
            $factJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new()
            $factJson.MaxJsonLength = 16384
            $factJson.RecursionLimit = 4
            $document = $factJson.DeserializeObject($text)
        }
        $value = ConvertTo-PostToolFactMap $document @(
            'version', 'quality', 'completeness', 'scope', 'producer', 'revision', 'facts'
        )
        $scope = ConvertTo-PostToolFactMap $value['scope'] @(
            'project', 'worktree_id', 'creation_nonce', 'workspace'
        )
        $producer = ConvertTo-PostToolFactMap $value['producer'] @(
            'runtime_root', 'runtime_generation', 'hook_facts_generation',
            'marketplaceId', 'installReceipt', 'pluginRoot', 'projection_generation'
        )
        $factNames = @(
            'status', 'status_note_at', 'last_session_id', 'head_yielded'
        )
        if ($value['version'] -eq 2) { $factNames += 'record_head_session' }
        $facts = ConvertTo-PostToolFactMap $value['facts'] $factNames
        if (($value['version'] -isnot [int] -and $value['version'] -isnot [long]) -or
            $value['version'] -notin @(1, 2) -or
            $value['version'] -gt $Discovery.hook_facts_version -or $value['quality'] -isnot [string] -or
            $value['completeness'] -isnot [string] -or $value['quality'] -cne 'advisory' -or
            $value['completeness'] -cne 'partial' -or
            ($value['revision'] -isnot [int] -and $value['revision'] -isnot [long]) -or
            $value['revision'] -lt 1) { return $null }
        $expectedScope = @{
            project = $Project; worktree_id = $WorktreeId
            creation_nonce = $CreationNonce; workspace = $Workspace
        }
        foreach ($name in $expectedScope.Keys) {
            if ($scope[$name] -isnot [string] -or -not [string]::Equals(
                $scope[$name], $expectedScope[$name], [StringComparison]::Ordinal)) { return $null }
        }
        if (-not [string]::Equals($producer['runtime_root'], $RuntimeRoot, [StringComparison]::Ordinal) -or
            -not [string]::Equals($producer['runtime_generation'], $Discovery.runtime_generation, [StringComparison]::Ordinal) -or
            -not [string]::Equals($producer['hook_facts_generation'], $Discovery.hook_facts_generation, [StringComparison]::Ordinal) -or
            $producer['projection_generation'] -isnot [string] -or
            $producer['projection_generation'] -cnotmatch '^[0-9a-f]{32}$') { return $null }
        foreach ($name in @('marketplaceId', 'installReceipt', 'pluginRoot')) {
            if (-not [string]::Equals($producer[$name], $Discovery[$name], [StringComparison]::Ordinal)) {
                return $null
            }
        }

        foreach ($map in @($scope, $producer, $facts)) {
            foreach ($name in $map.Keys) {
                if ($name -ceq 'head_yielded') {
                    if ($null -ne $map[$name] -and $map[$name] -isnot [bool]) { return $null }
                } elseif ($null -ne $map[$name] -and
                    ($map[$name] -isnot [string] -or $map[$name].Contains([char]0) -or
                     $utf8.GetByteCount($map[$name]) -gt 4096)) { return $null }
            }
        }
        $value['scope'] = $scope
        $value['producer'] = $producer
        $value['facts'] = $facts
        return ,$value
    } catch {
        return $null
    } finally {
        if ($null -ne $jsonReader) { $jsonReader.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Read-PostToolBoundedJson {
    param([string]$Path, [int]$Limit, [ref]$Snapshot)
    $stream = $null
    $reader = $null
    try {
        $null = Get-PreparedWorktreesPath $Path
        $stream = [IO.FileStream]::new(
            $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        )
        $bytes = [byte[]]::new($Limit + 1)
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        if ($count -gt $Limit) { return $null }
        $text = ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes, 0, $count)
        if ($null -ne $Snapshot) { $Snapshot.Value = $text }
        if ($PSVersionTable.PSVersion.Major -ge 6) {
            $reader = [Newtonsoft.Json.JsonTextReader]::new([IO.StringReader]::new($text))
            $reader.CloseInput = $true
            $reader.MaxDepth = 8
            $reader.DateParseHandling = [Newtonsoft.Json.DateParseHandling]::None
            $settings = [Newtonsoft.Json.Linq.JsonLoadSettings]::new()
            $settings.DuplicatePropertyNameHandling = [Newtonsoft.Json.Linq.DuplicatePropertyNameHandling]::Error
            $value = [Newtonsoft.Json.Linq.JObject]::Load($reader, $settings)
            if ($reader.Read()) { return $null }
        } else {
            [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
            $parser = [System.Web.Script.Serialization.JavaScriptSerializer]::new()
            $parser.MaxJsonLength = $Limit
            $parser.RecursionLimit = 8
            $value = $parser.DeserializeObject($text)
        }
        return ,(ConvertTo-PostToolFactMap $value)
    } catch {
        return $null
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Remove-PostToolLateWatch {
    param([string]$Path, [string]$Snapshot)
    $claimed = $Path + '.consuming-' + [Guid]::NewGuid().ToString('N')
    $guard = Enter-PostToolCounterLock ($Path + '.lock')
    if ($null -eq $guard) { return $false }
    try {
        [IO.File]::Move($Path, $claimed)
        try {
            $current = $null
            $null = Read-PostToolBoundedJson $claimed 8192 ([ref]$current)
            if (-not [string]::Equals($current, $Snapshot, [StringComparison]::Ordinal)) {
                return $false
            }
            [IO.File]::Delete($claimed)
            return $true
        } finally {
            if ([IO.File]::Exists($claimed)) {
                try { [IO.File]::Move($claimed, $Path) } catch {
                    if ([IO.File]::Exists($Path)) {
                        # A newer watch supersedes the claimed snapshot; never overwrite it.
                        [IO.File]::Delete($claimed)
                    }
                }
            }
        }
    } catch {} finally { $guard.Dispose() }
    return $false
}

function Test-PostToolWorkerGone {
    param($Owner)
    try { $ownerMap = ConvertTo-PostToolFactMap $Owner } catch { return $false }
    if (($ownerMap['pid'] -isnot [int] -and $ownerMap['pid'] -isnot [long]) -or
        $ownerMap['pid'] -le 0 -or $ownerMap['pid'] -gt [int]::MaxValue -or
        $ownerMap['start_time'] -isnot [string]) { return $false }
    $process = $null
    try {
        $process = [Diagnostics.Process]::GetProcessById([int]$ownerMap['pid'])
    } catch {
        # A valid absent PID is distinct from access denial or another unknown failure.
        return ($_.Exception.InnerException -is [ArgumentException])
    }
    try { return $process.HasExited } catch { return $false } finally { $process.Dispose() }
}

function Get-PostToolLateGuidanceNotice {
    param([string]$SessionHome, [string]$Session, [string]$Cwd)
    try {
        if ($Session -cnotmatch '^[A-Za-z0-9_-]{1,128}$') { return $null }
        $path = [IO.Path]::Combine($SessionHome, 'local-guidance-watch.json')
        $snapshot = $null
        $watch = Read-PostToolBoundedJson $path 8192 ([ref]$snapshot)
        if ($null -eq $watch -or $watch['version'] -ne 1 -or
            $watch['root'] -isnot [string] -or $watch['metadata'] -isnot [string]) { return $null }
        $root = Get-PreparedWorktreesPath $watch['root']
        if (-not [string]::Equals($Cwd, $root, [StringComparison]::OrdinalIgnoreCase) -and
            -not $Cwd.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $null }
        $metadata = Get-PreparedWorktreesPath $watch['metadata']
        $state = Read-PostToolBoundedJson ([IO.Path]::Combine($metadata, 'request.json')) 262144
        if ($null -eq $state -or $state['version'] -ne 1 -or
            -not [string]::Equals($state['root'], $watch['root'], [StringComparison]::Ordinal)) { return $null }
        if ($state['phase'] -cin @('spawning', 'queued', 'running')) {
            if (-not (Test-PostToolWorkerGone $state['owner'])) { return $null }
            $result = @{ status = 'failed'; counts_known = $false }
        } else {
            if ($state['phase'] -cnotin @('done', 'failed')) { return $null }
            foreach ($value in @($watch['started_at'], $state['completed_at'])) {
                if (($value -isnot [int] -and $value -isnot [long] -and
                     $value -isnot [double] -and $value -isnot [decimal]) -or
                    [double]::IsNaN([double]$value) -or [double]::IsInfinity([double]$value)) { return $null }
            }
            if ($state['completed_at'] -lt $watch['started_at']) { return $null }
            $result = ConvertTo-PostToolFactMap $state['result']
        }
        $status = $result['status']
        if ($status -cnotin @('ready', 'partial', 'skipped', 'failed', 'timeout')) { return $null }
        if (-not (Remove-PostToolLateWatch $path $snapshot)) { return $null }
        $latest = $null
        $publication = Read-PostToolBoundedJson ([IO.Path]::Combine($metadata, 'publication.json')) 4096
        if ($null -ne $publication -and $publication['version'] -eq 1 -and
            [string]::Equals($publication['root'], $watch['root'], [StringComparison]::Ordinal) -and
            $publication['revision'] -is [string] -and
            $publication['revision'] -cmatch '^[0-9a-f]{32}$') { $latest = $publication['revision'] }
        $revision = if ($latest) { $latest } else { $result['publication_revision'] }
        $restart = $result['restart_required'] -is [bool] -and $result['restart_required']
        $changed = $restart -or ($revision -is [string] -and $revision -and
            -not [string]::Equals($revision, $watch['publication_revision'], [StringComparison]::Ordinal))
        if ($status -cin @('failed', 'timeout')) {
            if ($changed) {
                return '[local-guidance] Backup refresh failed after possible file changes. ' +
                    'Retain reviewed fallback and repair preparation. Restart/new context is required ' +
                    'before discovering refreshed guidance; file changes are not proof of model admission.'
            }
            return '[local-guidance] Backup refresh failed; retain reviewed fallback. Model admission is unknown.'
        }
        if ($changed) {
            $coverage = if ($status -ceq 'partial') { 'partially refreshed' } else { 'refreshed' }
            $description = if ($restart) {
                "Static guidance files $coverage after session discovery."
            } elseif ($status -ceq 'partial') {
                'Static guidance preparation is incomplete after a possible late file change.'
            } else { 'Static guidance was revalidated after a possible late file change.' }
            if ($latest -and -not [string]::Equals(
                $latest, $result['publication_revision'], [StringComparison]::Ordinal
            )) {
                $description = if ($status -ceq 'partial') {
                    'Static guidance preparation is incomplete; files may have changed after its last validation.'
                } else { 'Static guidance may have changed after its last validation; reprepare it first.' }
            }
            $fallback = if ($status -ceq 'partial') { 'Retain reviewed fallback for unresolved sources. ' } else { '' }
            return "[local-guidance] $description " +
                'Restart/new context is required to discover the refreshed guidance; ' +
                'this is file publication, not proof of model admission. ' +
                $fallback + 'Do not restart automatically; preserve current work first.'
        }
        if ($status -ceq 'partial') {
            return '[local-guidance] Backup refresh is incomplete; retain reviewed fallback. Model admission is unknown.'
        }
        return '[local-guidance] Backup verified unchanged guidance; no new model admission is asserted.'
    } catch { return $null }
}

function Get-PostToolPositiveInt {
    param([string]$Text, [int]$Default)
    $number = 0
    if ([int]::TryParse($Text, [ref]$number) -and $number -gt 0) { return $number }
    return $Default
}

function Get-PostToolNoteIdentity {
    param($Value)
    if ($Value -isnot [string]) { return $Value }
    $stamp = [DateTimeOffset]::MinValue
    if ([DateTimeOffset]::TryParse(
        $Value, [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$stamp
    )) {
        return $stamp.ToUniversalTime().ToString(
            "yyyy-MM-dd'T'HH:mm:ss.ffffff'+00:00'", [Globalization.CultureInfo]::InvariantCulture
        )
    }
    return $Value
}

function Enter-PostToolCounterLock {
    param([string]$Path)
    $stream = $null
    try {
        $null = Get-PreparedWorktreesPath $Path
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Path))
        $stream = [IO.File]::Open($Path, [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
        $clock = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            try {
                $stream.Lock(0, 1)
                return $stream
            } catch [IO.IOException] {
                if ($clock.Elapsed.TotalMilliseconds -ge 100) { break }
                [Threading.Thread]::Sleep(5)
            }
        }
    } catch {}
    if ($null -ne $stream) { $stream.Dispose() }
    return $null
}

function Send-PostToolReadDemand {
    param([Collections.IDictionary]$Discovery, [Collections.IDictionary]$Mutation, [switch]$Shell)
    $client = $null
    try {
        $capabilities = $Discovery.hook_capabilities
        if ($PSVersionTable.PSVersion.Major -ge 6 -and
            $capabilities -is [Newtonsoft.Json.Linq.JArray]) {
            $capabilities = @(
                foreach ($capability in $capabilities) {
                    if ($capability -is [Newtonsoft.Json.Linq.JValue]) { $capability.Value }
                }
            )
        }
        $batch = $null -ne $Mutation -and $Mutation.Contains('toolCalls')
        $capability = if ($Shell -and $batch) { 'mixed-hook-batch-mutation-v1' }
        elseif ($Shell) { 'shell-hook-mutation-v1' }
        elseif ($batch) { 'native-hook-batch-mutation-v1' }
        elseif ($null -ne $Mutation) { 'native-hook-mutation-v1' }
        else { 'read-hook-demand-v1' }
        if ($null -eq $Discovery -or $Discovery.hook_transport -cne 'tcp' -or
            $capabilities -isnot [array] -or
            $capabilities -cnotcontains $capability -or
            $Discovery.hook_generation -isnot [string] -or
            $Discovery.hook_generation -cnotmatch '^[0-9a-f]{32}$' -or
            $Discovery.hook_endpoint -isnot [string] -or
            $Discovery.hook_endpoint -cnotmatch '^127\.0\.0\.1:([0-9]{1,5})$' -or
            $Discovery.hook_token -isnot [string] -or
            $Discovery.hook_token -cnotmatch '^[A-Za-z0-9_-]{1,256}$') { return }
        $port = [int]($Discovery.hook_endpoint.Substring(10))
        if ($port -lt 1 -or $port -gt 65535) { return }
        if (-not [Net.Sockets.Socket]::OSSupportsIPv4) { return }
        $request = @{
            version = 1; kind = 'readHookDemand'; token = $Discovery.hook_token
            hook_generation = $Discovery.hook_generation
            deadline = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 + 1.0
        }
        if ($null -ne $Mutation) {
            $request.kind = if ($Shell -and $batch) { 'mixedToolBatchMutation' }
            elseif ($Shell) { 'shellToolMutation' }
            elseif ($batch) { 'nativeToolBatchMutation' } else { 'nativeToolMutation' }
            $request.payload = $Mutation
        }
        $bytes = ([Text.UTF8Encoding]::new($false)).GetBytes(
            (ConvertTo-PreparedWorktreesJson -InputObject $request -Depth 100) + "`n"
        )
        $limit = if ($null -ne $Mutation) { 65536 } else { 2048 }
        if ($bytes.Length -gt $limit) { return }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $client = [Net.Sockets.TcpClient]::new([Net.Sockets.AddressFamily]::InterNetwork)
        $remaining = 100 - [int]$clock.ElapsedMilliseconds
        if ($remaining -le 0) { return }
        if (-not $client.ConnectAsync([Net.IPAddress]::Loopback, $port).Wait($remaining)) { return }
        $remaining = 100 - [int]$clock.ElapsedMilliseconds
        if ($remaining -le 0) { return }
        $null = $client.GetStream().WriteAsync($bytes, 0, $bytes.Length).Wait($remaining)
        # A completed write is not receiver acceptance; never read, retry or replay.
    } catch {} finally {
        if ($null -ne $client) { $client.Dispose() }
    }
}

function Get-PostToolRuntimeRoot {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $pointer = $env:COPILOT_EXTENSIONS_CONTEXT
        $null = Get-PreparedWorktreesPath $pointer
        if ([IO.Path]::GetFileName($pointer) -cne 'install.json') { throw 'Invalid context pointer' }
        $runtime = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($pointer))
        if ([IO.Path]::GetFileName($runtime) -cne 'agent-worktrees' -or
            [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($runtime)) -cne 'plugins') {
            throw 'Invalid context layout'
        }
        # Normalization can erase an ancestor; retain the original literal-path checks.
        $ancestor = $pointer
        for ($depth = 0; $ancestor; $depth++) {
            if ($depth -ge 128) { throw 'Prepared context path exceeds its bound' }
            if ([IO.File]::GetAttributes($ancestor) -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Linked context path'
            }
            $ancestor = [IO.Path]::GetDirectoryName($ancestor)
        }
    } else {
        $registryHome = if ($env:AGENT_HOME -and $env:AGENT_HOME.Trim()) {
            $env:AGENT_HOME.Trim()
        } else { $env:USERPROFILE }
        $runtime = [IO.Path]::Combine($registryHome, '.agent-worktrees')
    }
    $null = Get-PreparedWorktreesPath $runtime
    return $runtime
}

function Test-PostToolDiscoveryContext {
    param([Collections.IDictionary]$Discovery, [string]$RuntimeRoot)
    if ($null -eq $Discovery) { return $false }
    if (-not $env:COPILOT_EXTENSIONS_CONTEXT) {
        return -not ($Discovery.installReceipt -or $Discovery.marketplaceId -or $Discovery.pluginRoot)
    }
    try {
        # Prepared admission already validated the SDK's exact cell layout and receipt bytes.
        $cell = [IO.Path]::GetDirectoryName([IO.Path]::GetDirectoryName($RuntimeRoot))
        return $Discovery.marketplaceId -is [string] -and
            [string]::Equals($Discovery.marketplaceId, [IO.Path]::GetFileName($cell), [StringComparison]::Ordinal) -and
            [string]::Equals(
                (Get-PreparedWorktreesPath $Discovery.installReceipt),
                (Get-PreparedWorktreesPath $env:COPILOT_EXTENSIONS_CONTEXT), [StringComparison]::Ordinal) -and
            [string]::Equals(
                (Get-PreparedWorktreesPath $Discovery.pluginRoot),
                (Get-PreparedWorktreesPath $RuntimeRoot), [StringComparison]::Ordinal)
    } catch { return $false }
}

function Invoke-PreparedPostToolRead {
    param([string]$Raw)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { return $null }
    try {
        if ($Raw.Length -gt 1048576) { return $null }
        $event = ConvertFrom-ManagedWorktreesJson $Raw
        $tool = $event['toolName']
        if (-not $tool) { $tool = $event['tool_name'] }
        $writeTools = @(
            'create', 'edit', 'str_replace', 'str_replace_editor',
            'str_replace_based_edit_tool', 'write', 'write_file', 'insert',
            'apply_patch', 'new_file', 'multi_edit'
        )
        $nativeBatch = $event.ContainsKey('toolCalls')
        $nativeWrite = $tool -is [string] -and $tool.ToLowerInvariant() -in $writeTools
        $shellTools = @(
            'bash', 'sh', 'shell', 'powershell', 'pwsh', 'cmd', 'run', 'run_command',
            'execute', 'exec', 'terminal'
        )
        $shellTool = -not $nativeBatch -and $tool -is [string] -and $tool.ToLowerInvariant() -in $shellTools
        if ($nativeBatch) {
            $calls = $event['toolCalls']
            if ($calls -isnot [Collections.IList] -or $calls.Count -lt 1 -or $calls.Count -gt 128) {
                return $null
            }
            foreach ($call in $calls) {
                if ($call -isnot [Collections.IDictionary] -or $call['name'] -isnot [string] -or
                    $call['name'].ToLowerInvariant() -notin ($writeTools + $shellTools + @('view', 'rg', 'glob'))) {
                    return $null
                }
                foreach ($key in $call.Keys) {
                    if ($key -cnotin @('name', 'args', 'id')) { return $null }
                }
                if ($call['name'].ToLowerInvariant() -in $shellTools) { $shellTool = $true }
            }
        } elseif ($tool -isnot [string] -or
            (-not $nativeWrite -and -not $shellTool -and $tool.ToLowerInvariant() -notin @('view', 'rg', 'glob'))) {
            return $null
        }
        if ($event.ContainsKey('sessionId')) { $session = $event['sessionId'] }
        else { $session = $env:COPILOT_AGENT_SESSION_ID }
        if ($session -isnot [string] -or
            $session -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { return ,@{} }
        $sessionHome = [IO.Path]::Combine($env:USERPROFILE, '.copilot', 'session-state', $session)
        if (-not (Test-PreparedManagedWorktreesSession -Raw $Raw)) {
            if ($env:COPILOT_EXTENSIONS_CONTEXT) { return $null }
            return ,@{}
        }
        if ($event.ContainsKey('cwd')) { $cwd = $event['cwd'] }
        elseif ($event.ContainsKey('workingDirectory')) { $cwd = $event['workingDirectory'] }
        else { $cwd = $env:COPILOT_PROJECT_DIR }
        $cwd = Get-PreparedWorktreesPath $cwd
        $runtime = Get-PostToolRuntimeRoot
        $discovery = Read-PostToolBoundedJson ([IO.Path]::Combine($runtime, 'status-monitor.lock')) 16384
        if (-not (Test-PostToolDiscoveryContext -Discovery $discovery -RuntimeRoot $runtime)) {
            $discovery = $null
        }
        if ($nativeWrite -or $nativeBatch -or $shellTool) {
            $mutation = @{ cwd = $cwd; sessionId = $session }
            if ($nativeBatch) { $mutation.toolCalls = $calls }
            else { $mutation.toolName = $tool }
            foreach ($name in @('cwd', 'workingDirectory')) {
                if ($event.ContainsKey($name)) { $mutation[$name] = $event[$name] }
            }
            foreach ($name in @('toolArgs', 'tool_input')) {
                if ($event.ContainsKey($name)) { $mutation[$name] = $event[$name] }
            }
            Send-PostToolReadDemand -Discovery $discovery -Mutation $mutation -Shell:$shellTool
        } else {
            Send-PostToolReadDemand -Discovery $discovery
        }
        $notice = Get-PostToolLateGuidanceNotice -SessionHome $sessionHome -Session $session -Cwd $cwd
        $result = Invoke-PreparedPostToolReminders -SessionHome $sessionHome -Session $session `
            -Cwd $cwd -SkipCounter:([bool]$notice) -Discovery $discovery
        if ($notice) {
            $text = if ($result.additionalContext) { $notice + "`n`n" + $result.additionalContext } else { $notice }
            return ,@{ additionalContext = $text }
        }
        return ,$result
    } catch {
        return ,@{}
    }
}

function Update-PostToolAgendaWindow {
    # Mirrors nudge_status._agenda_tick: an independent window, same thresholds.
    param([string]$StateHome, [string]$Worktree, [double]$Now)
    if (([string]$env:AGENT_WORKTREES_AGENDA_NUDGE).Trim().ToLowerInvariant() -in @('0', 'off', 'false', 'no')) {
        return $false
    }
    $sidecar = [IO.Path]::Combine($StateHome, $Worktree + '.agenda.json')
    $data = Read-PostToolBoundedJson $sidecar 4096
    if ($null -eq $data -or -not $data.ContainsKey('window_start') -or
        ($data['count'] -isnot [int] -and $data['count'] -isnot [long]) -or
        $data['count'] -lt 0 -or $data['count'] -eq [long]::MaxValue -or
        ($data['window_start'] -isnot [double] -and $data['window_start'] -isnot [decimal] -and
         $data['window_start'] -isnot [int] -and $data['window_start'] -isnot [long])) {
        $data = @{ count = 0; window_start = $Now }
    }
    $calls = [long]$data['count'] + 1
    $elapsed = ($Now - [double]$data['window_start']) / 60.0
    $due = $calls -ge (Get-PostToolPositiveInt $env:AGENT_WORKTREES_NUDGE_CALLS 25) -or
        $elapsed -ge (Get-PostToolPositiveInt $env:AGENT_WORKTREES_NUDGE_MINUTES 20)
    $next = if ($due) { @{ count = 0; window_start = $Now } }
    else { @{ count = $calls; window_start = [double]$data['window_start'] } }
    try {
        [void][IO.Directory]::CreateDirectory($StateHome)
        [IO.File]::WriteAllText($sidecar, (ConvertTo-PreparedWorktreesJson -InputObject $next))
    } catch { return $false }
    return $due
}

function Get-PostToolAgendaLaneState {
    # Mirrors nudge_status.agenda_lane_state with local, read-only evidence.
    param([string]$ProjectRoot, [string]$Worktree, [string]$Workspace)
    try {
        $descriptor = [IO.Path]::Combine($ProjectRoot, 'worktree-state', $Worktree, 'descriptor.json')
        if ([IO.File]::Exists($descriptor)) {
            $value = Read-PostToolBoundedJson $descriptor 1048576
            if ($null -eq $value) { return 'unknown' }
            $json = $PSVersionTable.PSVersion.Major -ge 6
            $placements = $value['placements']
            if ($json) {
                if ($placements -isnot [Newtonsoft.Json.Linq.JArray]) { return 'unknown' }
            } elseif ($placements -isnot [array]) { return 'unknown' }
            foreach ($placement in $placements) {
                $domain = $null
                if ($json -and $placement -is [Newtonsoft.Json.Linq.JObject]) {
                    $domain = $placement['domain']
                    if ($domain -is [Newtonsoft.Json.Linq.JValue]) { $domain = $domain.Value }
                } elseif ($placement -is [Collections.IDictionary]) {
                    $domain = $placement['domain']
                }
                if ($domain -is [string] -and $domain -ceq 'agendas') { return 'present' }
            }
        }
        $agendaHome = [IO.Path]::Combine($Workspace, '.effort-agenda')
        if ([IO.Directory]::Exists($agendaHome)) {
            foreach ($child in [IO.Directory]::EnumerateDirectories($agendaHome)) { return 'present' }
        }
        return 'absent'
    } catch {
        return 'unknown'
    }
}

function Get-PostToolAgendaReminder {
    # The owner publishes the policy at session start; no config/Python/RPC here.
    param([string]$StateHome, [string]$Worktree, [string]$ProjectRoot, [string]$Workspace)
    $record = Read-PostToolBoundedJson ([IO.Path]::Combine($StateHome, $Worktree + '.agenda-policy.json')) 4096
    if ($null -eq $record -or
        ($record['version'] -isnot [int] -and $record['version'] -isnot [long]) -or
        $record['version'] -ne 1 -or $record['policy'] -isnot [string] -or
        $record['policy'] -cne 'expected') { return $null }
    if ((Get-PostToolAgendaLaneState $ProjectRoot $Worktree $Workspace) -cne 'absent') { return $null }
    return (
        '[agent-worktrees] This project expects an agenda for this session ' +
        '(agendas.default: expected), but no agenda lane was found for this ' +
        'worktree. Adopt one via the `driving-effort-agendas` skill: ' +
        '`agenda_outline.py from-effort <effort README.md>` (or ' +
        '`agenda_backfill.py preview <session-folder>` to rebuild from this ' +
        'session), then `preview` and `adopt`. You own and edit the agenda; ' +
        'nothing is adopted automatically.'
    )
}

function Invoke-PreparedPostToolReminders {
    param(
        [string]$SessionHome, [string]$Session, [string]$Cwd, [switch]$SkipCounter,
        [Collections.IDictionary]$Discovery
    )
    try {
        $binding = Read-PostToolBoundedJson ([IO.Path]::Combine($sessionHome, 'worktree-binding.json')) 16384
        if ($null -eq $binding -or $binding['sessionId'] -isnot [string] -or
            -not [string]::Equals($binding['sessionId'], $session, [StringComparison]::Ordinal)) {
            return ,@{}
        }
        foreach ($name in @('project', 'worktreeId', 'creationNonce')) {
            if ($binding[$name] -isnot [string]) { return ,@{} }
        }
        $locator = ConvertTo-PostToolFactMap $binding['hookFacts'] @('version', 'projectRoot', 'workspace')
        if (($locator['version'] -isnot [int] -and $locator['version'] -isnot [long]) -or
            $locator['version'] -ne 1) { return ,@{} }
        $root = Get-PreparedWorktreesPath $locator['workspace']
        if (-not [string]::Equals($cwd, $root, [StringComparison]::Ordinal) -and
            -not $cwd.StartsWith($root + '\', [StringComparison]::Ordinal)) { return ,@{} }
        $null = Get-PreparedWorktreesPath $locator['projectRoot']
        $runtime = Get-PostToolRuntimeRoot
        if ($null -eq $discovery) { return ,@{} }
        $view = Read-PostToolHookFacts -Discovery $discovery -RuntimeRoot $runtime `
            -ProjectRoot $locator['projectRoot'] -Project $binding['project'] `
            -WorktreeId $binding['worktreeId'] -CreationNonce $binding['creationNonce'] `
            -Workspace $locator['workspace']
        if ($null -eq $view -or $view['version'] -ne 2) { return ,@{} }
        $facts = $view['facts']
        $worktree = $binding['worktreeId']
        $stateRuntime = if ($env:COPILOT_EXTENSIONS_CONTEXT) { $runtime } else {
            [IO.Path]::Combine($env:USERPROFILE, '.agent-worktrees')
        }
        $stateHome = [IO.Path]::Combine($stateRuntime, 'nudge-state')
        $sidecar = [IO.Path]::Combine($stateHome, $worktree + '.json')
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        $messages = [Collections.Generic.List[string]]::new()
        $counterLock = $null
        $agendaDue = $false
        try {
            $null = Get-PreparedWorktreesPath $sidecar
            if (-not $SkipCounter -and
                ([string]$env:AGENT_WORKTREES_NUDGE).Trim().ToLowerInvariant() -notin @('0', 'off', 'false', 'no')) {
                $counterLock = Enter-PostToolCounterLock ([IO.Path]::Combine($stateHome, $worktree + '.lock'))
                if ($null -eq $counterLock) {
                    # Contention is advisory; the independent bind reminder still runs.
                } elseif ($facts['status'] -in @('completed', 'finalized', 'removed', 'pruned')) {
                    try { [IO.File]::Delete($sidecar) } catch {}
                } else {
                    try { $agendaDue = Update-PostToolAgendaWindow $stateHome $worktree $now } catch {}
                    $data = Read-PostToolBoundedJson $sidecar 4096
                    $note = Get-PostToolNoteIdentity $facts['status_note_at']
                    if ($null -eq $data -or
                        -not [string]::Equals((Get-PostToolNoteIdentity $data['seen_note_at']), $note, [StringComparison]::Ordinal) -or
                        -not $data.ContainsKey('window_start')) {
                        $data = @{ count = 0; window_start = $now; seen_note_at = $note }
                    }
                    if (($data['count'] -isnot [int] -and $data['count'] -isnot [long]) -or
                        $data['count'] -lt 0 -or $data['count'] -eq [long]::MaxValue -or
                        ($data['window_start'] -isnot [double] -and $data['window_start'] -isnot [decimal] -and
                         $data['window_start'] -isnot [int] -and $data['window_start'] -isnot [long])) {
                        $data = $null
                    }
                    if ($null -ne $data) {
                        $calls = [long]$data['count'] + 1
                        $elapsed = ($now - [double]$data['window_start']) / 60.0
                        $callsThreshold = Get-PostToolPositiveInt $env:AGENT_WORKTREES_NUDGE_CALLS 25
                        $minutesThreshold = Get-PostToolPositiveInt $env:AGENT_WORKTREES_NUDGE_MINUTES 20
                        if ($calls -ge $callsThreshold -or $elapsed -ge $minutesThreshold) {
                            $minutes = [Math]::Max(1, [Math]::Round($elapsed))
                            $messages.Add(
                                "[agent-worktrees] It's been $calls tool call(s) / ~$minutes min since " +
                                "this worktree's Picker disposition was last written, and the focus or " +
                                'state may have moved. If so, refresh it: `agent-worktrees status ' +
                                '--activity "<current sub-task>"` most often (pass this whenever the ' +
                                'immediate focus shifts); add `--summary "<updated recap>"` only ' +
                                'occasionally, to fold newly completed work into the existing summary; ' +
                                'add `--title "<short headline>"` only rarely, when the worktree''s ' +
                                'main theme genuinely changed. Keep `--follow-up`/`--resolved` ' +
                                'accurate. If nothing consequential changed, ignore this.'
                            )
                            $data = @{ count = 0; window_start = $now; seen_note_at = $note }
                        } else { $data['count'] = $calls }
                        try {
                            [void][IO.Directory]::CreateDirectory($stateHome)
                            [IO.File]::WriteAllText($sidecar, (ConvertTo-PreparedWorktreesJson -InputObject $data))
                        } catch {}
                    }
                }
            }
        } catch {} finally {
            if ($null -ne $counterLock) { $counterLock.Dispose() }
        }
        if ($agendaDue) {
            try {
                $agenda = Get-PostToolAgendaReminder $stateHome $worktree $locator['projectRoot'] $locator['workspace']
                if ($agenda) { $messages.Add($agenda) }
            } catch {}
        }
        try {
            if (-not $facts['record_head_session']) {
                $stamp = [IO.Path]::Combine($locator['projectRoot'], 'worktrees', $worktree + '.bind-nudge-at')
                $null = Get-PreparedWorktreesPath $stamp
                $age = $now - ([IO.File]::GetLastWriteTimeUtc($stamp) - [datetime]'1970-01-01').TotalSeconds
                if (-not [IO.File]::Exists($stamp) -or $age -ge 90) {
                    try { [IO.File]::WriteAllText($stamp, $now.ToString([Globalization.CultureInfo]::InvariantCulture)) } catch {}
                    $quotedWorkspace = "'" + $locator['workspace'].Replace("'", "''") + "'"
                    $messages.Add(
                        '[agent-worktrees] This worktree has no session bound on record, but ' +
                        'you are working in it -- so its live tracking (status bar, picker) is ' +
                        'inactive. If you started here (or changed directory in) without being ' +
                        "registered, restore the binding by running:`n" +
                        "    agent-worktrees bind-session --worktree-dir=$quotedWorkspace`n" +
                        'This is a one-time declaration; once bound, this reminder stops.'
                    )
                }
            }
        } catch {}
        if ($messages.Count) { return ,@{ additionalContext = [string]::Join("`n`n", $messages) } }
        return ,@{}
    } catch {
        return ,@{}
    }
}
