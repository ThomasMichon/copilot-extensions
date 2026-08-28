# Emit effort-enforcement context for an adopting repository.

$ErrorActionPreference = 'Stop'
$MaxPayloadBytes = 65536
$MaxConfigBytes = 4096
$MaxManifestBytes = 4096
$MaxContextBytes = 1024

function Write-Diagnostic([string] $Message) {
    [Console]::Error.WriteLine("[efforts] $Message")
}

function Emit-Empty {
    [Console]::Out.Write('{}')
    exit 0
}

if (-not ('EffortsStrictJson' -as [type])) {
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
using System.Text;

public static class EffortsStrictJson {
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
            var keys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (Consume('}')) return;
            while (true) {
                if (index >= text.Length || text[index] != '"') Error("expected object key");
                string key = ParseString();
                if (!keys.Add(key)) Error("duplicate or case-conflicting object key");
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
            if (!Consume(value)) Error("expected token");
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

public static class EffortsCanonicalPath {
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint FILE_SHARE_DELETE = 0x00000004;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string name, uint access, uint share, IntPtr security, uint creation,
        uint flags, IntPtr template);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle handle, StringBuilder path, uint size, uint flags);

    [DllImport("libc", SetLastError = true)]
    private static extern IntPtr realpath(string path, IntPtr resolved);

    [DllImport("libc")]
    private static extern void free(IntPtr pointer);

    private static string ResolveWindows(string path) {
        using (SafeFileHandle handle = CreateFile(
            path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            IntPtr.Zero, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS,
            IntPtr.Zero)) {
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            StringBuilder buffer = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandle(
                handle, buffer, (uint)buffer.Capacity, 0);
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

    private static string ResolvePosix(string path) {
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

    public static string Resolve(string path) {
        return System.IO.Path.DirectorySeparatorChar == '\\'
            ? ResolveWindows(path)
            : ResolvePosix(path);
    }
}
'@
    } catch {
        Write-Diagnostic 'strict JSON validator is unavailable; no policy context emitted'
        Emit-Empty
    }
}

function ConvertFrom-StrictJsonObject([string] $Text) {
    [EffortsStrictJson]::Validate($Text)
    if (-not $Text.TrimStart().StartsWith('{')) {
        throw 'JSON root must be an object'
    }
    $Value = $Text | ConvertFrom-Json -ErrorAction Stop
    if ($Value -isnot [pscustomobject]) {
        throw 'JSON root must be an object'
    }
    return $Value
}

function Test-AbsolutePath([string] $Value) {
    if ($env:OS -eq 'Windows_NT') {
        return $Value -cmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)'
    }
    return $Value.StartsWith('/')
}

function Test-RelativePathContainsReparsePoint(
    [string] $Root,
    [string] $RelativePath
) {
    try {
        $Current = [IO.Path]::GetFullPath($Root)
        foreach ($Segment in ($RelativePath -split '[\\/]')) {
            if (-not $Segment) { continue }
            $Current = Join-Path $Current $Segment
            if (Test-Path -LiteralPath $Current) {
                $Item = Get-Item -LiteralPath $Current -Force -ErrorAction Stop
                if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    return $true
                }
            }
        }
        return $false
    } catch {
        return $true
    }
}

function Read-BoundedUtf8([string] $Path, [int] $Limit) {
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $Item.PSIsContainer -or $Item.Length -gt $Limit) {
        throw 'not a bounded regular file'
    }
    $Stream = [IO.File]::Open(
        $Path,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        $Buffer = New-Object byte[] ($Limit + 1)
        $Count = 0
        while ($Count -lt $Buffer.Length) {
            $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
            if ($Read -eq 0) { break }
            $Count += $Read
        }
    } finally {
        $Stream.Dispose()
    }
    if ($Count -gt $Limit) { throw 'file exceeds limit' }
    for ($Index = 0; $Index -lt $Count; $Index += 1) {
        if ($Buffer[$Index] -eq 0) { throw 'file contains NUL' }
    }
    $Utf8 = New-Object Text.UTF8Encoding($false, $true)
    return $Utf8.GetString($Buffer, 0, $Count)
}

function Invoke-CleanGitRoot([string] $Cwd) {
    $Names = @(
        'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
        'GIT_CEILING_DIRECTORIES', 'GIT_DISCOVERY_ACROSS_FILESYSTEM',
        'GIT_PREFIX', 'GIT_SUPER_PREFIX', 'GIT_QUARANTINE_PATH',
        'GIT_NAMESPACE', 'GIT_CONFIG', 'GIT_CONFIG_SYSTEM',
        'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_COUNT'
    )
    foreach ($Entry in Get-ChildItem Env:) {
        if ($Entry.Name -like 'GIT_CONFIG_KEY_*' -or
            $Entry.Name -like 'GIT_CONFIG_VALUE_*') {
            $Names += $Entry.Name
        }
    }
    $Process = $null
    try {
        $StartInfo = New-Object Diagnostics.ProcessStartInfo
        $StartInfo.FileName = 'git'
        $StartInfo.Arguments = 'rev-parse --show-toplevel'
        $StartInfo.WorkingDirectory = $Cwd
        $StartInfo.UseShellExecute = $false
        $StartInfo.CreateNoWindow = $true
        $StartInfo.RedirectStandardOutput = $true
        $StartInfo.RedirectStandardError = $true
        foreach ($Name in $Names) {
            $StartInfo.EnvironmentVariables.Remove($Name)
        }
        $Process = New-Object Diagnostics.Process
        $Process.StartInfo = $StartInfo
        if (-not $Process.Start()) { return $null }
        if (-not $Process.WaitForExit(5000)) {
            $Process.Kill()
            $Process.WaitForExit()
            return $null
        }
        if ($Process.ExitCode -ne 0) { return $null }
        return ($Process.StandardOutput.ReadLine())
    } catch {
        return $null
    } finally {
        if ($null -ne $Process) { $Process.Dispose() }
    }
}

