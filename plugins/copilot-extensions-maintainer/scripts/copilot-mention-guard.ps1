# Native PowerShell port of copilot-mention-guard.py for the preToolUse hook.
#
# The hook invokes this file as a script block (no child process) with the raw
# hook payload and the plugin root. It must keep the Python guard's decisions
# and output contract: print one compact deny JSON line, or nothing. Any
# unexpected error propagates to the hook, which reports the "skipped" line.
# Keep this file ASCII-only and free of cmdlets that tests may shadow.
# Known deviation: JSON nesting deeper than 900 levels is treated as invalid
# input (Python's limit depends on its interpreter recursion limit).
param([string]$Raw, [string]$PluginRoot)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Off

$S = @{ Clock = [Diagnostics.Stopwatch]::StartNew(); BudgetMs = 1500.0 }
$IsWin = [IO.Path]::DirectorySeparatorChar -eq [char]92
$W = '[\s\x1c-\x1f]'

function Get-Remaining { return $S.BudgetMs - $S.Clock.Elapsed.TotalMilliseconds }
function Test-Expired { return (Get-Remaining) -le 0 }

function Lo([string]$t) {
    foreach ($ch in $t.ToCharArray()) { if ([int]$ch -gt 127) { return $t } }
    return $t.ToLowerInvariant()
}

function Test-Truthy($v) {
    if ($null -eq $v) { return $false }
    if ($v -is [bool]) { return $v }
    if ($v -is [string]) { return $v.Length -gt 0 }
    if ($v -is [long] -or $v -is [bigint]) { return $v -ne 0 }
    if ($v -is [double]) { return -not ($v -eq 0.0) }
    if ($v -is [Collections.ICollection]) { return $v.Count -gt 0 }
    return $true
}

function Get-Key($dict, [string]$key) {
    $value = $null
    if ($dict.TryGetValue($key, [ref]$value)) { return , $value }
    return $null
}

# ---------------------------------------------------------------- JSON parser
$JsonToken = [regex]::new('\G[ \t\n\r]*(?:(?<s>"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*")|(?<n>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?)|(?<p>[{}\[\]:,])|(?<k>true|false|null|NaN|Infinity|-Infinity))')
$JsonTail = [regex]::new('\G[ \t\n\r]*\z')
$JsonEscape = [regex]::new('\\(?:u([0-9a-fA-F]{4})|(["\\/bfnrt]))')
$JsonUnescape = [Text.RegularExpressions.MatchEvaluator] {
    param($m)
    if ($m.Groups[1].Success) { return [string][char][Convert]::ToInt32($m.Groups[1].Value, 16) }
    switch -CaseSensitive ($m.Groups[2].Value) {
        'b' { return [string][char]8 }
        'f' { return [string][char]12 }
        'n' { return "`n" }
        'r' { return "`r" }
        't' { return "`t" }
        default { return $m.Groups[2].Value }
    }
}

function ConvertFrom-JsonString([string]$token) {
    $inner = $token.Substring(1, $token.Length - 2)
    if ($inner.IndexOf([char]92) -lt 0) { return $inner }
    return $JsonEscape.Replace($inner, $JsonUnescape)
}

function ConvertFrom-JsonNumber([string]$text) {
    if ($text.IndexOfAny([char[]]'.eE') -ge 0) {
        $double = [double]0
        if ([double]::TryParse($text, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$double)) { return $double }
        # .NET Framework reports overflow as a parse failure; Python yields +/-inf.
        if ($text.StartsWith('-')) { return [double]::NegativeInfinity }
        return [double]::PositiveInfinity
    }
    $long = [long]0
    if ([long]::TryParse($text, [Globalization.NumberStyles]::AllowLeadingSign, [Globalization.CultureInfo]::InvariantCulture, [ref]$long)) { return $long }
    return [bigint]::Parse($text, [Globalization.CultureInfo]::InvariantCulture)
}

