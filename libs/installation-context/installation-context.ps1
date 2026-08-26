[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('source-id', 'resolve', 'validate')]
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
    [string]$ExpectedCellRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

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

function Fail([string]$Message) {
    throw [System.InvalidOperationException]::new($Message)
}

function Get-PropertyValue($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
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
            $resolved = Resolve-Path -LiteralPath $existing -ErrorAction SilentlyContinue
            if ($null -eq $resolved) { Fail "Cannot resolve path: $existing" }
            $fullPath = [IO.Path]::GetFullPath($resolved.ProviderPath)
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
        return ([IO.File]::ReadAllText($canonical) | ConvertFrom-Json)
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
        try { return ($SourceJson | ConvertFrom-Json) }
        catch { Fail "Invalid -SourceJson: $($_.Exception.Message)" }
    }
    return $null
}

function Normalize-GitUrl([string]$Url) {
    if ([string]::IsNullOrWhiteSpace($Url)) { Fail 'A git source requires url.' }
    $candidate = $Url.Trim()
    if ($candidate -match '^[^/@:]+@([^/:]+):(.+)$') {
        $candidate = "ssh://$($Matches[1])/$($Matches[2])"
    }
    try { $uri = [Uri]$candidate }
    catch { Fail "Invalid git URL '$Url'." }
    if (-not $uri.IsAbsoluteUri -or [string]::IsNullOrWhiteSpace($uri.Host)) {
        Fail "Git URL must be absolute and include a host: $Url"
    }
    $scheme = $uri.Scheme.ToLowerInvariant()
    $hostName = $uri.Host.ToLowerInvariant()
    $port = ''
    if (-not $uri.IsDefaultPort) { $port = ":$($uri.Port)" }
    $path = $uri.GetComponents(
        [UriComponents]::Path,
        [UriFormat]::UriEscaped
    ).TrimEnd('/')
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
    if (-not $path.StartsWith('/')) { $path = "/$path" }
    return "$scheme`://$hostName$port$path"
}