try {
    $Stream = [Console]::OpenStandardInput()
    $Buffer = New-Object byte[] ($MaxPayloadBytes + 1)
    $Count = 0
    while ($Count -lt $Buffer.Length) {
        $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
        if ($Read -eq 0) { break }
        $Count += $Read
    }
    if ($Count -gt $MaxPayloadBytes -or
        [Array]::IndexOf($Buffer, [byte]0, 0, $Count) -ge 0) {
        Write-Diagnostic 'missing or malformed sessionStart payload; no policy context emitted'
        Emit-Empty
    }
    try {
        $Utf8 = New-Object Text.UTF8Encoding($false, $true)
        $PayloadText = $Utf8.GetString($Buffer, 0, $Count)
        $Payload = ConvertFrom-StrictJsonObject $PayloadText
        $Cwd = $Payload.cwd
        if ($Cwd -isnot [string] -or -not (Test-AbsolutePath $Cwd) -or
            $Cwd -match '[\x00-\x1f]' -or
            -not (Test-Path -LiteralPath $Cwd -PathType Container)) {
            throw 'invalid cwd'
        }
    } catch {
        Write-Diagnostic 'missing or malformed sessionStart payload; no policy context emitted'
        Emit-Empty
    }

    $RawRoot = Invoke-CleanGitRoot $Cwd
    if (-not $RawRoot) { Emit-Empty }
    $RepoRoot = [EffortsCanonicalPath]::Resolve($RawRoot)
    $CwdRoot = [EffortsCanonicalPath]::Resolve($Cwd)
    $Comparison = if ($env:OS -eq 'Windows_NT') {
        [StringComparison]::OrdinalIgnoreCase
    } else {
        [StringComparison]::Ordinal
    }
    $Prefix = $RepoRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $CwdRoot.Equals($RepoRoot, $Comparison) -and
        -not $CwdRoot.StartsWith($Prefix, $Comparison)) {
        Emit-Empty
    }

    $ConfigRelativePath = '.copilot-extensions/efforts/config.json'
    $ConfigPath = Join-Path $RepoRoot $ConfigRelativePath
    $ConfigHasReparsePoint = Test-RelativePathContainsReparsePoint `
        $RepoRoot $ConfigRelativePath
    if (-not (Test-Path -LiteralPath $ConfigPath) -and
        -not $ConfigHasReparsePoint) {
        Emit-Empty
    }
    if ($ConfigHasReparsePoint -or
        -not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        Write-Diagnostic 'repository effort config is not a contained regular file'
        Emit-Empty
    }
    try {
        $ConfigText = Read-BoundedUtf8 $ConfigPath $MaxConfigBytes
        $Config = ConvertFrom-StrictJsonObject $ConfigText
        $Properties = @($Config.PSObject.Properties.Name)
        if ($Properties.Count -ne 2 -or
            $Properties -cnotcontains 'version' -or
            $Properties -cnotcontains 'enforcement' -or
            ($Config.version -isnot [int] -and $Config.version -isnot [long]) -or
            $Config.version -ne 1 -or
            $Config.enforcement -cne 'required') {
            throw 'invalid config'
        }
    } catch {
        Write-Diagnostic 'repository effort config is malformed; no policy context emitted'
        Emit-Empty
    }

    $PluginRoot = Split-Path -Parent $PSScriptRoot
    $ManifestPath = Join-Path $PluginRoot 'plugin.json'
    try {
        $ManifestText = Read-BoundedUtf8 $ManifestPath $MaxManifestBytes
        $Manifest = ConvertFrom-StrictJsonObject $ManifestText
        $Version = $Manifest.version
        if ($Version -isnot [string] -or $Version.Length -gt 64 -or
            $Version -cnotmatch '\A[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?\z') {
            throw 'invalid version'
        }
    } catch {
        Write-Diagnostic 'plugin manifest is missing or malformed; no policy context emitted'
        Emit-Empty
    }

    $Kernel = "[owner: efforts@$Version]`n" +
        'Efforts are required. For substantial multi-step work, use ' +
        '`planning-efforts` to create or resume the canonical effort, not a new ' +
        'plan document. Review its plan before implementation and execute in ' +
        'waves. Only the rightful head drives the next slice; superseded sessions ' +
        'assist or hand off. Continue until the effort is explicitly Done and ' +
        'each Plan and Validation Plan item is resolved or transferred to a named ' +
        'tracked objective. A completed phase, PR, ' +
        'handoff, or session is not completion. Pause only for genuine ' +
        'uncertainty, prerequisites, required review, or required ' +
        'safety/admin confirmation. Handoffs name the effort and next slice; ' +
        'bounded predecessor ramp-up covers only immediate activity. Keep ' +
        'cross-repo planning in the host unless an authoritative local target ' +
        'checkout has valid efforts adoption; then use one target-owned ' +
        'sub-effort referenced one-way.'
    if ([Text.Encoding]::UTF8.GetByteCount($Kernel) -ge $MaxContextBytes) {
        Write-Diagnostic 'policy context exceeds its byte budget; no policy context emitted'
        Emit-Empty
    }
    [Console]::Out.Write(
        (@{ additionalContext = $Kernel } | ConvertTo-Json -Compress)
    )
} catch {
    Write-Diagnostic 'policy producer failed; no policy context emitted'
    Emit-Empty
}