# Returns @{ v = value } on success or $null when the text is not valid JSON
# under Python json.loads rules (NaN/Infinity accepted, last duplicate wins).
function ConvertFrom-PyJson([string]$text) {
    if ($null -eq $text) { return $null }
    $pos = 0
    $stack = [Collections.Generic.List[object]]::new()
    $root = $null
    $haveRoot = $false
    # expect: 'value' | 'key' | 'colon' | 'after'
    $expect = 'value'
    $first = $false
    while ($true) {
        $m = $JsonToken.Match($text, $pos)
        if (-not $m.Success) { return $null }
        $pos = $m.Index + $m.Length
        $isValue = $false
        $value = $null
        if ($m.Groups['p'].Success) {
            $p = $m.Groups['p'].Value
            $frame = if ($stack.Count) { $stack[$stack.Count - 1] } else { $null }
            if ($p -eq '{' -or $p -eq '[') {
                if ($expect -ne 'value') { return $null }
                if ($stack.Count -ge 900) { return $null }
                if ($p -eq '{') {
                    $container = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
                    $stack.Add(@{ o = $container; k = $null })
                    $expect = 'key'
                } else {
                    $container = [Collections.Generic.List[object]]::new()
                    $stack.Add(@{ a = $container })
                    $expect = 'value'
                }
                $first = $true
                continue
            }
            if ($p -eq '}') {
                if ($null -eq $frame -or -not $frame.ContainsKey('o')) { return $null }
                if (-not (($expect -eq 'key' -and $first) -or $expect -eq 'after')) { return $null }
                $stack.RemoveAt($stack.Count - 1)
                $value = $frame.o
                $isValue = $true
            } elseif ($p -eq ']') {
                if ($null -eq $frame -or -not $frame.ContainsKey('a')) { return $null }
                if (-not (($expect -eq 'value' -and $first) -or $expect -eq 'after')) { return $null }
                $stack.RemoveAt($stack.Count - 1)
                $value = $frame.a
                $isValue = $true
            } elseif ($p -eq ':') {
                if ($expect -ne 'colon') { return $null }
                $expect = 'value'
                $first = $false
                continue
            } else {
                if ($expect -ne 'after' -or $null -eq $frame) { return $null }
                $expect = if ($frame.ContainsKey('o')) { 'key' } else { 'value' }
                $first = $false
                continue
            }
        } elseif ($m.Groups['s'].Success) {
            $str = ConvertFrom-JsonString $m.Groups['s'].Value
            if ($expect -eq 'key') {
                $stack[$stack.Count - 1].k = $str
                $expect = 'colon'
                continue
            }
            if ($expect -ne 'value') { return $null }
            $value = $str
            $isValue = $true
        } else {
            if ($expect -ne 'value') { return $null }
            if ($m.Groups['n'].Success) {
                $number = $m.Groups['n'].Value
                # Python's int() refuses more than 4300 digits, so json.loads fails.
                if ($number.IndexOfAny([char[]]'.eE') -lt 0 -and $number.TrimStart([char]45).Length -gt 4300) { return $null }
                $value = ConvertFrom-JsonNumber $number
            } else {
                switch -CaseSensitive ($m.Groups['k'].Value) {
                    'true' { $value = $true }
                    'false' { $value = $false }
                    'null' { $value = $null }
                    'NaN' { $value = [double]::NaN }
                    'Infinity' { $value = [double]::PositiveInfinity }
                    default { $value = [double]::NegativeInfinity }
                }
            }
            $isValue = $true
        }
        if ($isValue) {
            if ($stack.Count -eq 0) {
                $root = $value
                $haveRoot = $true
                break
            }
            $frame = $stack[$stack.Count - 1]
            if ($frame.ContainsKey('o')) { $frame.o[$frame.k] = $value } else { $frame.a.Add($value) }
            $expect = 'after'
            $first = $false
        }
    }
    if (-not $haveRoot -or -not $JsonTail.IsMatch($text, $pos)) { return $null }
    return @{ v = $root }
}

function Test-Dict($v) { return $v -is [Collections.Generic.Dictionary[string, object]] }

