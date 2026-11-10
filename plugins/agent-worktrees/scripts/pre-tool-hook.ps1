# Native event entry; Python remains the policy path for uncertain input/cell admission.
if ($env:AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION -ceq 'resident') { exit 0 }
$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false, $true)

$script:preToolBoundary = 'entry'

function Write-PreToolUnavailable([string]$Boundary) {
    [Console]::Out.Write((@{ additionalContext = (
        '[agent-worktrees pre-tool] Boundary: ' + $Boundary + '. Effect: this component could not evaluate policy; missing evidence is not authorization. This hook did not execute the tool; independent policy decisions remain in force. Next: inspect the selected installation pre-tool files and existing repairing-worktrees guidance; use its catalog command read-only doctor only if available. Retry: do not repeat the tool solely for this warning; inspect its result first, and retry evaluation only after this boundary changes.'
    ) } | ConvertTo-Json -Compress))
}

function ConvertFrom-PreToolJson([string]$Text) {
    if (-not $Text.TrimStart().StartsWith('{')) { throw 'Expected a JSON object' }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        return ,(ConvertFrom-ManagedWorktreesJson $Text)
    }
    if (-not $script:preToolJson) {
        [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
        $script:preToolJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new()
    }
    return $script:preToolJson.DeserializeObject($Text)
}