function Normalize-Source(
    $Descriptor,
    [string]$BaseDirectory,
    [switch]$FromReceipt
) {
    if ($null -eq $Descriptor) { Fail 'A source descriptor is required.' }
    $kind = [string](Get-PropertyValue $Descriptor 'kind' '')
    if (-not $kind) { $kind = [string](Get-PropertyValue $Descriptor 'source' '') }
    $kind = $kind.Trim().ToLowerInvariant()
    if ($kind -eq 'local') { $kind = 'directory' }
    if ($kind -eq 'url') { $kind = 'git' }
    $ref = [string](Get-PropertyValue $Descriptor 'ref' '')
    $canonicalInput = [string](Get-PropertyValue $Descriptor 'canonical' '')
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
                $repo = [string](Get-PropertyValue $Descriptor 'repo' '')
                if (-not $repo) { $repo = [string](Get-PropertyValue $Descriptor 'url' '') }
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
                $gitUrl = [string](Get-PropertyValue $Descriptor 'url' '')
            }
            $canonical = "git:$(Normalize-GitUrl $gitUrl)"
        }
        'opaque' {
            if ($canonicalInput) {
                $canonical = $canonicalInput
            }
            else {
                $opaqueId = [string](Get-PropertyValue $Descriptor 'id' '')
                if (-not $opaqueId) { $opaqueId = [string](Get-PropertyValue $Descriptor 'value' '') }
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
            $stableId = [string](Get-PropertyValue $Descriptor 'stableId' '')
            if ($canonicalInput) {
                if ($canonicalInput.StartsWith('directory-id:', [StringComparison]::Ordinal)) {
                    $canonical = $canonicalInput
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
                $directoryPath = [string](Get-PropertyValue $Descriptor 'path' '')
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

function Source-Identity($Source, [string]$ReadableName) {
    $record = Source-Record $Source
    $bytes = [Text.Encoding]::UTF8.GetBytes($record)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
    $slug = $ReadableName.ToLowerInvariant() -replace '[^a-z0-9]+', '-'
    $slug = $slug.Trim('-')
    if (-not $slug) { $slug = 'marketplace' }
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
            '.claude\settings.json',
            '.claude\settings.local.json',
            '.github\copilot\settings.json',
            '.github\copilot\settings.local.json'
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
            '.github\plugin\marketplace.json',
            'marketplace.json',
            '.plugin\marketplace.json',
            '.claude-plugin\marketplace.json'
        )) {
            $manifestPath = Join-Path $cursor $relativeManifest
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
            $manifest = Read-Json $manifestPath
            $metadata = Get-PropertyValue $manifest 'metadata'
            $pluginRoot = [string](Get-PropertyValue $metadata 'pluginRoot' '')
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
                $name = [string](Get-PropertyValue $plugin 'name' '')
                if ($RequestedPluginId -and $name -ne $RequestedPluginId) { continue }
                $sourcePath = [string](Get-PropertyValue $plugin 'source' '')
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
            $pluginIdFromManifest = [string](Get-PropertyValue $matches[0] 'name' '')
            $source = Normalize-Source ([pscustomobject]@{ source = 'directory'; path = $cursor }) $cursor
            return [pscustomobject]@{
                source = $source
                pluginId = $pluginIdFromManifest
                readableName = [string](Get-PropertyValue $manifest 'name' 'marketplace')
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
    if ([string](Get-PropertyValue $Locator 'kind' '') -ne
        [string](Get-PropertyValue $ReceiptLocator 'kind' '')) { return $false }
    if ($Locator.kind -eq 'installed') {
        return (([string](Get-PropertyValue $ReceiptLocator 'marketplaceKey' '')) -eq $Locator.marketplaceKey -and
                (Paths-Equal ([string](Get-PropertyValue $ReceiptLocator 'copilotHome' '')) $Locator.copilotHome))
    }
    if ($Locator.kind -eq 'directory') {
        return (Paths-Equal ([string](Get-PropertyValue $ReceiptLocator 'marketplaceRoot' '')) $Locator.marketplaceRoot)
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
    if ((Get-PropertyValue $namespace 'schema') -ne 'copilot-extensions.marketplace-namespace' -or
        (Get-PropertyValue $namespace 'version') -ne 1) {
        Fail "Namespace receipt '$actualReceipt' has an unsupported schema or version."
    }
    if ((Get-PropertyValue $namespace 'marketplaceId') -ne $marketplaceId) {
        Fail "Namespace receipt '$actualReceipt' does not match its cell directory."
    }
    $idMatch = [regex]::Match($marketplaceId, '^(.+)--([0-9a-f]{16})$')
    if (-not $idMatch.Success) {
        Fail "Invalid source-derived marketplace id '$marketplaceId'."
    }
    Assert-PositiveInteger (Get-PropertyValue $namespace 'generation') 'namespace.json generation'
    Assert-ReceiptState (Get-PropertyValue $namespace 'state') 'namespace.json state'
    $sourceReceipt = Get-PropertyValue $namespace 'source'
    $normalized = Normalize-Source ([pscustomobject]@{
        kind = Get-PropertyValue $sourceReceipt 'kind'
        canonical = Get-PropertyValue $sourceReceipt 'canonical'
        ref = Get-PropertyValue $sourceReceipt 'ref' ''
    }) '' -FromReceipt
    $identity = Source-Identity $normalized $idMatch.Groups[1].Value
    if ($identity.marketplaceId -ne $marketplaceId) {
        Fail "Namespace receipt '$actualReceipt' id does not match its normalized source."
    }
    if ((Get-PropertyValue $sourceReceipt 'fingerprint') -ne $identity.fingerprint) {
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
    if ((Get-PropertyValue $install 'schema') -ne 'copilot-extensions.plugin-installation' -or
        (Get-PropertyValue $install 'version') -ne 1) {
        Fail 'install.json has an unsupported schema or version.'
    }
    $marketplaceId = [string](Get-PropertyValue $install 'marketplaceId' '')
    $receiptPluginId = [string](Get-PropertyValue $install 'pluginId' '')
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
    if (-not (Paths-Equal ([string](Get-PropertyValue $install 'pluginRoot' '')) $expectedPluginRoot)) {
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
    Assert-PositiveInteger (Get-PropertyValue $install 'generation') 'install.json generation'
    Assert-ReceiptState (Get-PropertyValue $install 'state') 'install.json state'

    $namespacePath = Canonical-Path (Join-Path $cellRoot 'namespace.json')
    if (-not (Paths-Equal ([string](Get-PropertyValue $install 'namespaceReceipt' '')) $namespacePath)) {
        Fail 'install.json namespaceReceipt is not the exact namespace receipt in the same cell.'
    }
    $validatedNamespace = Validate-NamespaceReceipt $namespacePath $ResolvedDurableHome
    if ($validatedNamespace.marketplaceId -ne $marketplaceId) {
        Fail 'namespace.json marketplaceId does not match install.json.'
    }
    $identity = $validatedNamespace.identity

    $payload = Get-PropertyValue $install 'payload'
    $payloadPath = [string](Get-PropertyValue $payload 'root' '')
    if (-not [IO.Path]::IsPathRooted($payloadPath)) { Fail 'payload.root must be absolute.' }
    if ([string]::IsNullOrWhiteSpace([string](Get-PropertyValue $payload 'version' ''))) {
        Fail 'payload.version must be a non-empty string.'
    }
    if ([string](Get-PropertyValue $payload 'origin' '') -notin
        @('installed', 'directory', 'staged', 'explicit')) {
        Fail 'payload.origin must be installed, directory, staged, or explicit.'
    }
    $payloadPath = Canonical-Path $payloadPath
    if ($PayloadExpectation -and -not (Paths-Equal $payloadPath $PayloadExpectation)) {
        Fail "Expected payload '$PayloadExpectation', receipt names '$payloadPath'."
    }
    if ($env:COPILOT_PLUGIN_ROOT -and -not (Paths-Equal $payloadPath $env:COPILOT_PLUGIN_ROOT)) {
        Fail 'COPILOT_PLUGIN_ROOT conflicts with the validated payload root.'
    }

    $rootsReceipt = Get-PropertyValue $install 'roots'
    $rootNames = @('versions', 'snapshots', 'state', 'run', 'logs', 'cache', 'launchers')
    $roots = [ordered]@{}
    foreach ($name in $rootNames) {
        $roots[$name + 'Root'] = Resolve-RelativeRoot $expectedPluginRoot ([string](Get-PropertyValue $rootsReceipt $name '')) $name
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
        generation = Get-PropertyValue $install 'generation'
        state = Get-PropertyValue $install 'state'
    }
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
        $pointer = $Context
        if (-not $pointer) { $pointer = $env:COPILOT_EXTENSIONS_CONTEXT }
        if ($pointer) {
            $payloadExpectation = $PayloadRoot
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
            if (-not $PayloadRoot) { Fail 'resolve requires -PayloadRoot or COPILOT_PLUGIN_ROOT.' }
            if (-not [IO.Path]::IsPathRooted($PayloadRoot)) {
                Fail 'The payload root must be absolute.'
            }
            $resolvedPayload = Canonical-Path $PayloadRoot -MustExist
            if ($env:COPILOT_PLUGIN_ROOT -and -not (Paths-Equal $resolvedPayload $env:COPILOT_PLUGIN_ROOT)) {
                Fail 'COPILOT_PLUGIN_ROOT conflicts with -PayloadRoot.'
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
        }
    }
    $result | ConvertTo-Json -Depth 12 -Compress
    exit 0
}
catch {
    [Console]::Error.WriteLine("installation-context: $($_.Exception.Message)")
    exit 1
}