# ------------------------------------------------------------------ killswitch
function Test-KillswitchActive {
    $context = [Environment]::GetEnvironmentVariable('COPILOT_EXTENSIONS_CONTEXT')
    if ($context -and $context.Trim().Length) { return $false }
    $home_ = [Environment]::GetEnvironmentVariable('USERPROFILE')
    if (-not $IsWin -or -not $home_) { $home_ = [Environment]::GetEnvironmentVariable('HOME') }
    $override = [Environment]::GetEnvironmentVariable('PRETOOL_KILLSWITCH_STATE_FILE')
    if ($override) {
        if ($override.StartsWith('~')) {
            if (-not $home_) { return $false }
            $end = if ($IsWin) { $override.IndexOfAny([char[]]'/\') } else { $override.IndexOf('/') }
            if ($end -lt 0) { $end = $override.Length }
            if ($IsWin -and $end -ne 1) {
                $user = $override.Substring(1, $end - 1)
                if ($user -cne (Get-PyBasename $home_)) { $home_ = [IO.Path]::Combine([IO.Path]::GetDirectoryName($home_), $user) }
            }
            if ($IsWin -or $end -eq 1) { $override = $home_ + $override.Substring($end) }
        }
        if (-not (Test-AbsolutePath $override)) { return $false }
        $path = $override
    } else {
        if (-not $home_) { return $false }
        $path = [IO.Path]::Combine($home_, '.copilot-extensions', 'killSwitches', 'pretool-killswitch.json')
    }
    try {
        if (-not [IO.File]::Exists($path)) { return $false }
        $stream = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
            $buffer = [byte[]]::new(16385)
            $count = 0
            while ($count -lt $buffer.Length) {
                $read = $stream.Read($buffer, $count, $buffer.Length - $count)
                if ($read -le 0) { break }
                $count += $read
            }
        } finally { $stream.Dispose() }
        if ($count -gt 16384) { return $false }
        $offset = 0
        if ($count -ge 3 -and $buffer[0] -eq 0xEF -and $buffer[1] -eq 0xBB -and $buffer[2] -eq 0xBF) { $offset = 3 }
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($buffer, $offset, $count - $offset)
    } catch { return $false }
    $parsed = ConvertFrom-PyJson $text
    if ($null -eq $parsed -or -not (Test-Dict $parsed.v)) { return $false }
    $state = $parsed.v
    $active = Get-Key $state 'active'
    if ($active -isnot [bool]) { return $false }
    if ($state.ContainsKey('contract')) {
        $contract = Get-Key $state 'contract'
        if (-not (Test-Dict $contract)) { return $false }
        $id = Get-Key $contract 'id'
        if ($id -isnot [string] -or -not $id.Equals('copilot-extensions.pretool-killswitch', [StringComparison]::Ordinal)) { return $false }
        $version = Get-Key $contract 'version'
        if (-not (($version -is [long] -and $version -eq 1))) { return $false }
    }
    return $active
}

# ----------------------------------------------------------------- path rules
# pathlib.PurePath.is_absolute() (Python 3.12).
function Test-AbsolutePath([string]$p) {
    if (-not $p) { return $false }
    if (-not $IsWin) { return $p.StartsWith('/') }
    $parts = Split-NtRoot $p
    if ($parts[0] -and $parts[1]) { return $true }
    $drive = $parts[0].Replace('/', '\')
    if (-not $parts[1] -and $drive.StartsWith('\') -and -not $drive.EndsWith('\')) {
        $pieces = $drive.Split([char]92)
        if ($pieces.Count -eq 4 -and -not '?.'.Contains($pieces[2])) { return $true }
        if ($pieces.Count -eq 6) { return $true }
    }
    return $false
}
# Python 3.12 ntpath.splitroot(): returns drive, root, tail.
function Split-NtRoot([string]$p) {
    $n = $p.Replace('/', '\')
    if ($n.StartsWith('\\')) {
        $start = if ($n.Length -ge 8 -and $n.Substring(0, 8).ToUpperInvariant() -eq '\\?\UNC\') { 8 } else { 2 }
        $index = $n.IndexOf('\', $start)
        if ($index -lt 0) { return @($p, '', '') }
        $index2 = $n.IndexOf('\', $index + 1)
        if ($index2 -lt 0) { return @($p, '', '') }
        return @($p.Substring(0, $index2), $p.Substring($index2, 1), $p.Substring($index2 + 1))
    }
    if ($n.StartsWith('\')) { return @('', $p.Substring(0, 1), $p.Substring(1)) }
    if ($n.Length -ge 2 -and [int]$n[1] -eq 58) {
        if ($n.Length -ge 3 -and [int]$n[2] -eq 92) { return @($p.Substring(0, 2), $p.Substring(2, 1), $p.Substring(3)) }
        return @($p.Substring(0, 2), '', $p.Substring(2))
    }
    return @('', '', $p)
}

function Get-PyBasename([string]$token) {
    $t = $token.Replace('\', '/')
    if ($IsWin) { $t = (Split-NtRoot $t)[2] }
    $index = $t.LastIndexOfAny([char[]]'/\')
    if ($index -ge 0) { $t = $t.Substring($index + 1) }
    return $t
}

# pathlib's Path(cwd) / path for a relative path.
function Join-PyPath([string]$cwd, [string]$path) {
    if (-not $IsWin) {
        if ($path.StartsWith('/')) { return $path }
        if ($cwd.EndsWith('/')) { return $cwd + $path }
        return $cwd + '/' + $path
    }
    $pp = Split-NtRoot $path
    if ($pp[1]) {
        if ($pp[0]) { return $path }
        return (Split-NtRoot $cwd)[0] + $path
    }
    $cp = Split-NtRoot $cwd
    if ($pp[0] -and -not $pp[0].Equals($cp[0], [StringComparison]::OrdinalIgnoreCase)) { return $path }
    $base = $cwd
    $tail = $pp[2]
    if ($base.Length -and -not ($base.EndsWith('\') -or $base.EndsWith('/') -or ($base.Length -eq 2 -and [int]$base[1] -eq 58))) { $base += '\' }
    return $base + $tail
}

function Read-Bounded([string]$path, [bool]$strict, [string]$cwd) {
    try {
        if ($cwd -and -not (Test-AbsolutePath $path)) { $path = Join-PyPath $cwd $path }
        if (-not $path -or -not [IO.File]::Exists($path)) { return $null }
        if (Test-Expired) { return $null }
        $stream = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try {
            if ($stream.Length -gt 800004) { return $null }
            $buffer = [byte[]]::new([int]$stream.Length)
            $count = 0
            while ($count -lt $buffer.Length) {
                $read = $stream.Read($buffer, $count, $buffer.Length - $count)
                if ($read -le 0) { break }
                $count += $read
            }
        } finally { $stream.Dispose() }
        $encoding = [Text.UTF8Encoding]::new($false, $strict)
        $text = $encoding.GetString($buffer, 0, $count)
        $text = $text.Replace("`r`n", "`n").Replace("`r", "`n")
        if ($text.Length -gt 200000) {
            $points = $text.Length
            foreach ($ch in $text.ToCharArray()) { if ([char]::IsLowSurrogate($ch)) { $points-- } }
            if ($points -gt 200000) { return $null }
        }
        if (Test-Expired) { return $null }
        return $text
    } catch { return $null }
}

# ------------------------------------------------------------------ tokenizer
function Split-UnquotedNewlines([string]$command) {
    $lines = [Collections.Generic.List[string]]::new()
    $current = [Text.StringBuilder]::new()
    $quote = 0
    $escaped = $false
    foreach ($ch in $command.ToCharArray()) {
        $c = [int]$ch
        if ($escaped) { $null = $current.Append($ch); $escaped = $false; continue }
        if ($c -eq 92 -and $quote -ne 39) { $null = $current.Append($ch); $escaped = $true; continue }
        if ($quote) {
            $null = $current.Append($ch)
            if ($c -eq $quote) { $quote = 0 }
            continue
        }
        if ($c -eq 39 -or $c -eq 34) { $quote = $c; $null = $current.Append($ch); continue }
        if ($c -eq 10) { $lines.Add($current.ToString()); $null = $current.Clear(); continue }
        $null = $current.Append($ch)
    }
    $lines.Add($current.ToString())
    return , $lines
}

# Port of shlex.shlex(line, posix=True, punctuation_chars="();<>|&") with
# whitespace_split=True. Returns $null when shlex raises ValueError.
function Split-ShlexLine([string]$line) {
    $tokens = [Collections.Generic.List[string]]::new()
    $chars = $line.ToCharArray()
    $n = $chars.Length
    $i = 0
    while ($true) {
        $quoted = $false
        $escapedState = 32
        $state = 32
        $token = [Text.StringBuilder]::new()
        while ($true) {
            if ($i -ge $n) {
                if ($state -eq 39 -or $state -eq 34) { return $null }
                if ($state -eq 92) { return $null }
                break
            }
            $c = [int]$chars[$i]
            $i++
            $isWs = $c -eq 32 -or $c -eq 9 -or $c -eq 13 -or $c -eq 10
            $isPunct = $c -eq 40 -or $c -eq 41 -or $c -eq 59 -or $c -eq 60 -or $c -eq 62 -or $c -eq 124 -or $c -eq 38
            if ($state -eq 32) {
                if ($isWs) { if ($token.Length -or $quoted) { break } else { continue } }
                if ($c -eq 35) { $nl = $line.IndexOf([char]10, $i); $i = if ($nl -lt 0) { $n } else { $nl + 1 }; continue }
                if ($c -eq 92) { $escapedState = 97; $state = 92; continue }
                if ($isPunct) { $null = $token.Append([char]$c); $state = 99; continue }
                if ($c -eq 39 -or $c -eq 34) { $state = $c; continue }
                $null = $token.Append([char]$c); $state = 97; continue
            }
            if ($state -eq 39 -or $state -eq 34) {
                $quoted = $true
                if ($c -eq $state) { $state = 97; continue }
                if ($c -eq 92 -and $state -eq 34) { $escapedState = $state; $state = 92; continue }
                $null = $token.Append([char]$c); continue
            }
            if ($state -eq 92) {
                if (($escapedState -eq 39 -or $escapedState -eq 34) -and $c -ne 92 -and $c -ne $escapedState) { $null = $token.Append([char]92) }
                $null = $token.Append([char]$c)
                $state = $escapedState
                continue
            }
            # state 'a' (97) or 'c' (99)
            if ($isWs) { $state = 32; if ($token.Length -or $quoted) { break } else { continue } }
            if ($c -eq 35) {
                $nl = $line.IndexOf([char]10, $i)
                $i = if ($nl -lt 0) { $n } else { $nl + 1 }
                $state = 32
                if ($token.Length -or $quoted) { break } else { continue }
            }
            if ($state -eq 99) {
                if ($isPunct) { $null = $token.Append([char]$c); continue }
                $i--
                $state = 32
                break
            }
            if ($c -eq 39 -or $c -eq 34) { $state = $c; continue }
            if ($c -eq 92) { $escapedState = 97; $state = 92; continue }
            if ($isPunct) { $i--; $state = 32; if ($token.Length -or $quoted) { break } else { continue } }
            $null = $token.Append([char]$c)
        }
        if ($token.Length -eq 0 -and -not $quoted) { break }
        $tokens.Add($token.ToString())
    }
    return , $tokens
}

# ----------------------------------------------------------------- statements
$Separators = [Collections.Generic.HashSet[string]]::new([string[]]@(';', '&', '&&', '||', '|', "`n", '(', ')', '<', '>'), [StringComparer]::Ordinal)
$Assignment = [regex]::new('^[A-Za-z_][A-Za-z0-9_]*=')

function Remove-LeadingPrefixes($tokens) {
    $list = [Collections.Generic.List[string]]::new($tokens)
    while ($list.Count) {
        while ($list.Count -and $Assignment.IsMatch($list[0])) { $list.RemoveAt(0) }
        if (-not $list.Count) { break }
        $exe = Lo (Get-PyBasename $list[0])
        if ($exe -ceq 'env' -or $exe -ceq 'env.exe') {
            $list.RemoveAt(0)
            while ($list.Count -and ($list[0].StartsWith('-', [StringComparison]::Ordinal) -or $Assignment.IsMatch($list[0]))) { $list.RemoveAt(0) }
            continue
        }
        if ($exe -ceq 'command' -or $exe -ceq 'command.exe') {
            $list.RemoveAt(0)
            while ($list.Count -and $list[0].StartsWith('-', [StringComparison]::Ordinal)) { $list.RemoveAt(0) }
            continue
        }
        break
    }
    return , $list
}

function Split-Statements([string]$command) {
    $statements = [Collections.Generic.List[object]]::new()
    foreach ($line in (Split-UnquotedNewlines $command)) {
        $tokens = Split-ShlexLine $line
        if ($null -eq $tokens) { continue }
        $current = [Collections.Generic.List[string]]::new()
        foreach ($token in $tokens) {
            if ($Separators.Contains($token)) {
                if ($current.Count) { $statements.Add($current); $current = [Collections.Generic.List[string]]::new() }
                continue
            }
            $current.Add($token)
        }
        if ($current.Count) { $statements.Add($current) }
    }
    $result = [Collections.Generic.List[object]]::new()
    foreach ($statement in $statements) { $result.Add((Remove-LeadingPrefixes $statement)) }
    return , $result
}

function Test-Gh($tokens) {
    if (-not $tokens.Count) { return $false }
    $exe = Lo (Get-PyBasename $tokens[0])
    return $exe -ceq 'gh' -or $exe -ceq 'gh.exe'
}

$WritePairs = @('pr comment', 'issue comment', 'pr review', 'pr create', 'pr edit', 'issue create', 'issue edit')

function Test-PairIn($tokens, [string[]]$firsts, [string]$second) {
    for ($index = 1; $index -lt $tokens.Count - 1; $index++) {
        $a = Lo $tokens[$index]
        if ($firsts -ccontains $a -and (Lo $tokens[$index + 1]) -ceq $second) { return $true }
    }
    return $false
}

function Test-WriteSubcommand($tokens) {
    for ($index = 1; $index -lt $tokens.Count - 1; $index++) {
        $pair = (Lo $tokens[$index]) + ' ' + (Lo $tokens[$index + 1])
        if ($WritePairs -ccontains $pair) { return $true }
    }
    return $false
}

function Test-StartsWith([string]$s, [string]$prefix) { return $s.StartsWith($prefix, [StringComparison]::Ordinal) }
function Test-Contains([string]$s, [string]$part) { return $s.IndexOf($part, [StringComparison]::Ordinal) -ge 0 }

$FieldFlags = @('-f', '-F', '--field', '--raw-field')

function Test-ApiWrite($tokens) {
    $hasApi = $false
    for ($index = 1; $index -lt $tokens.Count; $index++) { if ((Lo $tokens[$index]) -ceq 'api') { $hasApi = $true; break } }
    if (-not $hasApi) { return $false }
    $hasBody = $false
    $hasInput = $false
    for ($index = 1; $index -lt $tokens.Count; $index++) {
        $token = $tokens[$index]
        if ($FieldFlags -ccontains $token -and $index + 1 -lt $tokens.Count -and (Test-StartsWith $tokens[$index + 1] 'body=')) { $hasBody = $true }
        elseif (((Test-StartsWith $token '-f') -or (Test-StartsWith $token '-F') -or (Test-StartsWith $token '--field=') -or (Test-StartsWith $token '--raw-field=')) -and (Test-Contains $token 'body=')) { $hasBody = $true }
        if ($token -ceq '--input' -or (Test-StartsWith $token '--input=')) { $hasInput = $true }
    }
    if (-not ($hasBody -or $hasInput)) { return $false }
    for ($index = 1; $index -lt $tokens.Count; $index++) {
        $token = $tokens[$index]
        if (Test-StartsWith $token '-') { continue }
        if ((Test-Contains $token '/comments') -or (Test-Contains $token '/reviews') -or (Test-Contains $token '/issues') -or (Test-Contains $token '/pulls')) { return $true }
    }
    return $false
}

function Get-PublicationStatements([string]$command) {
    $result = [Collections.Generic.List[object]]::new()
    foreach ($tokens in (Split-Statements $command)) {
        if ((Test-Gh $tokens) -and ((Test-WriteSubcommand $tokens) -or (Test-ApiWrite $tokens))) { $result.Add($tokens) }
    }
    return , $result
}

# --------------------------------------------------------------- body scanner
$Unscannable = [object]::new()
$Reference = [regex]::new('\$\{|\$\(|\$[A-Za-z_]|`')

function Get-InputJsonBody([string]$path, [string]$cwd) {
    if ($path -ceq '-') { return $Unscannable }
    $content = Read-Bounded $path $true $cwd
    if ($null -eq $content) { return $Unscannable }
    $parsed = ConvertFrom-PyJson $content
    if ($null -eq $parsed -or -not (Test-Dict $parsed.v)) { return $Unscannable }
    $body = Get-Key $parsed.v 'body'
    if ($body -is [string]) { return $body }
    return $Unscannable
}

function Get-BodyText($tokens, [string]$cwd) {
    $parts = [Collections.Generic.List[string]]::new()
    $count = $tokens.Count
    $index = 1
    while ($index -lt $count) {
        if (Test-Expired) { return $Unscannable }
        $token = $tokens[$index]
        $hasNext = $index + 1 -lt $count
        if (($token -ceq '--body' -or $token -ceq '-b') -and $hasNext) {
            $value = $tokens[$index + 1]
            if ($Reference.IsMatch($value)) { return $Unscannable }
            $parts.Add($value); $index += 2; continue
        }
        if ((Test-StartsWith $token '--body=') -or (Test-StartsWith $token '-b=')) {
            $value = $token.Substring($token.IndexOf('=') + 1)
            if ($Reference.IsMatch($value)) { return $Unscannable }
            $parts.Add($value); $index += 1; continue
        }
        if (($token -ceq '--body-file' -and $hasNext) -or (Test-StartsWith $token '--body-file=')) {
            if ($token -ceq '--body-file') { $value = $tokens[$index + 1]; $step = 2 } else { $value = $token.Substring(12); $step = 1 }
            if ($value -ceq '-') { return $Unscannable }
            $content = Read-Bounded $value $false $cwd
            if ($null -eq $content) { return $Unscannable }
            $parts.Add($content); $index += $step; continue
        }
        if ($token -ceq '--input' -and $hasNext) {
            $result = Get-InputJsonBody $tokens[$index + 1] $cwd
            if ([object]::ReferenceEquals($result, $Unscannable)) { return $Unscannable }
            $parts.Add($result); $index += 2; continue
        }
        if (Test-StartsWith $token '--input=') {
            $result = Get-InputJsonBody $token.Substring(8) $cwd
            if ([object]::ReferenceEquals($result, $Unscannable)) { return $Unscannable }
            $parts.Add($result); $index += 1; continue
        }
        $field = $null
        $consumed = 0
        if ($FieldFlags -ccontains $token -and $hasNext) {
            $field = $tokens[$index + 1]; $consumed = 2
        } else {
            foreach ($flag in $FieldFlags) {
                if (Test-StartsWith $token ($flag + '=')) { $field = $token.Substring($flag.Length + 1); $consumed = 1; break }
            }
        }
        if ($null -ne $field) {
            if (Test-StartsWith $field 'body=') {
                $value = $field.Substring(5)
                if ($value -ceq '@-') { return $Unscannable }
                if (Test-StartsWith $value '@') {
                    $content = Read-Bounded $value.Substring(1) $false $cwd
                    if ($null -eq $content) { return $Unscannable }
                    $parts.Add($content)
                } elseif ($Reference.IsMatch($value)) {
                    return $Unscannable
                } else {
                    $parts.Add($value)
                }
            }
            $index += $consumed
            continue
        }
        if (Test-StartsWith $token 'body=') {
            $priorShortField = $false
            for ($j = 0; $j -lt $index; $j++) { if ($tokens[$j] -ceq '-f') { $priorShortField = $true; break } }
            if (-not $priorShortField) {
                $value = $token.Substring(5)
                if ($Reference.IsMatch($value)) { return $Unscannable }
                $parts.Add($value)
            }
        }
        $index += 1
    }
    if (-not $parts.Count) {
        if (Test-PairIn $tokens @('pr', 'issue') 'create') { return $Unscannable }
        return [string]::Join(' ', [string[]]$tokens)
    }
    return [string]::Join("`n", [string[]]$parts)
}

$Mention = [regex]::new('@[cC][oO][pP][iI\u0131\u0130][lL][oO][tT]')

function Test-Mention([string]$text) {
    $m = $Mention.Match($text)
    while ($m.Success) {
        $next = $m.Index + $m.Length
        if ($next -ge $text.Length) { return $true }
        if ([int]$text[$next] -ne 45 -and [int]$text[$next] -ne 95) {
            $category = [int][Globalization.CharUnicodeInfo]::GetUnicodeCategory($text, $next)
            # UppercaseLetter..OtherLetter = 0..4, DecimalDigitNumber..OtherNumber = 8..10
            if (-not ($category -le 4 -or ($category -ge 8 -and $category -le 10))) { return $true }
        }
        $m = $Mention.Match($text, $m.Index + 1)
    }
    return $false
}

function Get-PublishReason($statements, [string]$cwd) {
    foreach ($tokens in $statements) {
        if (Test-Expired) { return 'unscannable' }
        $body = Get-BodyText $tokens $cwd
        if (Test-Expired) { return 'unscannable' }
        if ([object]::ReferenceEquals($body, $Unscannable)) { return 'unscannable' }
        if (Test-Mention $body) { return 'mention' }
    }
    return $null
}

# ------------------------------------------------------------ project scoping
function Get-LinkTarget([string]$path) {
    $info = [IO.DirectoryInfo]::new($path)
    if (-not $info.Exists) {
        $info = [IO.FileInfo]::new($path)
        if (-not $info.Exists) { return $null }
    }
    if (($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) { return $null }
    return [Microsoft.PowerShell.Commands.InternalSymbolicLinkLinkCodeMethods]::GetTarget([psobject]$info)
}

# Best-effort Path.resolve(): resolve symbolic links and junctions component
# by component; a missing component keeps the remaining path lexical.
function Resolve-RealPath([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    $hops = 0
    while ($true) {
        $root = [IO.Path]::GetPathRoot($full)
        $rest = $full.Substring($root.Length).Split([char[]]'/\', [StringSplitOptions]::RemoveEmptyEntries)
        $current = $root
        $restarted = $false
        for ($index = 0; $index -lt $rest.Count; $index++) {
            $candidate = [IO.Path]::Combine($current, $rest[$index])
            if (-not ([IO.Directory]::Exists($candidate) -or [IO.File]::Exists($candidate))) {
                for ($j = $index; $j -lt $rest.Count; $j++) { $current = [IO.Path]::Combine($current, $rest[$j]) }
                return $current
            }
            $target = $null
            try { $target = Get-LinkTarget $candidate } catch { $target = $null }
            if ($target -and $hops -lt 40) {
                $hops++
                if (-not [IO.Path]::IsPathRooted($target) -or ($IsWin -and -not (Test-AbsolutePath $target) -and -not $target.StartsWith('\'))) {
                    $target = [IO.Path]::Combine($current, $target)
                }
                $next = $target
                for ($j = $index + 1; $j -lt $rest.Count; $j++) { $next = [IO.Path]::Combine($next, $rest[$j]) }
                $full = [IO.Path]::GetFullPath($next)
                $restarted = $true
                break
            }
            $current = $candidate
        }
        if (-not $restarted) { return $current }
    }
}

function Test-AdoptingHarness($cwd) {
    $raw = if (Test-Truthy $cwd) { $cwd } else { [Environment]::GetEnvironmentVariable('COPILOT_PROJECT_DIR') }
    if ($raw -isnot [string] -or -not $raw) { return $false }
    if (-not (Test-AbsolutePath $raw)) { return $false }
    $directory = Resolve-RealPath $raw
    $prefix = 'copilot-extensions-maintainer@'
    while ($null -ne $directory -and $directory.Length) {
        if (Test-Expired) { return $false }
        $settings = [IO.Path]::Combine($directory, '.github', 'copilot')
        $enabled = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::Ordinal)
        $found = $false
        foreach ($name in @('settings.json', 'settings.local.json')) {
            $path = [IO.Path]::Combine($settings, $name)
            if (-not [IO.File]::Exists($path)) { continue }
            $found = $true
            $text = Read-Bounded $path $true $null
            if ($null -eq $text) { return $false }
            $parsed = ConvertFrom-PyJson $text
            if ($null -eq $parsed -or -not (Test-Dict $parsed.v)) { return $false }
            if ($parsed.v.ContainsKey('enabledPlugins')) {
                $plugins = Get-Key $parsed.v 'enabledPlugins'
                if (-not (Test-Dict $plugins)) { return $false }
                foreach ($pair in $plugins.GetEnumerator()) { $enabled[$pair.Key] = $pair.Value }
            }
        }
        if ($found) {
            foreach ($pair in $enabled.GetEnumerator()) {
                if ($pair.Key.Length -gt $prefix.Length -and (Test-StartsWith $pair.Key $prefix) -and $pair.Value -is [bool] -and $pair.Value) { return $true }
            }
            return $false
        }
        $git = [IO.Path]::Combine($directory, '.git')
        if ([IO.File]::Exists($git) -or [IO.Directory]::Exists($git)) { return $false }
        $directory = [IO.Path]::GetDirectoryName($directory)
    }
    return $false
}

$RepoUrl = [regex]::new('[gG][iI\u0131\u0130][tT][hH][uU][bB]\.[cC][oO][mM][:/]([^/\s\x1c-\x1f]+)/([^/\s\x1c-\x1f]+?)/?$')

function Get-GitHubRepo([string]$url) {
    $m = $RepoUrl.Match($url)
    if (-not $m.Success) { return $null }
    $owner = $m.Groups[1].Value
    $repo = $m.Groups[2].Value
    if ($repo.Length -ge 4 -and $repo.Substring($repo.Length - 4).ToLowerInvariant() -ceq '.git') { $repo = $repo.Substring(0, $repo.Length - 4) }
    return ($owner + '/' + $repo).Replace([string][char]0x130, "i$([char]0x307)").ToLowerInvariant()
}

function Get-ManifestTarget([string]$root) {
    $content = Read-Bounded ([IO.Path]::Combine($root, 'plugin.json')) $true $null
    if ($null -eq $content) { $manifest = [Collections.Generic.Dictionary[string, object]]::new() }
    else {
        $parsed = ConvertFrom-PyJson $content
        if ($null -eq $parsed) { return $null }
        $manifest = $parsed.v
    }
    if (-not (Test-Dict $manifest)) { throw 'plugin manifest is not an object' }
    foreach ($key in @('repository', 'homepage')) {
        $url = Get-Key $manifest $key
        if ($url -is [string]) {
            $repo = Get-GitHubRepo $url
            if ($repo) { return $repo }
        }
    }
    return $null
}

$RemoteLine = [regex]::new('^[\s\x1c-\x1f]*([^\s\x1c-\x1f]+)[\s\x1c-\x1f]+([^\s\x1c-\x1f]+)[\s\x1c-\x1f]+([^\s\x1c-\x1f].*)$')
$FetchField = [regex]::new('^\(fetch\)(?:[\s\x1c-\x1f]|$)')

function Get-RepoCandidates([string]$cwd) {
    $repos = [Collections.Generic.List[string]]::new()
    $remaining = Get-Remaining
    if ($remaining -le 0) { return , $repos }
    $process = $null
    try {
        $info = [Diagnostics.ProcessStartInfo]::new('git', 'remote --verbose')
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
        if ($cwd) { $info.WorkingDirectory = $cwd }
        foreach ($name in @('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')) {
            if ($info.EnvironmentVariables.ContainsKey($name)) { $info.EnvironmentVariables.Remove($name) }
        }
        $process = [Diagnostics.Process]::Start($info)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $null = $process.StandardError.ReadToEndAsync()
        $wait = [int][Math]::Max(1, [Math]::Ceiling((Get-Remaining)))
        if (-not $process.WaitForExit($wait)) {
            try { $process.Kill() } catch {}
            return , $repos
        }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { return , $repos }
        $output = $stdout.Result
    } catch {
        if ($null -ne $process) { try { if (-not $process.HasExited) { $process.Kill() } } catch {} }
        return , $repos
    } finally {
        if ($null -ne $process) { $process.Dispose() }
    }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($line in [regex]::Split($output, '\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]')) {
        $m = $RemoteLine.Match($line)
        if (-not $m.Success) { continue }
        $name = $m.Groups[1].Value
        if (-not $FetchField.IsMatch($m.Groups[3].Value) -or $seen.Contains($name)) { continue }
        $null = $seen.Add($name)
        $repo = Get-GitHubRepo $m.Groups[2].Value
        if ($repo) { $repos.Add($repo) }
    }
    return , $repos
}

# ----------------------------------------------------------------------- main
if (Test-KillswitchActive) { return }
$S.Clock.Restart()
if (-not $PluginRoot) { $PluginRoot = [Environment]::GetEnvironmentVariable('COPILOT_PLUGIN_ROOT') }
if (-not $PluginRoot -and $PSScriptRoot) { $PluginRoot = [IO.Path]::GetDirectoryName($PSScriptRoot) }

$parsed = ConvertFrom-PyJson $Raw
if ($null -eq $parsed -or -not (Test-Dict $parsed.v)) { return }
$data = $parsed.v
$toolName = Get-Key $data 'toolName'
if ($toolName -isnot [string] -or -not ($toolName -ceq 'bash' -or $toolName -ceq 'powershell')) { return }
$toolArgs = Get-Key $data 'toolArgs'
if (-not (Test-Truthy $toolArgs)) { $toolArgs = [Collections.Generic.Dictionary[string, object]]::new() }
if ($toolArgs -is [string]) {
    $reparsed = ConvertFrom-PyJson $toolArgs
    if ($null -eq $reparsed) { return }
    $toolArgs = $reparsed.v
}
$argsIsDict = Test-Dict $toolArgs
$command = ''
if ($argsIsDict -and $toolArgs.ContainsKey('command')) { $command = Get-Key $toolArgs 'command' }
$hookCwd = $null
$candidate = Get-Key $data 'cwd'
if (Test-Truthy $candidate) { $hookCwd = $candidate }
if ($null -eq $hookCwd) {
    $candidate = Get-Key $data 'workingDirectory'
    if (Test-Truthy $candidate) { $hookCwd = $candidate }
}
if ($null -eq $hookCwd -and $argsIsDict) {
    $candidate = Get-Key $toolArgs 'cwd'
    if (Test-Truthy $candidate) { $hookCwd = $candidate }
}
if ($null -eq $hookCwd) { $hookCwd = [Environment]::GetEnvironmentVariable('COPILOT_PROJECT_DIR') }
if ($command -isnot [string]) { return }

$statements = Get-PublicationStatements $command
if (-not $statements.Count) { return }
if (-not (Test-AdoptingHarness $hookCwd)) { return }
$target = Get-ManifestTarget $PluginRoot
if ($null -eq $target) { return }
if ($hookCwd -isnot [string]) { return }
if (-not ((Get-RepoCandidates $hookCwd) -ccontains $target)) { return }

$reason = Get-PublishReason $statements $hookCwd
if ($reason -ceq 'mention') {
    [Console]::Out.WriteLine('{"permissionDecision":"deny","permissionDecisionReason":"DENIED: an @copilot mention in a GitHub PR/issue comment or review does not nudge the review bot -- it delegates to the separate Copilot cloud coding agent, which will push its own unreviewed commits to the PR branch. The review bot already re-runs on every push; just push a commit for a fresh verdict, or post the comment without the mention.","decision":"deny","message":"DENIED: do not @-mention copilot in a PR/issue comment or review."}')
} elseif ($reason -ceq 'unscannable') {
    [Console]::Out.WriteLine('{"permissionDecision":"deny","permissionDecisionReason":"DENIED: this publishes a PR/issue comment or review body this guard cannot fully inspect within its decision budget (including stdin, unresolved input, or a slow file). Write the body to a local file (--body-file <path>) or pass it inline (--body \"...\") so it can be scanned for an @copilot mention.","decision":"deny","message":"DENIED: PR/issue body text cannot be fully scanned."}')
}