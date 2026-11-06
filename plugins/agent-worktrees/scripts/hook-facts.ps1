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
        $factsHome = Join-Path $ProjectRoot 'worktrees'
        foreach ($segment in @('', $WorktreeId, 'hook-facts')) {
            if ($segment) { $factsHome = Join-Path $factsHome $segment }
            $attributes = [IO.File]::GetAttributes($factsHome)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -or
                -not ($attributes -band [IO.FileAttributes]::Directory)) { return $null }
        }
        $path = Join-Path $factsHome ($Discovery.hook_facts_generation + '.json')
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
    param([string]$Path, [int]$Limit)
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

function Invoke-PreparedPostToolRead {
    param([string]$Raw)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        $env:COPILOT_EXTENSIONS_CONTEXT) { return $null }
    try {
        if ($Raw.Length -gt 1048576) { return ,@{} }
        $event = ConvertFrom-ManagedWorktreesJson $Raw
        $tool = $event['toolName']
        if (-not $tool) { $tool = $event['tool_name'] }
        if ($tool -isnot [string] -or $tool.ToLowerInvariant() -notin @('view', 'rg', 'glob')) {
            return $null
        }
        if ($event.ContainsKey('sessionId')) { $session = $event['sessionId'] }
        else { $session = $env:COPILOT_AGENT_SESSION_ID }
        if ($session -isnot [string] -or
            $session -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') { return ,@{} }
        $sessionHome = [IO.Path]::Combine($env:USERPROFILE, '.copilot', 'session-state', $session)
        $watch = [IO.Path]::Combine($sessionHome, 'local-guidance-watch.json')
        $null = Get-PreparedWorktreesPath $watch
        if ([IO.File]::Exists($watch)) { return $null }
        if (-not (Test-PreparedManagedWorktreesSession -Raw $Raw)) { return ,@{} }
        if ($event.ContainsKey('cwd')) { $cwd = $event['cwd'] }
        elseif ($event.ContainsKey('workingDirectory')) { $cwd = $event['workingDirectory'] }
        else { $cwd = $env:COPILOT_PROJECT_DIR }
        $cwd = Get-PreparedWorktreesPath $cwd
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
        $runtime = Get-ManagedWorktreesRuntimeRoot
        $discovery = Read-PostToolBoundedJson ([IO.Path]::Combine($runtime, 'status-monitor.lock')) 16384
        if ($null -eq $discovery) { return ,@{} }
        $view = Read-PostToolHookFacts -Discovery $discovery -RuntimeRoot $runtime `
            -ProjectRoot $locator['projectRoot'] -Project $binding['project'] `
            -WorktreeId $binding['worktreeId'] -CreationNonce $binding['creationNonce'] `
            -Workspace $locator['workspace']
        if ($null -eq $view -or $view['version'] -ne 2) { return ,@{} }
        $facts = $view['facts']
        $worktree = $binding['worktreeId']
        $stateHome = [IO.Path]::Combine($runtime, 'nudge-state')
        $sidecar = [IO.Path]::Combine($stateHome, $worktree + '.json')
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
        $messages = [Collections.Generic.List[string]]::new()
        $counterLock = $null
        try {
            $null = Get-PreparedWorktreesPath $sidecar
            if (([string]$env:AGENT_WORKTREES_NUDGE).Trim().ToLowerInvariant() -notin @('0', 'off', 'false', 'no')) {
                $counterLock = Enter-PostToolCounterLock ([IO.Path]::Combine($stateHome, $worktree + '.lock'))
                if ($null -eq $counterLock) {
                    # Contention is advisory; the independent bind reminder still runs.
                } elseif ($facts['status'] -in @('completed', 'finalized', 'removed', 'pruned')) {
                    try { [IO.File]::Delete($sidecar) } catch {}
                } else {
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
                            [IO.File]::WriteAllText($sidecar, (ConvertTo-Json -InputObject $data -Compress))
                        } catch {}
                    }
                }
            }
        } catch {} finally {
            if ($null -ne $counterLock) { $counterLock.Dispose() }
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
