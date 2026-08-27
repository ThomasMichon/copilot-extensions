[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('source-id', 'resolve', 'validate', 'stamp')]
    [string]$Action,
    [string]$SourceJson,
    [string]$SourceFile,
    [string]$MarketplaceKey,
    [string]$PluginId,
    [string]$PayloadRoot,
    [string]$CopilotHome,
    [string]$ProjectRoot,
    [string]$DurableHome,
    [string]$Context,
    [string]$ExpectedMarketplaceId,
    [string]$ExpectedPluginId,
    [string]$ExpectedPayloadRoot,
    [string]$ExpectedCellRoot,
    [string]$PayloadVersion,
    [ValidateSet('', 'installed', 'directory', 'staged', 'explicit')]
    [string]$PayloadOrigin,
    [string]$PayloadOriginReceipt,
    [long]$ExpectedNamespaceGeneration = -1,
    [long]$ExpectedInstallGeneration = -1,
    [ValidateSet('active', 'inactive', 'orphaned', 'removing')]
    [string]$NamespaceState = 'active',
    [ValidateSet('active', 'inactive', 'orphaned', 'removing')]
    [string]$InstallState = 'active'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$utf8NoBom = New-Object Text.UTF8Encoding($false)
$strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
$OutputEncoding = $utf8NoBom
try { [Console]::OutputEncoding = $utf8NoBom } catch {}
$script:HeldLockPath = ''
$script:HeldLockToken = ''

if ($env:OS -eq 'Windows_NT' -and -not ('CeFinalPath' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

public static class CeFinalPath {
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const uint FILE_NAME_NORMALIZED = 0;
    private const uint VOLUME_NAME_DOS = 0;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle handle, StringBuilder path, uint size, uint flags);

    public static string Resolve(string path) {
        using (SafeFileHandle handle = CreateFile(
            path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            IntPtr.Zero, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS,
            IntPtr.Zero)) {
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            StringBuilder buffer = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(
                handle, buffer, (uint)buffer.Capacity,
                FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
            if (length == 0 || length >= buffer.Capacity) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            string result = buffer.ToString();
            if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                return @"\\" + result.Substring(8);
            }
            if (result.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                return result.Substring(4);
            }
            return result;
        }
    }
}
'@
}

