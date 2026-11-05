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
    if ($map.Count -ne $Names.Count) { throw 'invalid fact fields' }
    foreach ($name in $Names) {
        if (-not $map.ContainsKey($name)) { throw 'invalid fact fields' }
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
            $Discovery.hook_facts_version -ne 1 -or
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
        $facts = ConvertTo-PostToolFactMap $value['facts'] @(
            'status', 'status_note_at', 'last_session_id', 'head_yielded'
        )
        if (($value['version'] -isnot [int] -and $value['version'] -isnot [long]) -or
            $value['version'] -ne 1 -or $value['quality'] -cne 'advisory' -or
            $value['completeness'] -cne 'partial' -or
            ($value['revision'] -isnot [int] -and $value['revision'] -isnot [long]) -or
            $value['revision'] -lt 1) { return $null }
        if ($scope['project'] -cne $Project -or $scope['worktree_id'] -cne $WorktreeId -or
            $scope['creation_nonce'] -cne $CreationNonce -or $scope['workspace'] -cne $Workspace -or
            $producer['runtime_root'] -cne $RuntimeRoot -or
            $producer['runtime_generation'] -cne $Discovery.runtime_generation -or
            $producer['hook_facts_generation'] -cne $Discovery.hook_facts_generation -or
            $producer['projection_generation'] -isnot [string] -or
            $producer['projection_generation'] -cnotmatch '^[0-9a-f]{32}$') { return $null }
        foreach ($name in @('marketplaceId', 'installReceipt', 'pluginRoot')) {
            if ($producer[$name] -cne $Discovery[$name]) { return $null }
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