function Read-PreToolJson([string]$Path, [int]$Limit) {
    $info = [IO.FileInfo]::new($Path)
    if (-not $info.Exists) { throw 'JSON file is unavailable' }
    if (($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $info.Length -gt $Limit) {
        throw 'JSON file is linked or exceeds its bound'
    }
    return ConvertFrom-PreToolJson ([IO.File]::ReadAllText($Path, $utf8))
}

function Get-NativePreToolStop {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { return $false }
    $path = $env:PRETOOL_KILLSWITCH_STATE_FILE
    if ($path -and $path -notmatch '^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+(?:[\\/]|$))') { return $null }
    if (-not $path) {
        $path = [IO.Path]::Combine($env:USERPROFILE, '.copilot-extensions\killSwitches\pretool-killswitch.json')
    }
    if (-not [IO.File]::Exists($path)) { return $false }
    try {
        $state = Read-PreToolJson $path 16384
        if ($state -isnot [Collections.IDictionary] -or $state.active -isnot [bool]) { return $null }
        if ($state.ContainsKey('contract')) {
            $contract = $state.contract
            if ($contract -isnot [Collections.IDictionary] -or $contract.id -isnot [string] -or
                $contract.id -cne 'copilot-extensions.pretool-killswitch' -or
                ($contract.version -isnot [int] -and $contract.version -isnot [long]) -or $contract.version -ne 1) { return $null }
        }
        return $state.active
    } catch { return $null }
}

function Test-NativePreToolSources([bool]$WriteProof = $false) {
    try {
        $policy = Read-PreToolJson ([IO.Path]::Combine($PSScriptRoot, 'hook-read-policy.json')) 4096
        if ($policy.schema -isnot [string] -or $policy.schema -cne 'agent-worktrees.native-read-policy' -or
            ($policy.version -isnot [int] -and $policy.version -isnot [long]) -or $policy.version -ne 1 -or
            $policy.guards -isnot [Collections.IDictionary]) { return $false }
        $names = @('statelessness_guard.py', 'cross_repo_guard.py', 'anchor_write_guard.py', 'pr_supersede_guard.py')
        if ($policy.guards.Count -ne $names.Count) { return $false }
        $sources = $policy.guards
        if ($WriteProof) {
            $dependencies = $policy.nativeWriteDependencies
            $extra = @('tool_call_forms.py', 'guarded_anchor_map.py')
            if ($dependencies -isnot [Collections.IDictionary] -or $dependencies.Count -ne $extra.Count) {
                return $false
            }
            $sources = @{}
            foreach ($guard in $names) { $sources[$guard] = $policy.guards[$guard] }
            foreach ($dependency in $extra) { $sources[$dependency] = $dependencies[$dependency] }
            $names += $extra
        }
        foreach ($guard in $names) {
            $expected = $sources[$guard]
            if ($expected -isnot [string] -or $expected -cnotmatch '^[0-9a-f]{64}$') { return $false }
            $path = [IO.Path]::Combine($PSScriptRoot, $guard)
            $info = [IO.FileInfo]::new($path)
            if (-not $info.Exists -or $info.Length -gt 131072 -or
                ($info.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
            # Exact normalized source binds the exemption across partial helper upgrades.
            $text = [IO.File]::ReadAllText($path, $utf8).Replace("`r`n", "`n")
            $hasher = [Security.Cryptography.SHA256]::Create()
            try {
                $actual = ([BitConverter]::ToString($hasher.ComputeHash($utf8.GetBytes($text)))).Replace('-', '').ToLowerInvariant()
            } finally { $hasher.Dispose() }
            if ($actual -cne $expected) { return $false }
        }
        return $true
    } catch { return $false }
}

function Test-NativePreToolRead([object]$Payload, [bool]$PreparedMembership = $false) {
    if (($env:COPILOT_EXTENSIONS_CONTEXT -and -not $PreparedMembership) -or
        $Payload -isnot [Collections.IDictionary]) { return $false }
    if ($Payload.ContainsKey('toolCalls')) { return $false }
    $name = $Payload.toolName
    if (-not $name) { $name = $Payload.tool_name }
    if ($name -isnot [string] -or $name -cnotin @('view', 'rg', 'glob')) { return $false }
    return Test-NativePreToolSources
}

function Read-NativePreToolText([string]$Path, [int]$Limit) {
    $null = Get-PreparedWorktreesPath $Path
    $stream = [IO.FileStream]::new(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    try {
        if ($stream.Length -gt $Limit) { throw 'Oversized native pre-tool facts' }
        $bytes = [byte[]]::new($Limit + 1)
        $count = 0
        while ($count -lt $bytes.Length) {
            $read = $stream.Read($bytes, $count, $bytes.Length - $count)
            if (-not $read) { break }
            $count += $read
        }
        if ($count -eq $bytes.Length) { throw 'Oversized native pre-tool facts' }
        return $utf8.GetString($bytes, 0, $count)
    } finally { $stream.Dispose() }
}

function Test-NativePreToolWrite([object]$Payload, [object]$Facts) {
    try {
        if ($Facts -isnot [Collections.IDictionary]) { return $false }
        $writeTools = @(
            'create', 'edit', 'str_replace', 'str_replace_editor', 'str_replace_based_edit_tool',
            'write', 'write_file', 'insert', 'new_file', 'multi_edit', 'apply_patch')
        # Source-bound tool_call_forms owns this header-target grammar.
        $patchTarget = '^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)\r?$'
        $writeArgs = [Collections.Generic.List[object]]::new()
        $cwd = $Payload.cwd
        if ($Payload.ContainsKey('toolCalls')) {
            # tool_call_forms gives batch members precedence over top-level tool fields.
            $calls = $Payload['toolCalls']
            if ($calls -isnot [Collections.IList] -or $calls.Count -lt 1 -or $calls.Count -gt 128) {
                return $false
            }
            foreach ($call in $calls) {
                if ($call -isnot [Collections.IDictionary] -or $call['name'] -isnot [string] -or
                    $call['name'] -cnotin ($writeTools + @('view', 'rg', 'glob'))) { return $false }
                foreach ($key in $call.Keys) {
                    if ($key -cnotin @('name', 'args', 'id')) { return $false }
                }
                if ($call['name'] -cin $writeTools) {
                    $writeArgs.Add(@{ name = $call['name']; args = $call['args'] })
                }
            }
            if (-not $Payload.ContainsKey('cwd')) { $cwd = $Payload.workingDirectory }
        } else {
            $name = $Payload.toolName
            if (-not $name) { $name = $Payload.tool_name }
            if ($name -isnot [string] -or $name -cnotin $writeTools) { return $false }
            $writeArgs.Add(@{ name = $name; args = $(if ($Payload.ContainsKey('toolArgs')) {
                $Payload.toolArgs
            } else { $Payload.tool_input }) })
        }
        if (-not $writeArgs.Count -or $cwd -isnot [string] -or
            (Get-PreparedWorktreesPath $cwd) -cne $Facts.workspace) { return $false }
        $root = $Facts.identity.root
        if (-not [IO.File]::Exists([IO.Path]::Combine($root, '.git'))) { return $false }
        # Membership proves enrollment; current Git bytes still prove this target's identity.
        $pointer = (Read-NativePreToolText ([IO.Path]::Combine($root, '.git')) 8192).TrimEnd("`n").TrimEnd("`r")
        if (-not $pointer.StartsWith('gitdir:', [StringComparison]::OrdinalIgnoreCase)) { return $false }
        $pointer = $pointer.Substring(7)
        if ($pointer.StartsWith(' ')) { $pointer = $pointer.Substring(1) }
        if (-not $pointer) { return $false }
        $gitDir = Get-PreparedWorktreesPath ([IO.Path]::Combine($root, $pointer))
        if ($gitDir -cne $Facts.identity.git_dir) { return $false }
        $common = (Read-NativePreToolText ([IO.Path]::Combine($gitDir, 'commondir')) 8192).TrimEnd("`n").TrimEnd("`r")
        if (-not $common -or (Get-PreparedWorktreesPath ([IO.Path]::Combine($gitDir, $common))) -cne
            $Facts.identity.common_dir -or -not [IO.File]::Exists([IO.Path]::Combine($gitDir, 'HEAD')) -or
            -not [IO.Directory]::Exists([IO.Path]::Combine($Facts.identity.common_dir, 'objects')) -or
            -not [IO.Directory]::Exists([IO.Path]::Combine($Facts.identity.common_dir, 'refs'))) { return $false }
        $rawPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($write in $writeArgs) {
            $args = $write.args
            if ($args -is [string]) {
                if ($write.name -ceq 'apply_patch' -and [regex]::IsMatch($args, $patchTarget, 'Multiline')) {
                    # A permissive JSON decoder must not discard headers inside comments.
                    $args = @{ command = $args }
                } else {
                    try { $args = ConvertFrom-PreToolJson $args }
                    catch {
                        if ($write.name -cne 'apply_patch') { return $false }
                        $args = @{ command = $args }
                    }
                }
            }
            if ($args -isnot [Collections.IDictionary]) { return $false }
            $entries = [Collections.Generic.List[object]]::new()
            $entries.Add($args)
            foreach ($key in @('edits', 'files', 'changes')) {
                if (-not $args.ContainsKey($key)) { continue }
                $values = $args[$key]
                if ($values -isnot [Collections.IList] -or $values.Count -gt 128) { return $false }
                foreach ($entry in $values) {
                    if ($entry -isnot [Collections.IDictionary]) { return $false }
                    foreach ($nested in @('edits', 'files', 'changes')) {
                        if ($entry.ContainsKey($nested)) { return $false }
                    }
                    $entries.Add($entry)
                }
            }
            $callPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            for ($index = 0; $index -lt $entries.Count; $index++) {
                $found = $false
                foreach ($key in @('path', 'file_path', 'filePath', 'filename', 'fileName', 'target_file', 'targetFile')) {
                    $raw = $entries[$index][$key]
                    if ($raw -isnot [string] -or -not $raw) { continue }
                    $found = $true
                    $null = $callPaths.Add($raw)
                    if ($callPaths.Count -gt 128) { return $false }
                }
                if ($index -gt 0 -and -not $found) { return $false }
            }
            if ($write.name -ceq 'apply_patch') {
                $foundPatch = $false
                foreach ($field in @('input', 'patch', 'command')) {
                    if (-not $args.ContainsKey($field)) { continue }
                    $patch = $args[$field]
                    if ($patch -isnot [string] -or $patch.Length -gt 131072 -or
                        $patch -cnotmatch '\A\*\*\* Begin Patch\r?\n[\s\S]*\r?\n\*\*\* End Patch(?:\r?\n)?\z') {
                        return $false
                    }
                    $headers = [regex]::Matches($patch, $patchTarget, 'Multiline')
                    if (-not $headers.Count) { return $false }
                    $foundPatch = $true
                    foreach ($header in $headers) {
                        $raw = $header.Groups[1].Value
                        if ($raw.EndsWith("`r")) { $raw = $raw.Substring(0, $raw.Length - 1) }
                        $null = $callPaths.Add($raw)
                        if ($callPaths.Count -gt 128) { return $false }
                    }
                }
                if (-not $foundPatch) { return $false }
            }
            if (-not $callPaths.Count) { return $false }
            foreach ($raw in $callPaths) {
                $null = $rawPaths.Add($raw)
                if ($rawPaths.Count -gt 128) { return $false }
            }
        }
        $paths = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($raw in $rawPaths) {
            if ($raw.Length -gt 4096 -or ($raw -notmatch '^[A-Za-z]:[\\/]' -and
                ($raw.Contains(':') -or $raw -match '^[\\/]'))) { return $false }
            $target = Get-PreparedWorktreesPath ([IO.Path]::Combine($cwd, $raw))
            if (-not $target.StartsWith($root + '\', [StringComparison]::Ordinal)) { return $false }
            $ancestor = $target
            while ($ancestor -cne $root) {
                foreach ($marker in @('.git', 'HEAD', 'objects', 'refs')) {
                    try {
                        $null = [IO.File]::GetAttributes([IO.Path]::Combine($ancestor, $marker))
                        return $false
                    } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
                    }
                }
                $ancestor = [IO.Path]::GetDirectoryName($ancestor)
            }
            $null = $paths.Add($target)
        }
        if (-not (Test-NativePreToolSources -WriteProof $true)) { return $false }
        $configPath = [IO.Path]::Combine($root, '.agent-worktrees', 'config.yaml')
        $null = Get-PreparedWorktreesPath $configPath
        $stateless = $false
        try {
            $attributes = [IO.File]::GetAttributes($configPath)
            if ($attributes -band [IO.FileAttributes]::Directory) { return $false }
            $text = (Read-NativePreToolText $configPath 16384).Replace("`r`n", "`n").Replace("`r", "`n")
            # Python's guard also recognizes the four information-separator whitespace characters.
            $stateless = [regex]::IsMatch($text,
                '^[\s\x1c-\x1f]*(?:stateless|requires_external_state_root)[\s\x1c-\x1f]*:[\s\x1c-\x1f]*true[\s\x1c-\x1f]*(?:#.*)?$', 'Multiline')
        } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
        }
        if ($stateless) {
            foreach ($target in $paths) {
                $relative = $target.Substring($root.Length + 1).Replace('\', '/').TrimStart([char[]]'./')
                if ($relative -match '^(?:efforts|logs|weekly-updates|icm)(?:/|$)' -or
                    [IO.Path]::GetFileName($relative) -cin @('ownership.yml', 'dev-assignments.yml')) {
                    return $false
                }
            }
        }
        $hash = [Security.Cryptography.SHA256]::Create()
        try {
            $key = ([BitConverter]::ToString($hash.ComputeHash($utf8.GetBytes($root)))).Replace('-', '').ToLowerInvariant()
        } finally { $hash.Dispose() }
        $map = ConvertFrom-PreToolJson (Read-NativePreToolText (
            [IO.Path]::Combine($Facts.registry, 'guarded-anchors', $key + '.json')) 1048576)
        if ($map -isnot [Collections.IDictionary]) { return $false }
        foreach ($field in @('schema', 'source_root', 'registry_root', 'inventory_authority', 'posture_authority')) {
            if ($map[$field] -isnot [string]) { return $false }
        }
        if ($map.schema -cne 'agent-worktrees.guarded-anchors' -or
            ($map.version -isnot [int] -and $map.version -isnot [long]) -or $map.version -ne 1 -or
            $map.source_root -cne $root -or $map.registry_root -cne $Facts.registry -or
            $map.inventory_authority -cne 'complete' -or $map.posture_authority -cne 'complete' -or
            $map.generation -isnot [string] -or -not $map.generation -or
            $map.source_identity -isnot [Collections.IDictionary] -or
            $map.roots -isnot [Collections.IList] -or $map.postures -isnot [Collections.IList] -or
            $map.posture_errors -isnot [Collections.IList] -or $map.posture_errors.Count -ne 0 -or
            $map.roots.Count + $map.postures.Count -gt 4096) { return $false }
        foreach ($field in @('root', 'git_dir', 'common_dir')) {
            if ($map.source_identity[$field] -isnot [string] -or
                $map.source_identity[$field] -cne $Facts.identity[$field]) { return $false }
        }
        foreach ($kind in @('roots', 'postures')) {
            foreach ($row in $map[$kind]) {
                if ($row -isnot [Collections.IDictionary] -or $row.identity -isnot [Collections.IDictionary]) {
                    return $false
                }
                foreach ($field in @('name', 'delegate', 'path')) {
                    if ($row[$field] -isnot [string] -or -not $row[$field]) { return $false }
                }
                foreach ($field in @('root', 'git_dir', 'common_dir')) {
                    $value = $row.identity[$field]
                    # Inventory identities are lexical snapshot data, not live paths to probe.
                    if ($value -isnot [string] -or $value.Length -gt 4096 -or
                        $value -cnotmatch '^[a-z]:\\' -or $value.Contains([char]0) -or
                        $value.Contains('/') -or $value.ToLowerInvariant() -cne $value) {
                        return $false
                    }
                    if ($value.Length -gt 3) {
                        foreach ($part in $value.Substring(3).Split('\')) {
                            if ($part -cin @('', '.', '..')) { return $false }
                        }
                    }
                }
                if ($row.path -cne $row.identity.root) { return $false }
                if ($kind -ceq 'postures' -and (
                    $row.posture -isnot [string] -or $row.posture -cnotin @('', 'reference', 'worktree') -or
                    $row.elevated -isnot [bool])) { return $false }
                if ($row.identity.common_dir -ceq $Facts.identity.common_dir -and (
                    $kind -ceq 'roots' -or $row.posture -cne 'worktree' -or $row.elevated -or
                    $row.identity.root -ceq $root)) { return $false }
            }
        }
        return $true
    } catch { return $false }
}

function Invoke-PreToolPolicy([string]$Python, [string]$Payload, [ref]$Diagnostic) {
    $worker = Join-Path $PSScriptRoot 'hook_policy_process.py'
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Python
    $info.Arguments = '-X utf8 -B "' + $worker + '" --base64-input'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $utf8
    $info.StandardErrorEncoding = $utf8
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $proc = [Diagnostics.Process]::Start($info)
    try {
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $stderr = $proc.StandardError.ReadToEndAsync()
        $frame = [Convert]::ToBase64String($utf8.GetBytes($Payload)) + "`n"
        $bytes = $utf8.GetBytes($frame)
        $writing = $proc.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
        if (-not $writing.Wait([Math]::Max(0, 28000 - [int]$clock.ElapsedMilliseconds)) -or
            -not $proc.StandardInput.BaseStream.FlushAsync().Wait([Math]::Max(0, 28000 - [int]$clock.ElapsedMilliseconds)) -or
            -not $proc.WaitForExit([Math]::Max(0, 28000 - [int]$clock.ElapsedMilliseconds)) -or
            -not [Threading.Tasks.Task]::WaitAll(@($stdout, $stderr), [Math]::Max(0, 28000 - [int]$clock.ElapsedMilliseconds))) {
            throw 'Pre-tool policy exceeded its owned deadline'
        }
        $global:LASTEXITCODE = $proc.ExitCode
        $Diagnostic.Value = $stderr.Result
        return $stdout.Result
    } finally {
        # EOF cancels the real SDK worker even behind a virtual-environment launcher.
        try { $proc.StandardInput.Close() } finally {
            if (-not $proc.HasExited) { $proc.Kill() }
            $proc.Dispose()
        }
    }
}

try {
    $reader = [IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8, $false)
    try { $raw = $reader.ReadToEnd() } finally { $reader.Dispose() }
    $admission = Join-Path $PSScriptRoot 'managed-session.ps1'
    if (-not [IO.File]::Exists($admission)) { exit 0 }
    . $admission
    $preparedMembership = $false
    $membershipFacts = $null
    $supportsFacts = $false
    foreach ($parameter in ${function:Test-ManagedWorktreesSession}.Ast.Body.ParamBlock.Parameters) {
        if ($parameter.Name.VariablePath.UserPath -ceq 'Facts') { $supportsFacts = $true; break }
    }
    $admitted = if ($supportsFacts) {
        Test-ManagedWorktreesSession -Raw $raw -Prepared ([ref]$preparedMembership) -Facts ([ref]$membershipFacts)
    } else {
        Test-ManagedWorktreesSession -Raw $raw -Prepared ([ref]$preparedMembership)
    }
    if (-not $admitted) { exit 0 }
    $stop = Get-NativePreToolStop
    if ($stop -eq $true) { exit 0 }
    $script:preToolBoundary = 'event JSON'
    $payload = ConvertFrom-PreToolJson $raw
    if ($payload -isnot [Collections.IDictionary]) { throw 'Expected an event object' }
    if ($stop -eq $false -and (Test-NativePreToolRead $payload $preparedMembership)) { exit 0 }
    if ($stop -eq $false -and $preparedMembership -and (Test-NativePreToolWrite $payload $membershipFacts)) { exit 0 }
    try { Set-Location -LiteralPath $env:USERPROFILE -ErrorAction Stop }
    catch { Set-Location -LiteralPath ([IO.Path]::GetPathRoot($PSScriptRoot)) -ErrorAction Stop }
    [IO.Directory]::SetCurrentDirectory((Get-Location).Path)
    $script:preToolBoundary = 'runtime selection'
    $python = $null
    $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
    $runtimeRoot = Join-Path $env:USERPROFILE '.agent-worktrees'
    if ($env:COPILOT_EXTENSIONS_CONTEXT) {
        $script:preToolBoundary = 'installation-context validation'
        $validator = Join-Path $PSScriptRoot 'installation-context\installation-context.ps1'
        if (-not [IO.File]::Exists($validator)) { throw 'Required installation-context validator is unavailable' }
        $owner = if ($env:AGENT_WORKTREES_PAYLOAD_ROOT) { $env:AGENT_WORKTREES_PAYLOAD_ROOT }
                 elseif ($env:COPILOT_PLUGIN_ROOT) { $env:COPILOT_PLUGIN_ROOT }
                 else { Split-Path -Parent $PSScriptRoot }
        $durableHome = $env:COPILOT_EXTENSIONS_CONTEXT
        1..5 | ForEach-Object { $durableHome = Split-Path -Parent $durableHome }
        $validation = @(& $validator -Action validate -Context $env:COPILOT_EXTENSIONS_CONTEXT `
            -DurableHome $durableHome -ExpectedPluginId 'agent-worktrees' -ExpectedPayloadRoot $owner)
        if ($LASTEXITCODE -ne 0) { throw 'Required installation context is invalid' }
        $context = ConvertFrom-PreToolJson ($validation -join "`n")
        if ($context -isnot [Collections.IDictionary] -or $context.pluginRoot -isnot [string] -or
            -not [IO.Path]::IsPathRooted($context.pluginRoot)) { throw 'Invalid installation-context runtime root' }
        $runtimeRoot = $context.pluginRoot
    }
    $script:preToolBoundary = 'runtime selection'
    if ([IO.File]::Exists($resolver)) {
        $previousRoot = $env:AGENT_RT_ROOT
        try {
            $env:AGENT_RT_ROOT = $runtimeRoot
            . $resolver -TraceMode read-only
            $python = $AwPy
        } finally {
            if ($null -eq $previousRoot) { Remove-Item Env:AGENT_RT_ROOT -ErrorAction SilentlyContinue }
            else { $env:AGENT_RT_ROOT = $previousRoot }
        }
    }
    if (-not $python -and $env:COPILOT_EXTENSIONS_CONTEXT) {
        throw 'Required installation-context runtime is unavailable'
    }
    if (-not $python) {
        $python = Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
            Select-Object -First 1 -ExpandProperty Source
    }
    $client = Join-Path $PSScriptRoot 'hook_policy_process.py'
    $script:preToolBoundary = 'policy interpreter or hook_policy_process.py'
    if (-not $python -or -not [IO.File]::Exists($client)) {
        throw 'Required pre-tool policy interpreter/client/runner is unavailable'
    }
    $script:preToolBoundary = 'policy worker execution'
    $diagnostic = ''
    $previousPythonEncoding = [Environment]::GetEnvironmentVariable('PYTHONIOENCODING', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'Process')
        $result = Invoke-PreToolPolicy $python $raw ([ref]$diagnostic)
    } finally {
        [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', $previousPythonEncoding, 'Process')
    }
    if ($LASTEXITCODE -ne 0) { throw "Pre-tool policy client exited $LASTEXITCODE" }
    $script:preToolBoundary = 'policy worker response'
    if (-not $result -or -not $result.Trim() -or $utf8.GetByteCount($result) -gt 65536) {
        throw 'Strict pre-tool policy returned no bounded response'
    }
    $response = ConvertFrom-PreToolJson $result
    if ($response -isnot [Collections.IDictionary]) { throw 'Invalid pre-tool policy response' }
    foreach ($name in $response.get_Keys()) {
        if ($name -cnotin @('permissionDecision', 'permissionDecisionReason', 'additionalContext')) {
            throw 'Invalid pre-tool policy response'
        }
    }
    if ($response.ContainsKey('permissionDecision') -and (
        $response.permissionDecision -isnot [string] -or
        $response.permissionDecision -cnotin @('allow', 'ask', 'deny')
    )) { throw 'Invalid pre-tool policy decision' }
    foreach ($name in @('permissionDecisionReason', 'additionalContext')) {
        if ($response.ContainsKey($name) -and $response[$name] -isnot [string]) {
            throw 'Invalid pre-tool policy response'
        }
    }
    if ($diagnostic) { [Console]::Error.Write($diagnostic) }
    [Console]::Out.Write($result)
} catch {
    [Console]::Error.WriteLine('agent-worktrees preToolUse: ' + $script:preToolBoundary + ' unavailable')
    Write-PreToolUnavailable $script:preToolBoundary
}
exit 0