if ($env:OS -ne 'Windows_NT' -and -not ('CePosixPath' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

public static class CePosixPath {
    [DllImport("libc", SetLastError = true)]
    private static extern IntPtr realpath(string path, IntPtr resolved);

    [DllImport("libc")]
    private static extern void free(IntPtr pointer);

    public static string Resolve(string path) {
        IntPtr pointer = realpath(path, IntPtr.Zero);
        if (pointer == IntPtr.Zero) {
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        try {
            return Marshal.PtrToStringAnsi(pointer);
        }
        finally {
            free(pointer);
        }
    }
}
'@
}

if (-not ('CeStrictJson' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;

public static class CeStrictJson {
    private sealed class Parser {
        private readonly string text;
        private int index;

        internal Parser(string value) {
            if (value == null) throw new ArgumentNullException("value");
            text = value;
        }

        internal void Parse() {
            SkipWhitespace();
            ParseValue();
            SkipWhitespace();
            if (index != text.Length) Error("trailing data");
        }

        private void ParseValue() {
            SkipWhitespace();
            if (index >= text.Length) Error("missing value");
            switch (text[index]) {
                case '{': ParseObject(); return;
                case '[': ParseArray(); return;
                case '"': ParseString(); return;
                case 't': ParseLiteral("true"); return;
                case 'f': ParseLiteral("false"); return;
                case 'n': ParseLiteral("null"); return;
                default:
                    if (text[index] == '-' || char.IsDigit(text[index])) {
                        ParseNumber();
                        return;
                    }
                    Error("unexpected value");
                    return;
            }
        }

        private void ParseObject() {
            index++;
            SkipWhitespace();
            var keys = new HashSet<string>(StringComparer.Ordinal);
            if (Consume('}')) return;
            while (true) {
                if (index >= text.Length || text[index] != '"') Error("expected object key");
                string key = ParseString();
                if (!keys.Add(key)) Error("duplicate object key '" + key + "'");
                SkipWhitespace();
                Require(':');
                ParseValue();
                SkipWhitespace();
                if (Consume('}')) return;
                Require(',');
                SkipWhitespace();
            }
        }

        private void ParseArray() {
            index++;
            SkipWhitespace();
            if (Consume(']')) return;
            while (true) {
                ParseValue();
                SkipWhitespace();
                if (Consume(']')) return;
                Require(',');
                SkipWhitespace();
            }
        }

        private string ParseString() {
            Require('"');
            var result = new System.Text.StringBuilder();
            while (index < text.Length) {
                char value = text[index++];
                if (value == '"') return result.ToString();
                if (value < 0x20) Error("unescaped control character");
                if (value != '\\') {
                    result.Append(value);
                    continue;
                }
                if (index >= text.Length) Error("unterminated escape");
                char escape = text[index++];
                switch (escape) {
                    case '"': result.Append('"'); break;
                    case '\\': result.Append('\\'); break;
                    case '/': result.Append('/'); break;
                    case 'b': result.Append('\b'); break;
                    case 'f': result.Append('\f'); break;
                    case 'n': result.Append('\n'); break;
                    case 'r': result.Append('\r'); break;
                    case 't': result.Append('\t'); break;
                    case 'u':
                        int code = ParseHex4();
                        if (code >= 0xD800 && code <= 0xDBFF) {
                            if (index + 1 >= text.Length || text[index] != '\\' ||
                                text[index + 1] != 'u') Error("unpaired high surrogate");
                            index += 2;
                            int low = ParseHex4();
                            if (low < 0xDC00 || low > 0xDFFF) Error("invalid low surrogate");
                            result.Append((char)code);
                            result.Append((char)low);
                        } else if (code >= 0xDC00 && code <= 0xDFFF) {
                            Error("unpaired low surrogate");
                        } else {
                            result.Append((char)code);
                        }
                        break;
                    default: Error("invalid string escape"); break;
                }
            }
            Error("unterminated string");
            return "";
        }

        private int ParseHex4() {
            if (index + 4 > text.Length) Error("invalid unicode escape");
            int result = 0;
            for (int offset = 0; offset < 4; offset++) {
                char value = text[index++];
                int digit;
                if (value >= '0' && value <= '9') digit = value - '0';
                else if (value >= 'a' && value <= 'f') digit = value - 'a' + 10;
                else if (value >= 'A' && value <= 'F') digit = value - 'A' + 10;
                else { Error("invalid unicode escape"); return 0; }
                result = (result * 16) + digit;
            }
            return result;
        }

        private void ParseNumber() {
            if (Consume('-') && index >= text.Length) Error("invalid number");
            if (Consume('0')) {
                if (index < text.Length && char.IsDigit(text[index])) Error("leading zero");
            } else {
                if (index >= text.Length || text[index] < '1' || text[index] > '9') {
                    Error("invalid number");
                }
                while (index < text.Length && char.IsDigit(text[index])) index++;
            }
            if (Consume('.')) {
                if (index >= text.Length || !char.IsDigit(text[index])) Error("invalid fraction");
                while (index < text.Length && char.IsDigit(text[index])) index++;
            }
            if (index < text.Length && (text[index] == 'e' || text[index] == 'E')) {
                index++;
                if (index < text.Length && (text[index] == '+' || text[index] == '-')) index++;
                if (index >= text.Length || !char.IsDigit(text[index])) Error("invalid exponent");
                while (index < text.Length && char.IsDigit(text[index])) index++;
            }
        }

        private void ParseLiteral(string value) {
            if (index + value.Length > text.Length ||
                string.CompareOrdinal(text, index, value, 0, value.Length) != 0) {
                Error("invalid literal");
            }
            index += value.Length;
        }

        private bool Consume(char value) {
            if (index < text.Length && text[index] == value) {
                index++;
                return true;
            }
            return false;
        }

        private void Require(char value) {
            if (!Consume(value)) Error("expected '" + value + "'");
        }

        private void SkipWhitespace() {
            while (index < text.Length) {
                char value = text[index];
                if (value != ' ' && value != '\t' && value != '\r' && value != '\n') return;
                index++;
            }
        }

        private void Error(string message) {
            throw new FormatException(message + " at character " + index);
        }
    }

    public static void Validate(string value) {
        new Parser(value).Parse();
    }
}
'@
}

if (-not ('CeAtomicFile' -as [type])) {
    Add-Type -TypeDefinition @'
using System.IO;

public static class CeAtomicFile {
    public static void Replace(string source, string destination) {
        if (File.Exists(destination)) {
            File.Replace(source, destination, null);
        } else {
            File.Move(source, destination);
        }
    }
}
'@
}

function Fail([string]$Message) {
    throw [System.InvalidOperationException]::new($Message)
}

function Get-UtcTimestamp {
    return [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ', [Globalization.CultureInfo]::InvariantCulture)
}

function Write-AtomicJson([string]$Path, $Value, [switch]$SkipLockCheck) {
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $temporary = Join-Path $directory ('.' + (Split-Path -Leaf $Path) + '.tmp-' + [guid]::NewGuid().ToString('N'))
    try {
        $json = $Value | ConvertTo-Json -Depth 12
        [IO.File]::WriteAllText($temporary, $json + "`n", $utf8NoBom)
        if (-not $SkipLockCheck -and $script:HeldLockPath) {
            Assert-LockOwned
        }
        [CeAtomicFile]::Replace($temporary, $Path)
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Read-LockOwner([string]$OwnerPath) {
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('installation-context-lock-' + [guid]::NewGuid().ToString('N') + '.json')
    try {
        Copy-Item -LiteralPath $OwnerPath -Destination $temporary -ErrorAction Stop
        return Read-Json $temporary
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Assert-LockOwnerShape(
    $Owner,
    [string]$Kind,
    [string]$MarketplaceIdValue,
    [string]$PluginIdValue
) {
    $version = Get-PropertyValue $Owner 'version'
    if (($version -isnot [byte] -and $version -isnot [int16] -and
         $version -isnot [int32] -and $version -isnot [int64]) -or
        (Get-StringProperty $Owner 'schema') -ne 'copilot-extensions.installation-lock' -or
        $version -ne 1 -or
        (Get-StringProperty $Owner 'kind') -ne $Kind -or
        (Get-StringProperty $Owner 'marketplaceId') -ne $MarketplaceIdValue -or
        (Get-StringProperty $Owner 'pluginId') -ne $PluginIdValue -or
        -not (Get-StringProperty $Owner 'token') -or
        -not (Get-StringProperty $Owner 'host')) {
        Fail 'Installation lock owner receipt is invalid.'
    }
    $ownerPid = Get-PropertyValue $Owner 'pid'
    if ($ownerPid -isnot [byte] -and $ownerPid -isnot [int16] -and
        $ownerPid -isnot [int32] -and $ownerPid -isnot [int64]) {
        Fail 'Installation lock pid must be an integer.'
    }
    if ([long]$ownerPid -lt 1) {
        Fail 'Installation lock pid must be at least 1.'
    }
}

function Assert-LockOwned {
    if (-not $script:HeldLockPath -or
        -not (Test-Path -LiteralPath $script:HeldLockPath -PathType Container)) {
        Fail 'Installation lock is not held.'
    }
    $owner = Read-LockOwner (Join-Path $script:HeldLockPath 'owner.json')
    if ((Get-StringProperty $owner 'token') -ne $script:HeldLockToken) {
        Fail "Installation lock '$script:HeldLockPath' ownership changed during mutation."
    }
}

function Acquire-Lock(
    [string]$Path,
    [string]$Kind,
    [string]$MarketplaceIdValue,
    [string]$PluginIdValue = ''
) {
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $token = [guid]::NewGuid().ToString('N')
    $hostName = [Environment]::MachineName.Split('.')[0].ToLowerInvariant()
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed -lt [TimeSpan]::FromSeconds(5)) {
        try {
            New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
            $script:HeldLockPath = $Path
            $script:HeldLockToken = $token
            $owner = [ordered]@{
                schema = 'copilot-extensions.installation-lock'
                version = 1
                kind = $Kind
                marketplaceId = $MarketplaceIdValue
                pluginId = $PluginIdValue
                token = $token
                host = $hostName
                pid = $PID
                acquiredAt = Get-UtcTimestamp
            }
            try {
                Write-AtomicJson (Join-Path $Path 'owner.json') $owner -SkipLockCheck
            }
            catch {
                Remove-Item -LiteralPath (Join-Path $Path 'owner.json') -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
                $script:HeldLockPath = ''
                $script:HeldLockToken = ''
                throw
            }
            return
        }
        catch [System.IO.IOException] {
            if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
                continue
            }
        }
        $ownerPath = Join-Path $Path 'owner.json'
        if (-not (Test-Path -LiteralPath $ownerPath -PathType Leaf)) {
            Start-Sleep -Milliseconds 10
            continue
        }
        try {
            $owner = Read-LockOwner $ownerPath
        }
        catch [System.Management.Automation.ItemNotFoundException] {
            continue
        }
        Assert-LockOwnerShape $owner $Kind $MarketplaceIdValue $PluginIdValue
        $ownerHost = Get-StringProperty $owner 'host'
        $ownerPid = [long](Get-PropertyValue $owner 'pid')
        if ($ownerHost -eq $hostName) {
            $ownerProcess = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
            if ($null -eq $ownerProcess) {
                Fail "Installation lock '$Path' has a stale owner (host=$ownerHost, pid=$ownerPid); explicit repair is required."
            }
            Start-Sleep -Milliseconds 10
            continue
        }
        Fail "Installation lock '$Path' is busy (host=$ownerHost, pid=$ownerPid)."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Path 'owner.json') -PathType Leaf)) {
        Fail "Installation lock '$Path' has no owner receipt; explicit repair is required."
    }
    Fail "Installation lock '$Path' remained busy."
}

function Release-Lock {
    Assert-LockOwned
    Remove-Item -LiteralPath (Join-Path $script:HeldLockPath 'owner.json') -Force
    Remove-Item -LiteralPath $script:HeldLockPath -Force
    $script:HeldLockPath = ''
    $script:HeldLockToken = ''
}

function Get-PropertyValue($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) { return $Default }
    $exact = $null
    foreach ($property in $Object.PSObject.Properties) {
        if ([string]::Equals($property.Name, $Name, [StringComparison]::Ordinal)) {
            $exact = $property
            continue
        }
        if ([string]::Equals($property.Name, $Name, [StringComparison]::OrdinalIgnoreCase)) {
            Fail "JSON property '$($property.Name)' conflicts with exact case '$Name'."
        }
    }
    if ($null -ne $exact) { return $exact.Value }
    return $Default
}

function Get-StringProperty($Object, [string]$Name, [string]$Default = '') {
    $value = Get-PropertyValue $Object $Name $Default
    if ($null -eq $value) { return $Default }
    if ($value -isnot [string]) {
        Fail "JSON field '$Name' must be a string."
    }
    if ($value.Contains([char]0)) {
        Fail "JSON field '$Name' may not contain NUL."
    }
    return $value
}

function Get-ReceiptTimestamp($Object, [string]$Name, [string]$Default) {
    $value = Get-PropertyValue $Object $Name $Default
    if ($value -is [DateTime]) {
        return $value.ToUniversalTime().ToString(
            'yyyy-MM-ddTHH:mm:ssZ',
            [Globalization.CultureInfo]::InvariantCulture
        )
    }
    if ($value -isnot [string]) {
        Fail "JSON field '$Name' must be a string."
    }
    return $value
}

function Canonical-Path([string]$Path, [switch]$MustExist) {
    if ([string]::IsNullOrWhiteSpace($Path)) { Fail 'A required path is empty.' }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    $fullPath = [IO.Path]::GetFullPath($expanded)
    if ($MustExist -and -not (Test-Path -LiteralPath $fullPath)) {
        Fail "Path does not exist: $Path"
    }
    $existing = $fullPath
    $tail = New-Object Collections.Generic.List[string]
    while (-not (Test-Path -LiteralPath $existing)) {
        $leaf = Split-Path -Leaf $existing
        if (-not $leaf) { break }
        $tail.Insert(0, $leaf)
        $parent = Split-Path -Parent $existing
        if (-not $parent -or $parent -eq $existing) { break }
        $existing = $parent
    }
    if (Test-Path -LiteralPath $existing) {
        if ($env:OS -eq 'Windows_NT') {
            try { $fullPath = [CeFinalPath]::Resolve($existing) }
            catch { Fail "Cannot resolve final Windows path '$existing': $($_.Exception.Message)" }
        }
        else {
            try { $fullPath = [CePosixPath]::Resolve($existing) }
            catch { Fail "Cannot resolve final POSIX path '$existing': $($_.Exception.Message)" }
        }
        foreach ($segment in $tail) { $fullPath = Join-Path $fullPath $segment }
        $fullPath = [IO.Path]::GetFullPath($fullPath)
    }
    $pathRoot = [IO.Path]::GetPathRoot($fullPath)
    if ($fullPath -eq $pathRoot) { return $fullPath }
    return $fullPath.TrimEnd('\', '/')
}

function Path-IsWithin([string]$Child, [string]$Parent) {
    $childPath = Canonical-Path $Child
    $parentPath = Canonical-Path $Parent
    if (Paths-Equal $childPath $parentPath) { return $true }
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    $prefix = $parentPath.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    return $childPath.StartsWith($prefix, $comparison)
}

function Paths-Equal([string]$Left, [string]$Right) {
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    return [string]::Equals(
        (Canonical-Path $Left),
        (Canonical-Path $Right),
        $comparison
    )
}

function Read-Json([string]$Path) {
    $canonical = Canonical-Path $Path -MustExist
    try {
        $bytes = [IO.File]::ReadAllBytes($canonical)
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
            $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
            Fail "UTF-8 BOM is not allowed in '$canonical'."
        }
        $text = $strictUtf8.GetString($bytes)
        [CeStrictJson]::Validate($text)
        return ($text | ConvertFrom-Json)
    }
    catch {
        Fail "Invalid JSON in '$canonical': $($_.Exception.Message)"
    }
}

function Read-SourceDescriptor {
    if ($SourceJson -and $SourceFile) {
        Fail 'Specify only one of -SourceJson and -SourceFile.'
    }
    if ($SourceFile) { return Read-Json $SourceFile }
    if ($SourceJson) {
        if ($SourceJson[0] -eq [char]0xFEFF) {
            Fail 'UTF-8 BOM is not allowed in -SourceJson.'
        }
        try {
            [CeStrictJson]::Validate($SourceJson)
            return ($SourceJson | ConvertFrom-Json)
        }
        catch { Fail "Invalid -SourceJson: $($_.Exception.Message)" }
    }
    return $null
}

function Normalize-GitPath([string]$Path) {
    $candidate = $Path.Replace('\', '/')
    if (-not $candidate.StartsWith('/')) { $candidate = "/$candidate" }
    $segments = New-Object Collections.Generic.List[string]
    foreach ($segment in $candidate.Split([char]'/', [StringSplitOptions]::None)) {
        if (-not $segment) {
            if ($segments.Count -gt 0) { $segments.Add('') }
            continue
        }
        if ($segment -eq '.') { continue }
        if ($segment -eq '..') {
            if ($segments.Count -gt 0) { $segments.RemoveAt($segments.Count - 1) }
            continue
        }
        $segments.Add($segment)
    }
    while ($segments.Count -gt 0 -and -not $segments[$segments.Count - 1]) {
        $segments.RemoveAt($segments.Count - 1)
    }
    return '/' + ($segments -join '/')
}

function Normalize-GitUrl([string]$Url) {
    foreach ($character in $Url.ToCharArray()) {
        $code = [int]$character
        if ($code -lt 32 -or $code -eq 127) {
            Fail 'A git source URL may not contain control characters.'
        }
    }
    if ([string]::IsNullOrWhiteSpace($Url)) { Fail 'A git source requires url.' }
    $candidate = $Url.Trim()
    if ([regex]::IsMatch($candidate, '%(?![0-9A-Fa-f]{2})')) {
        Fail 'Git URL has a malformed percent-escape.'
    }
    if ($candidate -match '^[^/@:]+@([^/:]+):(.+)$') {
        $candidate = "ssh://$($Matches[1])/$($Matches[2])"
    }
    $authorityMatch = [regex]::Match(
        $candidate,
        '^([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]+)'
    )
    if (-not $authorityMatch.Success) {
        Fail "Git URL must be absolute and include a host: $Url"
    }
    $scheme = $authorityMatch.Groups[1].Value.ToLowerInvariant()
    $authority = $authorityMatch.Groups[2].Value
    $at = $authority.LastIndexOf('@')
    if ($at -ge 0) { $authority = $authority.Substring($at + 1) }
    $hostCore = ''
    $rawPort = ''
    $portPresent = $false
    $bracketMatch = [regex]::Match($authority, '^(\[[^]]+\])(?::([0-9]*))?$')
    $hostMatch = [regex]::Match($authority, '^([^:]+)(?::([0-9]*))?$')
    if ($bracketMatch.Success) {
        $hostCore = $bracketMatch.Groups[1].Value.Trim('[', ']').ToLowerInvariant()
        $portPresent = $bracketMatch.Groups[2].Success
        $rawPort = $bracketMatch.Groups[2].Value
    }
    elseif ($hostMatch.Success) {
        $hostCore = $hostMatch.Groups[1].Value.ToLowerInvariant()
        $portPresent = $hostMatch.Groups[2].Success
        $rawPort = $hostMatch.Groups[2].Value
    }
    else {
        Fail "Invalid git URL '$Url'."
    }
    if ($hostCore.Contains(':')) {
        $parsedAddress = $null
        if ($hostCore -notmatch '^[0-9a-f:]+$' -or
            -not [Net.IPAddress]::TryParse($hostCore, [ref]$parsedAddress) -or
            $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetworkV6) {
            Fail "Git URL has an invalid host: $Url"
        }
    }
    elseif ($hostCore -notmatch '^[a-z0-9._-]+$') {
        Fail "Git URL has an invalid host: $Url"
    }
    $hostName = $hostCore
    if ($hostCore.Contains(':')) { $hostName = "[$hostCore]" }
    $port = ''
    if ($portPresent -and $rawPort) {
        [int]$portNumber = 0
        if (-not [int]::TryParse($rawPort, [ref]$portNumber) -or
            $portNumber -gt 65535) {
            Fail "Invalid git URL '$Url'."
        }
        if (-not (($scheme -eq 'http' -and $portNumber -eq 80) -or
                  ($scheme -eq 'https' -and $portNumber -eq 443))) {
            $port = ":$portNumber"
        }
    }
    try { $uri = [Uri]$candidate }
    catch { Fail "Invalid git URL '$Url'." }
    if (-not $uri.IsAbsoluteUri -or [string]::IsNullOrWhiteSpace($uri.Host)) {
        Fail "Git URL must be absolute and include a host: $Url"
    }
    $path = Normalize-GitPath ($uri.GetComponents(
        [UriComponents]::Path,
        [UriFormat]::UriEscaped
    ))
    $path = [regex]::Replace($path, '%([0-9A-Fa-f]{2})', {
        param($match)
        $value = [Convert]::ToInt32($match.Groups[1].Value, 16)
        $character = [char]$value
        if (($character -ge 'A' -and $character -le 'Z') -or
            ($character -ge 'a' -and $character -le 'z') -or
            ($character -ge '0' -and $character -le '9') -or
            $character -in @('-', '.', '_', '~')) {
            return [string]$character
        }
        return '%' + $match.Groups[1].Value.ToUpperInvariant()
    })
    if ($path.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
        $path = $path.Substring(0, $path.Length - 4)
    }
    return "$scheme`://$hostName$port$path"
}

function Normalize-Source(
    $Descriptor,
    [string]$BaseDirectory,
    [switch]$FromReceipt
) {
    if ($null -eq $Descriptor) { Fail 'A source descriptor is required.' }
    $kind = Get-StringProperty $Descriptor 'kind'
    if (-not $kind) { $kind = Get-StringProperty $Descriptor 'source' }
    $kind = $kind.Trim().ToLowerInvariant()
    if ($kind -eq 'local') { $kind = 'directory' }
    if ($kind -eq 'url') { $kind = 'git' }
    $ref = Get-StringProperty $Descriptor 'ref'
    $canonicalInput = Get-StringProperty $Descriptor 'canonical'
    if ($FromReceipt -and -not $canonicalInput) {
        Fail 'A receipt source requires canonical identity.'
    }
    $canonical = ''

    switch ($kind) {
        'github' {
            if ($canonicalInput) {
                if (-not $canonicalInput.StartsWith('github:', [StringComparison]::Ordinal)) {
                    Fail "Invalid canonical GitHub source '$canonicalInput'."
                }
                $repo = $canonicalInput.Substring(7)
            }
            else {
                $repo = Get-StringProperty $Descriptor 'repo'
                if (-not $repo) { $repo = Get-StringProperty $Descriptor 'url' }
            }
            $repo = $repo.Trim()
            $repo = $repo -replace '^(?i:https?://github\.com/)', ''
            $repo = $repo -replace '^(?i:ssh://git@github\.com/)', ''
            $repo = $repo -replace '^(?i:git@github\.com:)', ''
            $repo = $repo.Trim('/')
            if ($repo.EndsWith('.git', [StringComparison]::OrdinalIgnoreCase)) {
                $repo = $repo.Substring(0, $repo.Length - 4)
            }
            if ($repo -notmatch '^[^/]+/[^/]+$') {
                Fail "GitHub source requires owner/repository, got '$repo'."
            }
            # GitHub owner/repository identity is case-insensitive.
            $repo = $repo.ToLowerInvariant()
            $canonical = "github:$repo"
        }
        'git' {
            if ($canonicalInput) {
                if (-not $canonicalInput.StartsWith('git:', [StringComparison]::Ordinal)) {
                    Fail "Invalid canonical git source '$canonicalInput'."
                }
                $gitUrl = $canonicalInput.Substring(4)
            }
            else {
                $gitUrl = Get-StringProperty $Descriptor 'url'
            }
            $canonical = "git:$(Normalize-GitUrl $gitUrl)"
        }
        'opaque' {
            if ($canonicalInput) {
                $canonical = $canonicalInput
            }
            else {
                $opaqueId = Get-StringProperty $Descriptor 'id'
                if (-not $opaqueId) { $opaqueId = Get-StringProperty $Descriptor 'value' }
                if ([string]::IsNullOrWhiteSpace($opaqueId)) {
                    Fail 'An opaque source requires a non-empty id.'
                }
                $canonical = "opaque:$opaqueId"
            }
            if (-not $canonical.StartsWith('opaque:', [StringComparison]::Ordinal)) {
                Fail "Invalid canonical opaque source '$canonical'."
            }
        }
        'directory' {
            $stableId = (Get-StringProperty $Descriptor 'stableId').Trim()
            if ($canonicalInput) {
                if ($canonicalInput.StartsWith('directory-id:', [StringComparison]::Ordinal)) {
                    $receiptStableId = $canonicalInput.Substring(13).Trim()
                    if (-not $receiptStableId) {
                        Fail 'A canonical directory-id source requires a non-empty id.'
                    }
                    $canonical = "directory-id:$receiptStableId"
                }
                elseif ($canonicalInput.StartsWith('directory:', [StringComparison]::Ordinal)) {
                    $receiptDirectory = $canonicalInput.Substring(10)
                    if ($FromReceipt) {
                        $canonical = "directory:$(Canonical-Path $receiptDirectory)"
                    }
                    else {
                        $canonical = "directory:$(Canonical-Path $receiptDirectory -MustExist)"
                    }
                }
                else {
                    Fail "Invalid canonical directory source '$canonicalInput'."
                }
            }
            elseif ($stableId) {
                $canonical = "directory-id:$stableId"
            }
            else {
                $directoryPath = Get-StringProperty $Descriptor 'path'
                if ([string]::IsNullOrWhiteSpace($directoryPath)) {
                    Fail 'A directory source requires a non-empty path or stableId.'
                }
                if (-not [IO.Path]::IsPathRooted($directoryPath)) {
                    if (-not $BaseDirectory) {
                        Fail 'A relative directory source requires a declaration base directory.'
                    }
                    $directoryPath = Join-Path $BaseDirectory $directoryPath
                }
                $canonical = "directory:$(Canonical-Path $directoryPath -MustExist)"
            }
            if (-not ($canonical.StartsWith('directory:', [StringComparison]::Ordinal) -or
                      $canonical.StartsWith('directory-id:', [StringComparison]::Ordinal))) {
                Fail "Invalid canonical directory source '$canonical'."
            }
        }
        default { Fail "Unsupported source kind '$kind'." }
    }

    return [pscustomobject][ordered]@{
        kind = $kind
        canonical = $canonical
        ref = $ref
    }
}

function Source-Record($Source) {
    $builder = New-Object Text.StringBuilder
    foreach ($field in @(
        @('version', '1'),
        @('kind', [string]$Source.kind),
        @('source', [string]$Source.canonical),
        @('ref', [string]$Source.ref)
    )) {
        $length = [Text.Encoding]::UTF8.GetByteCount([string]$field[1])
        [void]$builder.Append("$($field[0]):$length`:$($field[1])`n")
    }
    return $builder.ToString()
}

function Readable-Slug([string]$Value) {
    $builder = New-Object Text.StringBuilder
    $previousDash = $false
    foreach ($character in $Value.ToCharArray()) {
        $code = [int]$character
        if ($code -ge 65 -and $code -le 90) {
            $character = [char]($code + 32)
            $code += 32
        }
        if (($code -ge 97 -and $code -le 122) -or
            ($code -ge 48 -and $code -le 57)) {
            [void]$builder.Append($character)
            $previousDash = $false
        }
        elseif ($builder.Length -gt 0 -and -not $previousDash) {
            [void]$builder.Append('-')
            $previousDash = $true
        }
    }
    $slug = $builder.ToString().Trim('-')
    if (-not $slug) { return 'marketplace' }
    return $slug
}

function Source-Identity($Source, [string]$ReadableName) {
    $record = Source-Record $Source
    $bytes = [Text.Encoding]::UTF8.GetBytes($record)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
    $slug = Readable-Slug $ReadableName
    return [pscustomobject][ordered]@{
        kind = $Source.kind
        canonical = $Source.canonical
        ref = $Source.ref
        record = $record
        sha256 = $digest
        fingerprint = "sha256:$digest"
        marketplaceId = "$slug--$($digest.Substring(0, 16))"
    }
}

function Get-DefaultHome([string]$Child) {
    $userHomePath = $env:USERPROFILE
    if (-not $userHomePath) { $userHomePath = $HOME }
    if (-not $userHomePath) { Fail 'Cannot determine the user home.' }
    return Join-Path $userHomePath $Child
}

function Get-Declarations([string]$Key, [string]$ResolvedCopilotHome, [string]$ResolvedProjectRoot) {
    $declarations = @()
    $settingsPaths = @(
        [pscustomobject]@{
            path = (Join-Path $ResolvedCopilotHome 'settings.json')
            label = 'user'
            baseDirectory = $ResolvedCopilotHome
        },
        [pscustomobject]@{
            path = (Join-Path $ResolvedCopilotHome 'settings.local.json')
            label = 'user-local'
            baseDirectory = $ResolvedCopilotHome
        }
    )
    if ($ResolvedProjectRoot) {
        foreach ($relativeSettings in @(
            '.claude/settings.json',
            '.claude/settings.local.json',
            '.github/copilot/settings.json',
            '.github/copilot/settings.local.json'
        )) {
            $settingsPaths += [pscustomobject]@{
                path = (Join-Path $ResolvedProjectRoot $relativeSettings)
                label = "project:$ResolvedProjectRoot"
                baseDirectory = $ResolvedProjectRoot
            }
        }
    }
    foreach ($entry in $settingsPaths) {
        if (-not (Test-Path -LiteralPath $entry.path -PathType Leaf)) { continue }
        $settings = Read-Json $entry.path
        $marketplaces = Get-PropertyValue $settings 'extraKnownMarketplaces'
        if ($null -eq $marketplaces) { continue }
        $declaration = Get-PropertyValue $marketplaces $Key
        if ($null -eq $declaration) { continue }
        $descriptor = Get-PropertyValue $declaration 'source'
        if ($null -eq $descriptor) { Fail "Marketplace '$Key' has no source in '$($entry.path)'." }
        $source = Normalize-Source $descriptor $entry.baseDirectory
        $declarations += [pscustomobject]@{
            source = $source
            declaredIn = $entry.label
            settingsPath = Canonical-Path $entry.path
        }
    }
    if ($declarations.Count -eq 0) {
        Fail "No user or explicit project extraKnownMarketplaces declaration found for installed key '$Key'."
    }
    $distinct = @($declarations | Group-Object { "$($_.source.kind)`n$($_.source.canonical)`n$($_.source.ref)" })
    if ($distinct.Count -ne 1) {
        $locations = ($declarations | ForEach-Object { $_.settingsPath }) -join ', '
        Fail "Conflicting declarations for marketplace key '$Key' in: $locations. Supply explicit management provenance."
    }
    return $declarations
}

function Resolve-InstalledEvidence(
    [string]$ResolvedPayload,
    [string]$ResolvedCopilotHome,
    [string]$ResolvedProjectRoot
) {
    $installed = Canonical-Path (Join-Path $ResolvedCopilotHome 'installed-plugins')
    $prefix = $installed + [IO.Path]::DirectorySeparatorChar
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    if (-not $ResolvedPayload.StartsWith($prefix, $comparison)) { return $null }
    $relative = $ResolvedPayload.Substring($prefix.Length)
    $parts = @($relative -split '[\\/]')
    if ($parts.Count -ne 2 -or -not $parts[0] -or -not $parts[1]) {
        Fail "Installed payload must be exactly <copilot-home>/installed-plugins/<key>/<plugin>: $ResolvedPayload"
    }
    $key = $parts[0]
    $plugin = $parts[1]
    $declarations = @(Get-Declarations $key $ResolvedCopilotHome $ResolvedProjectRoot)
    return [pscustomobject]@{
        source = $declarations[0].source
        pluginId = $plugin
        readableName = $key
        locator = [pscustomobject][ordered]@{
            kind = 'installed'
            copilotHome = $ResolvedCopilotHome
            marketplaceKey = $key
            declaredIn = @($declarations | ForEach-Object { $_.declaredIn })
        }
    }
}

function Resolve-DirectoryEvidence([string]$ResolvedPayload, [string]$RequestedPluginId) {
    $cursor = $ResolvedPayload
    while ($cursor) {
        foreach ($relativeManifest in @(
            '.github/plugin/marketplace.json',
            'marketplace.json',
            '.plugin/marketplace.json',
            '.claude-plugin/marketplace.json'
        )) {
            $manifestPath = Join-Path $cursor $relativeManifest
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
            $manifest = Read-Json $manifestPath
            $metadata = Get-PropertyValue $manifest 'metadata'
            $pluginRoot = Get-StringProperty $metadata 'pluginRoot'
            $sourceBase = $cursor
            if ($pluginRoot) {
                if ([IO.Path]::IsPathRooted($pluginRoot)) {
                    Fail "Marketplace metadata.pluginRoot must be relative in '$manifestPath'."
                }
                if (@($pluginRoot -split '[\\/]') -contains '..') {
                    Fail "Marketplace metadata.pluginRoot may not escape '$cursor'."
                }
                $sourceBase = Join-Path $cursor $pluginRoot
                if (-not (Path-IsWithin $sourceBase $cursor)) {
                    Fail "Marketplace metadata.pluginRoot escapes '$cursor'."
                }
            }
            $plugins = @(Get-PropertyValue $manifest 'plugins' @())
            $matches = @()
            foreach ($plugin in $plugins) {
                $name = Get-StringProperty $plugin 'name'
                if ($RequestedPluginId -and $name -ne $RequestedPluginId) { continue }
                $sourcePath = Get-StringProperty $plugin 'source'
                if (-not $sourcePath) { continue }
                if ([IO.Path]::IsPathRooted($sourcePath) -or
                    @($sourcePath -split '[\\/]') -contains '..') {
                    Fail "Marketplace plugin source must be relative and remain beneath '$cursor'."
                }
                $candidate = $sourcePath
                if (-not [IO.Path]::IsPathRooted($candidate)) {
                    $candidate = Join-Path $sourceBase $candidate
                }
                if (-not (Path-IsWithin $candidate $cursor)) {
                    Fail "Marketplace plugin source escapes '$cursor'."
                }
                if ((Test-Path -LiteralPath $candidate) -and (Paths-Equal $candidate $ResolvedPayload)) {
                    $matches += $plugin
                }
            }
            if ($matches.Count -ne 1) {
                Fail "Marketplace manifest '$manifestPath' does not contain exactly one plugin entry resolving to '$ResolvedPayload'."
            }
            $pluginIdFromManifest = Get-StringProperty $matches[0] 'name'
            $source = Normalize-Source ([pscustomobject]@{ source = 'directory'; path = $cursor }) $cursor
            return [pscustomobject]@{
                source = $source
                pluginId = $pluginIdFromManifest
                readableName = Get-StringProperty $manifest 'name' 'marketplace'
                locator = [pscustomobject][ordered]@{
                    kind = 'directory'
                    marketplaceRoot = Canonical-Path $cursor -MustExist
                }
            }
        }
        $parent = Split-Path -Parent $cursor
        if (-not $parent -or (Paths-Equal $parent $cursor)) { break }
        $cursor = Canonical-Path $parent
    }
    return $null
}

function Locator-Matches($Locator, $ReceiptLocator) {
    if ((Get-StringProperty $Locator 'kind') -ne
        (Get-StringProperty $ReceiptLocator 'kind')) { return $false }
    if ($Locator.kind -eq 'installed') {
        return ((Get-StringProperty $ReceiptLocator 'marketplaceKey') -eq $Locator.marketplaceKey -and
                (Paths-Equal (Get-StringProperty $ReceiptLocator 'copilotHome') $Locator.copilotHome))
    }
    if ($Locator.kind -eq 'directory') {
        return (Paths-Equal (Get-StringProperty $ReceiptLocator 'marketplaceRoot') $Locator.marketplaceRoot)
    }
    return $false
}

function Find-ExistingSource(
    [string]$ResolvedDurableHome,
    [string]$Fingerprint,
    [string]$DesiredId,
    $Locator
) {
    $marketplaces = Join-Path $ResolvedDurableHome 'marketplaces'
    if (-not (Test-Path -LiteralPath $marketplaces -PathType Container)) { return @() }
    $results = @()
    foreach ($cellDirectory in @(Get-ChildItem -LiteralPath $marketplaces -Directory -ErrorAction SilentlyContinue)) {
        $file = Get-Item -LiteralPath (Join-Path $cellDirectory.FullName 'namespace.json') -ErrorAction SilentlyContinue
        if ($null -eq $file) { continue }
        $validated = Validate-NamespaceReceipt $file.FullName $ResolvedDurableHome
        $receipt = $validated.receipt
        $receiptFingerprint = $validated.identity.fingerprint
        $receiptId = $validated.marketplaceId
        if ($cellDirectory.Name -eq $DesiredId -and $receiptFingerprint -ne $Fingerprint) {
            Fail "Marketplace id '$DesiredId' is already occupied by a different full source fingerprint."
        }
        if ($receiptFingerprint -ne $Fingerprint) { continue }
        $locatorMatch = $null -eq $Locator
        if ($null -ne $Locator) {
            foreach ($known in @(Get-PropertyValue $receipt 'locators' @())) {
                if (Locator-Matches $Locator $known) { $locatorMatch = $true; break }
            }
        }
        $results += [pscustomobject][ordered]@{
            marketplaceId = $receiptId
            namespaceReceipt = Canonical-Path $file.FullName
            sameId = ($receiptId -eq $DesiredId)
            locatorMatch = $locatorMatch
        }
    }
    return $results
}

function Assert-PositiveInteger($Value, [string]$Name) {
    if ($Value -isnot [byte] -and $Value -isnot [int16] -and
        $Value -isnot [int32] -and $Value -isnot [int64]) {
        Fail "$Name must be an integer."
    }
    if ([int64]$Value -lt 1) { Fail "$Name must be at least 1." }
}

function Assert-ReceiptState($Value, [string]$Name) {
    if ([string]$Value -notin @('active', 'inactive', 'orphaned', 'removing')) {
        Fail "$Name must be active, inactive, orphaned, or removing."
    }
}

function Assert-ReceiptGeneration($Value, [string]$Name) {
    Assert-PositiveInteger $Value $Name
    if ([int64]$Value -gt [int64]::MaxValue) {
        Fail "$Name exceeds the portable signed 64-bit maximum."
    }
}

function Validate-NamespaceReceipt(
    [string]$ReceiptPath,
    [string]$ResolvedDurableHome
) {
    $actualReceipt = Canonical-Path $ReceiptPath -MustExist
    $cellRoot = Split-Path -Parent $actualReceipt
    $marketplacesRoot = Canonical-Path (Join-Path $ResolvedDurableHome 'marketplaces')
    if (-not (Path-IsWithin $cellRoot $marketplacesRoot)) {
        Fail "Namespace receipt '$actualReceipt' is outside the durable marketplaces root."
    }
    $marketplaceId = Split-Path -Leaf $cellRoot
    $canonicalReceipt = Canonical-Path (Join-Path $cellRoot 'namespace.json')
    if (-not (Paths-Equal $actualReceipt $canonicalReceipt)) {
        Fail "namespace.json is not at its exact canonical receipt location '$canonicalReceipt'."
    }
    $namespace = Read-Json $actualReceipt
    $namespaceVersion = Get-PropertyValue $namespace 'version'
    Assert-PositiveInteger $namespaceVersion 'namespace.json version'
    if ((Get-StringProperty $namespace 'schema') -ne 'copilot-extensions.marketplace-namespace' -or
        $namespaceVersion -ne 1) {
        Fail "Namespace receipt '$actualReceipt' has an unsupported schema or version."
    }
    if ((Get-StringProperty $namespace 'marketplaceId') -ne $marketplaceId) {
        Fail "Namespace receipt '$actualReceipt' does not match its cell directory."
    }
    $idMatch = [regex]::Match($marketplaceId, '^(.+)--([0-9a-f]{16})$')
    if (-not $idMatch.Success) {
        Fail "Invalid source-derived marketplace id '$marketplaceId'."
    }
    Assert-ReceiptGeneration (Get-PropertyValue $namespace 'generation') 'namespace.json generation'
    Assert-ReceiptState (Get-StringProperty $namespace 'state') 'namespace.json state'
    $sourceReceipt = Get-PropertyValue $namespace 'source'
    $normalized = Normalize-Source ([pscustomobject]@{
        kind = Get-StringProperty $sourceReceipt 'kind'
        canonical = Get-StringProperty $sourceReceipt 'canonical'
        ref = Get-StringProperty $sourceReceipt 'ref'
    }) '' -FromReceipt
    $identity = Source-Identity $normalized $idMatch.Groups[1].Value
    if ($identity.marketplaceId -ne $marketplaceId) {
        Fail "Namespace receipt '$actualReceipt' id does not match its normalized source."
    }
    if ((Get-StringProperty $sourceReceipt 'fingerprint') -ne $identity.fingerprint) {
        Fail "Namespace receipt '$actualReceipt' fingerprint does not match its normalized source."
    }
    return [pscustomobject]@{
        receipt = $namespace
        receiptPath = $actualReceipt
        cellRoot = $cellRoot
        marketplaceId = $marketplaceId
        identity = $identity
    }
}

function Assert-PluginId([string]$Value) {
    if ($Value -notmatch '^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$' -or
        $Value -in @('.', '..')) {
        Fail "Invalid filesystem-safe plugin id '$Value'."
    }
    $baseName = $Value.Split([char]'.')[0].ToUpperInvariant()
    if ($baseName -in @('CON', 'PRN', 'AUX', 'NUL') -or
        $baseName -match '^(COM|LPT)[1-9]$') {
        Fail "Invalid filesystem-safe plugin id '$Value'."
    }
}

function Resolve-RelativeRoot([string]$PluginRootPath, [string]$Relative, [string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative)) {
        Fail "roots.$Name must be a non-empty relative path."
    }
    $segments = @($Relative -split '[\\/]')
    if ($segments -contains '..' -or $segments -contains '.') {
        Fail "roots.$Name may not escape or use dot segments."
    }
    $resolved = Canonical-Path (Join-Path $PluginRootPath $Relative)
    $prefix = (Canonical-Path $PluginRootPath) + [IO.Path]::DirectorySeparatorChar
    $comparison = [StringComparison]::Ordinal
    if ($env:OS -eq 'Windows_NT') { $comparison = [StringComparison]::OrdinalIgnoreCase }
    if (-not $resolved.StartsWith($prefix, $comparison)) {
        Fail "roots.$Name escapes pluginRoot."
    }
    return $resolved
}

function Validate-ContextReceipt(
    [string]$ReceiptPath,
    [string]$ResolvedDurableHome,
    [string]$MarketplaceExpectation,
    [string]$PluginExpectation,
    [string]$PayloadExpectation,
    [string]$CellExpectation
) {
    if (-not [IO.Path]::IsPathRooted($ReceiptPath)) {
        Fail 'The installation-context receipt pointer must be absolute.'
    }
    foreach ($expectation in @(
        @('expected payload root', $PayloadExpectation),
        @('expected cell root', $CellExpectation)
    )) {
        if ($expectation[1] -and -not [IO.Path]::IsPathRooted([string]$expectation[1])) {
            Fail "$($expectation[0]) must be absolute."
        }
    }
    $actualReceipt = Canonical-Path $ReceiptPath -MustExist
    $install = Read-Json $actualReceipt
    $installVersion = Get-PropertyValue $install 'version'
    Assert-PositiveInteger $installVersion 'install.json version'
    if ((Get-StringProperty $install 'schema') -ne 'copilot-extensions.plugin-installation' -or
        $installVersion -ne 1) {
        Fail 'install.json has an unsupported schema or version.'
    }
    $marketplaceId = Get-StringProperty $install 'marketplaceId'
    $receiptPluginId = Get-StringProperty $install 'pluginId'
    if (-not $marketplaceId -or -not $receiptPluginId) { Fail 'install.json identity is incomplete.' }
    if ($marketplaceId -notmatch '^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}$') {
        Fail "Invalid source-derived marketplace id '$marketplaceId'."
    }
    Assert-PluginId $receiptPluginId
    $cellRoot = Canonical-Path (Join-Path (Join-Path $ResolvedDurableHome 'marketplaces') $marketplaceId)
    $expectedPluginRoot = Canonical-Path (Join-Path (Join-Path $cellRoot 'plugins') $receiptPluginId)
    $canonicalReceipt = Canonical-Path (Join-Path $expectedPluginRoot 'install.json')
    if (-not (Paths-Equal $actualReceipt $canonicalReceipt)) {
        Fail "install.json is not at its exact canonical receipt location '$canonicalReceipt'."
    }
    if (-not (Paths-Equal (Get-StringProperty $install 'pluginRoot') $expectedPluginRoot)) {
        Fail 'install.json pluginRoot does not match its canonical cell/plugin location.'
    }
    if ($MarketplaceExpectation -and $marketplaceId -ne $MarketplaceExpectation) {
        Fail "Expected marketplace '$MarketplaceExpectation', receipt names '$marketplaceId'."
    }
    if ($PluginExpectation -and $receiptPluginId -ne $PluginExpectation) {
        Fail "Expected plugin '$PluginExpectation', receipt names '$receiptPluginId'."
    }
    if ($CellExpectation -and -not (Paths-Equal $cellRoot $CellExpectation)) {
        Fail "Expected cell '$CellExpectation', receipt belongs to '$cellRoot'."
    }
    Assert-ReceiptGeneration (Get-PropertyValue $install 'generation') 'install.json generation'
    Assert-ReceiptState (Get-StringProperty $install 'state') 'install.json state'

    $namespacePath = Canonical-Path (Join-Path $cellRoot 'namespace.json')
    if (-not (Paths-Equal (Get-StringProperty $install 'namespaceReceipt') $namespacePath)) {
        Fail 'install.json namespaceReceipt is not the exact namespace receipt in the same cell.'
    }
    $validatedNamespace = Validate-NamespaceReceipt $namespacePath $ResolvedDurableHome
    if ($validatedNamespace.marketplaceId -ne $marketplaceId) {
        Fail 'namespace.json marketplaceId does not match install.json.'
    }
    $identity = $validatedNamespace.identity

    $payload = Get-PropertyValue $install 'payload'
    $payloadPath = Get-StringProperty $payload 'root'
    if (-not [IO.Path]::IsPathRooted($payloadPath)) { Fail 'payload.root must be absolute.' }
    if ([string]::IsNullOrWhiteSpace((Get-StringProperty $payload 'version'))) {
        Fail 'payload.version must be a non-empty string.'
    }
    if ((Get-StringProperty $payload 'origin') -notin
        @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload.origin must be installed, directory, staged, or explicit.'
    }
    $payloadOriginReceipt = Get-PropertyValue $payload 'originReceipt'
    if ($null -ne $payloadOriginReceipt) {
        if ($payloadOriginReceipt -isnot [string]) {
            Fail 'payload.originReceipt must be a string.'
        }
        if (-not [IO.Path]::IsPathRooted($payloadOriginReceipt)) {
            Fail 'payload.originReceipt must be absolute.'
        }
    }
    $payloadPath = Canonical-Path $payloadPath
    if ($PayloadExpectation -and -not (Paths-Equal $payloadPath $PayloadExpectation)) {
        Fail "Expected payload '$PayloadExpectation', receipt names '$payloadPath'."
    }
    if ($env:COPILOT_PLUGIN_ROOT) {
        if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
        }
        if (-not (Paths-Equal $payloadPath $env:COPILOT_PLUGIN_ROOT)) {
            Fail 'COPILOT_PLUGIN_ROOT conflicts with the validated payload root.'
        }
    }

    $rootsReceipt = Get-PropertyValue $install 'roots'
    $rootNames = @('versions', 'snapshots', 'state', 'run', 'logs', 'cache', 'launchers')
    $roots = [ordered]@{}
    foreach ($name in $rootNames) {
        $roots[$name + 'Root'] = Resolve-RelativeRoot $expectedPluginRoot (Get-StringProperty $rootsReceipt $name) $name
    }
    return [pscustomobject][ordered]@{
        action = 'validate'
        marketplaceId = $marketplaceId
        marketplaceSlot = $marketplaceId
        sourceFingerprint = $identity.fingerprint
        source = [pscustomobject][ordered]@{
            kind = $identity.kind
            canonical = $identity.canonical
            ref = $identity.ref
        }
        pluginId = $receiptPluginId
        payloadRoot = $payloadPath
        cellRoot = $cellRoot
        pluginRoot = $expectedPluginRoot
        versionsRoot = $roots.versionsRoot
        snapshotsRoot = $roots.snapshotsRoot
        stateRoot = $roots.stateRoot
        runRoot = $roots.runRoot
        logsRoot = $roots.logsRoot
        cacheRoot = $roots.cacheRoot
        launchersRoot = $roots.launchersRoot
        reposRoot = Canonical-Path (Join-Path $cellRoot 'repos')
        namespaceReceipt = $namespacePath
        installReceipt = $actualReceipt
        namespaceGeneration = Get-PropertyValue $validatedNamespace.receipt 'generation'
        generation = Get-PropertyValue $install 'generation'
        state = Get-PropertyValue $install 'state'
    }
}

function Assert-ExpectedGeneration(
    [long]$Actual,
    [long]$Expected,
    [string]$ReceiptName
) {
    if ($Expected -lt 0) {
        Fail "Expected $ReceiptName generation must be a non-negative integer."
    }
    if ($Actual -ne $Expected) {
        Fail "$ReceiptName generation changed: expected $Expected, found $Actual; restart installation-context resolution."
    }
}

function Stamp-Context($Resolved, [string]$ResolvedDurableHome) {
    if ([string]::IsNullOrWhiteSpace($PayloadVersion)) {
        Fail 'stamp requires -PayloadVersion.'
    }
    if ($PayloadOrigin -notin @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload origin must be installed, directory, staged, or explicit.'
    }
    if ($ExpectedNamespaceGeneration -lt 0) {
        Fail 'stamp requires -ExpectedNamespaceGeneration.'
    }
    if ($ExpectedInstallGeneration -lt 0) {
        Fail 'stamp requires -ExpectedInstallGeneration.'
    }

    $originReceiptPath = ''
    if ($PayloadOriginReceipt) {
        if (-not [IO.Path]::IsPathRooted($PayloadOriginReceipt)) {
            Fail 'payload origin receipt must be absolute.'
        }
        $originReceiptPath = Canonical-Path $PayloadOriginReceipt -MustExist
    }

    $namespacePath = [string]$Resolved.namespaceReceipt
    $installPath = [string]$Resolved.installReceipt
    $cellRoot = [string]$Resolved.cellRoot
    $pluginRoot = [string]$Resolved.pluginRoot
    $marketplaceId = [string]$Resolved.marketplaceId
    $receiptPluginId = [string]$Resolved.pluginId
    $namespaceChanged = $false
    $installChanged = $false

    $genesisLock = Join-Path (Join-Path $ResolvedDurableHome 'marketplaces/.locks') ($marketplaceId + '.genesis')
    Acquire-Lock $genesisLock 'genesis' $marketplaceId
    $genesisFailed = $false
    try {
        $existingNamespace = $null
        $namespaceGeneration = 0
        $existingNamespaceState = ''
        $locators = @()
        if (Test-Path -LiteralPath $namespacePath -PathType Leaf) {
            $validatedNamespace = Validate-NamespaceReceipt $namespacePath $ResolvedDurableHome
            $existingNamespace = $validatedNamespace.receipt
            $namespaceGeneration = [long](Get-PropertyValue $existingNamespace 'generation')
            $existingNamespaceState = Get-StringProperty $existingNamespace 'state'
            $priorLocators = Get-PropertyValue $existingNamespace 'locators' @()
            $locators = @($priorLocators)
        }
        Assert-ExpectedGeneration $namespaceGeneration $ExpectedNamespaceGeneration 'namespace.json'
        $appendLocator = $false
        if ($null -ne $Resolved.locator) {
            $appendLocator = $true
            foreach ($known in $locators) {
                if (Locator-Matches $Resolved.locator $known) {
                    $appendLocator = $false
                    break
                }
            }
        }
        if ($appendLocator) { $locators += $Resolved.locator }
        if ($locators.Count -gt 16) {
            $locators = @($locators | Select-Object -Last 16)
        }
        if ($null -eq $existingNamespace -or
            $existingNamespaceState -ne $NamespaceState -or
            $appendLocator) {
            $now = Get-UtcTimestamp
            $createdAt = $now
            if ($null -ne $existingNamespace) {
                $createdAt = Get-ReceiptTimestamp $existingNamespace 'createdAt' $now
            }
            if ($namespaceGeneration -eq [int64]::MaxValue) {
                Fail 'namespace.json generation cannot be incremented; explicit repair is required.'
            }
            $namespaceGeneration++
            $namespace = [ordered]@{
                schema = 'copilot-extensions.marketplace-namespace'
                version = 1
                marketplaceId = $marketplaceId
                source = [ordered]@{
                    kind = [string]$Resolved.source.kind
                    canonical = [string]$Resolved.source.canonical
                    ref = [string]$Resolved.source.ref
                    fingerprint = [string]$Resolved.sourceFingerprint
                }
                locators = @($locators)
                generation = $namespaceGeneration
                state = $NamespaceState
                createdAt = $createdAt
                updatedAt = $now
            }
            Write-AtomicJson $namespacePath $namespace
            $namespaceChanged = $true
        }
    }
    catch {
        $genesisFailed = $true
        throw
    }
    finally {
        if ($script:HeldLockPath) {
            try { Release-Lock }
            catch {
                if (-not $genesisFailed) { throw }
                [Console]::Error.WriteLine(
                    "installation-context: $($_.Exception.Message) while preserving the original mutation failure."
                )
            }
        }
    }

    $installLock = Join-Path (Join-Path $cellRoot '.locks') ($receiptPluginId + '.install.lock')
    Acquire-Lock $installLock 'install' $marketplaceId $receiptPluginId
    $installFailed = $false
    try {
        $existingInstall = $null
        $installGeneration = 0
        $existingInstallState = ''
        $currentPayloadRoot = ''
        $currentPayloadVersion = ''
        $currentPayloadOrigin = ''
        $currentOriginReceipt = ''
        if (Test-Path -LiteralPath $installPath -PathType Leaf) {
            $savedPluginRoot = $env:COPILOT_PLUGIN_ROOT
            try {
                Remove-Item Env:COPILOT_PLUGIN_ROOT -ErrorAction SilentlyContinue
                [void](Validate-ContextReceipt $installPath $ResolvedDurableHome $marketplaceId $receiptPluginId '' $cellRoot)
            }
            finally {
                if ($null -ne $savedPluginRoot) {
                    $env:COPILOT_PLUGIN_ROOT = $savedPluginRoot
                }
            }
            $existingInstall = Read-Json $installPath
            $installGeneration = [long](Get-PropertyValue $existingInstall 'generation')
            $existingInstallState = Get-StringProperty $existingInstall 'state'
            $existingPayload = Get-PropertyValue $existingInstall 'payload'
            $currentPayloadRoot = Canonical-Path (Get-StringProperty $existingPayload 'root')
            $currentPayloadVersion = Get-StringProperty $existingPayload 'version'
            $currentPayloadOrigin = Get-StringProperty $existingPayload 'origin'
            $currentOriginReceipt = Get-StringProperty $existingPayload 'originReceipt'
        }
        Assert-ExpectedGeneration $installGeneration $ExpectedInstallGeneration 'install.json'
        $payloadChanged = $null -eq $existingInstall
        if ($null -ne $existingInstall) {
            $payloadChanged = (-not (Paths-Equal $currentPayloadRoot $Resolved.payloadRoot) -or
                $currentPayloadVersion -ne $PayloadVersion -or
                $currentPayloadOrigin -ne $PayloadOrigin -or
                $currentOriginReceipt -ne $originReceiptPath -or
                $existingInstallState -ne $InstallState)
        }
        if ($payloadChanged) {
            $now = Get-UtcTimestamp
            $createdAt = $now
            if ($null -ne $existingInstall) {
                $createdAt = Get-ReceiptTimestamp $existingInstall 'createdAt' $now
                $existingRoots = Get-PropertyValue $existingInstall 'roots'
                $roots = [ordered]@{}
                foreach ($name in @('versions', 'snapshots', 'state', 'run', 'logs', 'cache', 'launchers')) {
                    $roots[$name] = Get-StringProperty $existingRoots $name
                }
            }
            else {
                $roots = [ordered]@{
                    versions = 'versions'
                    snapshots = 'snapshots'
                    state = 'state'
                    run = 'run'
                    logs = 'logs'
                    cache = 'cache'
                    launchers = 'launchers'
                }
            }
            $payload = [ordered]@{
                root = [string]$Resolved.payloadRoot
                version = $PayloadVersion
                origin = $PayloadOrigin
            }
            if ($originReceiptPath) { $payload['originReceipt'] = $originReceiptPath }
            if ($installGeneration -eq [int64]::MaxValue) {
                Fail 'install.json generation cannot be incremented; explicit repair is required.'
            }
            $installGeneration++
            $install = [ordered]@{
                schema = 'copilot-extensions.plugin-installation'
                version = 1
                marketplaceId = $marketplaceId
                pluginId = $receiptPluginId
                pluginRoot = $pluginRoot
                namespaceReceipt = $namespacePath
                payload = $payload
                roots = $roots
                generation = $installGeneration
                state = $InstallState
                createdAt = $createdAt
                updatedAt = $now
            }
            Write-AtomicJson $installPath $install
            $installChanged = $true
        }
    }
    catch {
        $installFailed = $true
        throw
    }
    finally {
        if ($script:HeldLockPath) {
            try { Release-Lock }
            catch {
                if (-not $installFailed) { throw }
                [Console]::Error.WriteLine(
                    "installation-context: $($_.Exception.Message) while preserving the original mutation failure."
                )
            }
        }
    }

    $validated = Validate-ContextReceipt $installPath $ResolvedDurableHome $marketplaceId $receiptPluginId $Resolved.payloadRoot $cellRoot
    $validated.action = 'stamp'
    $validated | Add-Member -NotePropertyName namespaceChanged -NotePropertyValue $namespaceChanged
    $validated | Add-Member -NotePropertyName installChanged -NotePropertyValue $installChanged
    $validated | Add-Member -NotePropertyName operative -NotePropertyValue $false
    return $validated
}

try {
    if (-not $CopilotHome) { $CopilotHome = Get-DefaultHome '.copilot' }
    if (-not $DurableHome) { $DurableHome = Get-DefaultHome '.copilot-extensions' }
    if (-not [IO.Path]::IsPathRooted($CopilotHome) -or
        -not [IO.Path]::IsPathRooted($DurableHome)) {
        Fail '-CopilotHome and -DurableHome must be absolute.'
    }
    $resolvedCopilotHome = Canonical-Path $CopilotHome
    $resolvedDurableHome = Canonical-Path $DurableHome
    $resolvedProjectRoot = ''
    if ($ProjectRoot) { $resolvedProjectRoot = Canonical-Path $ProjectRoot -MustExist }

    if ($Action -eq 'source-id') {
        $descriptor = Read-SourceDescriptor
        if ($null -eq $descriptor) {
            Fail 'source-id requires -SourceJson or -SourceFile.'
        }
        $normalized = Normalize-Source $descriptor ''
        $name = $MarketplaceKey
        if (-not $name) { $name = 'marketplace' }
        $result = Source-Identity $normalized $name
    }
    elseif ($Action -eq 'validate') {
        $pointer = $Context
        if (-not $pointer) { $pointer = $env:COPILOT_EXTENSIONS_CONTEXT }
        if (-not $pointer) { Fail 'validate requires -Context or COPILOT_EXTENSIONS_CONTEXT.' }
        $payloadExpectation = $ExpectedPayloadRoot
        if (-not $payloadExpectation -and $PayloadRoot) { $payloadExpectation = $PayloadRoot }
        $result = Validate-ContextReceipt $pointer $resolvedDurableHome $ExpectedMarketplaceId $ExpectedPluginId $payloadExpectation $ExpectedCellRoot
    }
    else {
        $pointer = ''
        if ($Action -eq 'resolve') {
            $pointer = $Context
            if (-not $pointer) { $pointer = $env:COPILOT_EXTENSIONS_CONTEXT }
        }
        if ($pointer) {
            $payloadExpectation = $ExpectedPayloadRoot
            if (-not $payloadExpectation) { $payloadExpectation = $PayloadRoot }
            if (-not $payloadExpectation) { $payloadExpectation = $env:COPILOT_PLUGIN_ROOT }
            $pluginExpectation = $PluginId
            if (-not $pluginExpectation) { $pluginExpectation = $ExpectedPluginId }
            if (-not $pluginExpectation) {
                Fail 'resolve with an explicit context requires an expected plugin id.'
            }
            if (-not $payloadExpectation -and -not $ExpectedMarketplaceId -and
                -not $ExpectedCellRoot) {
                Fail 'resolve with an explicit context requires an expected payload, marketplace, or cell identity.'
            }
            $result = Validate-ContextReceipt $pointer $resolvedDurableHome $ExpectedMarketplaceId $pluginExpectation $payloadExpectation $ExpectedCellRoot
            $result.action = 'resolve'
        }
        else {
            if (-not $PayloadRoot) { $PayloadRoot = $env:COPILOT_PLUGIN_ROOT }
            if (-not $PayloadRoot) { Fail "$Action requires -PayloadRoot or COPILOT_PLUGIN_ROOT." }
            if (-not [IO.Path]::IsPathRooted($PayloadRoot)) {
                Fail 'The payload root must be absolute.'
            }
            $resolvedPayload = Canonical-Path $PayloadRoot -MustExist
            if (-not (Test-Path -LiteralPath $resolvedPayload -PathType Container)) {
                Fail "The payload root must be an existing directory: $resolvedPayload"
            }
            if ($env:COPILOT_PLUGIN_ROOT) {
                if (-not [IO.Path]::IsPathRooted($env:COPILOT_PLUGIN_ROOT)) {
                    Fail 'COPILOT_PLUGIN_ROOT must be absolute.'
                }
                if (-not (Paths-Equal $resolvedPayload $env:COPILOT_PLUGIN_ROOT)) {
                    Fail 'COPILOT_PLUGIN_ROOT conflicts with -PayloadRoot.'
                }
            }
            $descriptor = Read-SourceDescriptor
            $evidence = $null
            if ($null -ne $descriptor) {
                if (-not $PluginId) { Fail 'Explicit source resolution requires -PluginId.' }
                $evidence = [pscustomobject]@{
                    source = Normalize-Source $descriptor ''
                    pluginId = $PluginId
                    readableName = $(if ($MarketplaceKey) { $MarketplaceKey } else { 'marketplace' })
                    locator = $null
                }
            }
            else {
                $evidence = Resolve-InstalledEvidence $resolvedPayload $resolvedCopilotHome $resolvedProjectRoot
                if ($null -eq $evidence) {
                    $evidence = Resolve-DirectoryEvidence $resolvedPayload $PluginId
                }
                if ($null -eq $evidence) {
                    Fail "Cannot establish marketplace provenance for payload '$resolvedPayload'. Supply an explicit source descriptor for management/development mode."
                }
                if ($PluginId -and $PluginId -ne $evidence.pluginId) {
                    Fail "Expected plugin '$PluginId', payload evidence identifies '$($evidence.pluginId)'."
                }
            }
            $identity = Source-Identity $evidence.source $evidence.readableName
            Assert-PluginId $evidence.pluginId
            $existing = @(Find-ExistingSource $resolvedDurableHome $identity.fingerprint $identity.marketplaceId $evidence.locator)
            $rebind = @($existing | Where-Object { -not $_.sameId -or -not $_.locatorMatch })
            if ($rebind.Count -gt 0) {
                $owners = ($rebind | ForEach-Object { $_.marketplaceId }) -join ', '
                Fail "Source '$($identity.fingerprint)' already owns cell/locator '$owners'; explicit rebind or new-cell intent is required."
            }
            $cellRoot = Canonical-Path (Join-Path (Join-Path $resolvedDurableHome 'marketplaces') $identity.marketplaceId)
            $pluginRootPath = Canonical-Path (Join-Path (Join-Path $cellRoot 'plugins') $evidence.pluginId)
            $result = [pscustomobject][ordered]@{
                action = 'resolve'
                source = [pscustomobject][ordered]@{
                    kind = $identity.kind
                    canonical = $identity.canonical
                    ref = $identity.ref
                    record = $identity.record
                }
                sourceFingerprint = $identity.fingerprint
                marketplaceId = $identity.marketplaceId
                marketplaceSlot = $identity.marketplaceId
                pluginId = $evidence.pluginId
                payloadRoot = $resolvedPayload
                cellRoot = $cellRoot
                pluginRoot = $pluginRootPath
                versionsRoot = Canonical-Path (Join-Path $pluginRootPath 'versions')
                snapshotsRoot = Canonical-Path (Join-Path $pluginRootPath 'snapshots')
                stateRoot = Canonical-Path (Join-Path $pluginRootPath 'state')
                runRoot = Canonical-Path (Join-Path $pluginRootPath 'run')
                logsRoot = Canonical-Path (Join-Path $pluginRootPath 'logs')
                cacheRoot = Canonical-Path (Join-Path $pluginRootPath 'cache')
                launchersRoot = Canonical-Path (Join-Path $pluginRootPath 'launchers')
                reposRoot = Canonical-Path (Join-Path $cellRoot 'repos')
                namespaceReceipt = Canonical-Path (Join-Path $cellRoot 'namespace.json')
                installReceipt = Canonical-Path (Join-Path $pluginRootPath 'install.json')
                locator = $evidence.locator
                existingCells = $existing
                rebindRequired = $false
                operative = $false
            }
            if ($Action -eq 'stamp') {
                $result = Stamp-Context $result $resolvedDurableHome
            }
        }
    }
    $result | ConvertTo-Json -Depth 12 -Compress
    exit 0
}
catch {
    [Console]::Error.WriteLine("installation-context: $($_.Exception.Message)")
    exit 1
}
