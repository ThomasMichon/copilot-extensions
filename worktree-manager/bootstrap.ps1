# bootstrap.ps1 — fetch, version-install, and launch the copilot-extensions Worktree Manager.
#
#   iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.ps1)
#
# This is delivered OUTSIDE the plugin pipe: it does not require any Copilot
# plugin to be installed, and does not go through `copilot plugin`. It fetches
# the Worktree Manager payload, installs it under the SAME versioning convention as
# the harness's other installers — an immutable ~/.worktree-manager/versions/<ver>
# slot, a plain-text ~/.worktree-manager/current-version marker, and a
# ~/.local/bin/worktree-manager binstub — then launches it. Re-running is
# version-gated (a no-op when already current).
#
# Prerequisites are auto-provisioned: uv is installed user-local (no admin) when
# missing; git is installed best-effort where a package manager exists, otherwise
# the payload is fetched as a GitHub tarball so a bare machine still bootstraps.
# The git source (repo + ref) is taken from the user-level source config
# ($Root/config.toml [source]) when present -- set it with `worktree-manager
# source set` to track a fork / canary branch -- otherwise the canonical defaults
# below. Relocate the install root with $env:WORKTREE_MANAGER_ROOT.

$ErrorActionPreference = 'Stop'

# copilot-extensions:recovery-source-contract=1
if ($args.Count -eq 1 -and $args[0] -ceq '--describe-recovery-source') {
    [Console]::Out.WriteLine('{"schema":"copilot-extensions.recovery-source-capability","version":1,"request_versions":[1,2],"capabilities":["request-file","preview","install","main","commit","configured-main-pin","legacy-install-flow","prepared-install-source","repository-preview"]}')
    exit 0
}

if ($args.Count -eq 1 -and $args[0] -ceq '--describe-source-inputs') {
    [Console]::Out.WriteLine('{"schema":"copilot-extensions.manager-source-inputs","version":1,"capabilities":["install-source"]}')
    exit 0
}

if ($args.Count -eq 1 -and $args[0] -ceq '--describe-toolchain-config') {
    [Console]::Out.WriteLine('{"schema":"copilot-extensions.toolchain-config","version":1,"capabilities":["native-file","python-index","python-install-mirror","uv-install-mirror","npm-registry","request-context-v1","bootstrap-toolchain-only"]}')
    exit 0
}

# This grammar intentionally needs neither Python, uv, Node nor a TOML module.
$ToolchainConfig = $env:COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG
if ($args.Count -and $args[0] -ceq '--toolchain-config') {
    if ($args.Count -lt 2 -or [string]::IsNullOrWhiteSpace($args[1])) {
        throw '--toolchain-config requires an absolute file.'
    }
    if ($ToolchainConfig -and [IO.Path]::GetFullPath($ToolchainConfig) -cne [IO.Path]::GetFullPath($args[1])) {
        throw 'Explicit toolchain configuration conflicts with the environment selection.'
    }
    $ToolchainConfig = $args[1]
    $args = @($args | Select-Object -Skip 2)
}
if ($args -contains '--toolchain-config') { throw '--toolchain-config must occur once, before other arguments.' }
$ToolchainRoot = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } else {
    Join-Path $env:USERPROFILE '.worktree-manager'
}
if (-not $ToolchainConfig) {
    foreach ($candidate in @(
        (Join-Path (Get-Location).Path '.copilot-extensions\toolchain.conf'),
        (Join-Path $ToolchainRoot 'toolchain.conf')
    )) {
        if (Test-Path -LiteralPath $candidate) { $ToolchainConfig = $candidate; break }
    }
}
$ToolchainValues = @{}
$ToolchainEnvironment = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
if ($ToolchainConfig) {
    if (-not [IO.Path]::IsPathRooted($ToolchainConfig)) { throw 'Toolchain configuration must be absolute.' }
    $file = Get-Item -LiteralPath $ToolchainConfig -Force
    if ($file.PSIsContainer -or $file.Length -gt 16384 -or
        ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Toolchain configuration must be a small ordinary file.'
    }
    foreach ($byte in [IO.File]::ReadAllBytes($file.FullName)) {
        if ($byte -gt 126 -or ($byte -lt 32 -and $byte -notin @(9, 10, 13))) {
            throw 'Toolchain configuration must be plain ASCII text.'
        }
    }
    foreach ($line in [IO.File]::ReadAllLines($file.FullName)) {
        if ($line -match '^\s*(#.*)?$') { continue }
        if ($line -cnotmatch '^([a-z_]+)=([^\s]+)$') { throw 'Malformed toolchain configuration.' }
        $key, $value = $Matches[1], $Matches[2]
        if ($key -cnotin @('schema', 'python_index_url', 'python_install_mirror', 'uv_install_mirror', 'npm_registry') -or
            $ToolchainValues.ContainsKey($key)) { throw 'Unknown or duplicate toolchain configuration key.' }
        if (($key -ceq 'schema' -and $value -cne '1') -or
            ($key -cne 'schema' -and ($value.Length -gt 2048 -or
                $value -cnotmatch '^https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~:/+-]*)?$'))) {
            throw 'Unsupported toolchain schema or source URL.'
        }
        $ToolchainValues[$key] = $value
    }
    if ($ToolchainValues['schema'] -cne '1' -or $ToolchainValues.Count -lt 2) {
        throw 'Toolchain configuration requires schema=1 and a source.'
    }
    $mapping = @{
        python_index_url = @('UV_DEFAULT_INDEX', 'PIP_INDEX_URL', 'UV_INDEX_URL')
        python_install_mirror = @('UV_PYTHON_INSTALL_MIRROR')
        uv_install_mirror = @('UV_INSTALLER_GITHUB_BASE_URL')
        npm_registry = @('npm_config_registry', 'NPM_CONFIG_REGISTRY')
    }
    foreach ($key in $mapping.Keys) {
        if (-not $ToolchainValues.ContainsKey($key)) { continue }
        foreach ($name in $mapping[$key]) {
            $current = [Environment]::GetEnvironmentVariable($name)
            if ($current -and $current.TrimEnd('/') -cne $ToolchainValues[$key].TrimEnd('/')) {
                throw "$name conflicts with toolchain configuration."
            }
            if ($name -cne 'UV_INDEX_URL') { $ToolchainEnvironment[$name] = $ToolchainValues[$key] }
        }
    }
    if ($ToolchainValues.ContainsKey('python_index_url')) {
        if ($env:PIP_CONFIG_FILE -in @('NUL', '/dev/null')) {
            throw 'PIP_CONFIG_FILE disables native configuration.'
        }
        foreach ($name in @(
            'UV_INDEX', 'UV_EXTRA_INDEX_URL', 'UV_FIND_LINKS',
            'UV_INSECURE_HOST', 'UV_NO_INDEX', 'PIP_EXTRA_INDEX_URL',
            'PIP_FIND_LINKS', 'PIP_TRUSTED_HOST', 'PIP_NO_INDEX'
        )) {
            if ([Environment]::GetEnvironmentVariable($name)) { throw "$name requires explicit source reconciliation." }
        }
    }
    if ($ToolchainValues.ContainsKey('python_index_url') -or $ToolchainValues.ContainsKey('python_install_mirror')) {
        foreach ($name in @('UV_CONFIG_FILE', 'UV_NO_CONFIG')) {
            if ([Environment]::GetEnvironmentVariable($name)) { throw "$name requires explicit source reconciliation." }
        }
    }
    if ($ToolchainValues.ContainsKey('uv_install_mirror')) {
        foreach ($name in @('UV_DOWNLOAD_URL', 'INSTALLER_DOWNLOAD_URL', 'UV_INSTALLER_GHE_BASE_URL')) {
            if ([Environment]::GetEnvironmentVariable($name)) { throw "$name requires explicit source reconciliation." }
        }
    }
    $toolchainWindows = [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT
    $toolchainDarwin = [bool](Get-Variable -Name IsMacOS -ValueOnly -ErrorAction SilentlyContinue)
    $appdata = if ($toolchainWindows) {
        if ($env:APPDATA) { $env:APPDATA } else { Join-Path $env:USERPROFILE 'AppData\Roaming' }
    } else {
        if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $env:HOME '.config' }
    }
    $nativeConfigs = @()
    if ($ToolchainValues.ContainsKey('python_index_url')) {
        if ($toolchainWindows) {
            $bases = @($env:PROGRAMDATA)
            if (-not $env:PIP_CONFIG_FILE) { $bases += @($env:USERPROFILE, $appdata) }
            foreach ($base in $bases) {
                if ($base) { $nativeConfigs += ,@((Join-Path $base 'pip\pip.ini'), 'pip') }
            }
        } elseif ($toolchainDarwin) {
            $nativeConfigs += ,@('/Library/Application Support/pip/pip.conf', 'pip')
            if (-not $env:PIP_CONFIG_FILE) {
                $pipDirectory = Join-Path $env:HOME 'Library/Application Support/pip'
                if (-not (Test-Path -LiteralPath $pipDirectory -PathType Container)) {
                    $pipDirectory = Join-Path $env:HOME '.config/pip'
                }
                $nativeConfigs += ,@((Join-Path $env:HOME '.pip/pip.conf'), 'pip')
                $nativeConfigs += ,@((Join-Path $pipDirectory 'pip.conf'), 'pip')
            }
        } else {
            $nativeConfigs += ,@('/etc/pip.conf', 'pip')
            if (-not $env:PIP_CONFIG_FILE) {
                $nativeConfigs += ,@((Join-Path $env:HOME '.pip/pip.conf'), 'pip')
                $nativeConfigs += ,@((Join-Path $appdata 'pip/pip.conf'), 'pip')
            }
            $xdgDirs = if ($env:XDG_CONFIG_DIRS) { $env:XDG_CONFIG_DIRS } else { '/etc/xdg' }
            foreach ($base in ($xdgDirs -split ':')) {
                $nativeConfigs += ,@((Join-Path $base 'pip/pip.conf'), 'pip')
            }
        }
        if ($env:VIRTUAL_ENV) {
            $site = if ($toolchainWindows) { 'pip.ini' } else { 'pip.conf' }
            $nativeConfigs += ,@((Join-Path $env:VIRTUAL_ENV $site), 'pip')
        }
        if ($env:PIP_CONFIG_FILE) {
            if (-not (Test-Path -LiteralPath $env:PIP_CONFIG_FILE -PathType Leaf)) {
                throw 'Explicit pip configuration is unavailable.'
            }
            $nativeConfigs += ,@($env:PIP_CONFIG_FILE, 'pip')
        }
    }
    if ($ToolchainValues.ContainsKey('python_index_url') -or $ToolchainValues.ContainsKey('python_install_mirror')) {
        $nativeConfigs += ,@((Join-Path $appdata 'uv\uv.toml'), 'uv')
        $systemCandidates = if ($toolchainWindows) {
            if ($env:PROGRAMDATA) { Join-Path $env:PROGRAMDATA 'uv\uv.toml' }
        } else {
            $uvSystemDirs = if ($env:XDG_CONFIG_DIRS) { $env:XDG_CONFIG_DIRS } else { '/etc/xdg' }
            if ($uvSystemDirs) {
                foreach ($base in ($uvSystemDirs -split ':')) {
                    if ($base) { Join-Path $base 'uv/uv.toml' }
                }
            }
            '/etc/uv/uv.toml'
        }
        foreach ($candidate in $systemCandidates) {
            if (Test-Path -LiteralPath $candidate) {
                $nativeConfigs += ,@($candidate, 'uv')
                break
            }
        }
    }
    if ($ToolchainValues.ContainsKey('npm_registry')) {
        foreach ($suffix in @('USERCONFIG', 'GLOBALCONFIG')) {
            $upper = [Environment]::GetEnvironmentVariable("NPM_CONFIG_$suffix")
            $lower = [Environment]::GetEnvironmentVariable("npm_config_$($suffix.ToLowerInvariant())")
            if ($upper -and $lower -and
                $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($upper) -cne
                $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($lower)) {
                throw 'npm configuration-file selections conflict.'
            }
        }
        $npmUser = if ($env:NPM_CONFIG_USERCONFIG) { $env:NPM_CONFIG_USERCONFIG } else {
            if ($env:npm_config_userconfig) { $env:npm_config_userconfig } else {
                $npmHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
                Join-Path $npmHome '.npmrc'
            }
        }
        $nativeConfigs += ,@($npmUser, 'npm')
        $npmGlobal = if ($env:NPM_CONFIG_GLOBALCONFIG) { $env:NPM_CONFIG_GLOBALCONFIG } else {
            $env:npm_config_globalconfig
        }
        if ($npmGlobal) { $nativeConfigs += ,@($npmGlobal, 'npm') }
    }
    foreach ($entry in $nativeConfigs) {
        $path, $kind = $entry
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer -or $item.Length -gt 1048576 -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Native tool configuration is not a bounded ordinary file.'
        }
        $indexCount = 0
        $defaultCount = 0
        foreach ($line in [IO.File]::ReadAllLines($item.FullName)) {
            if ($line -match '^\s*(#|;|$)') { continue }
            if ($kind -eq 'pip') {
                if ($line -match '^\s*(?:extra[-_]index[-_]url|find[-_]links|trusted[-_]host|no[-_]index)\s*[:=]') {
                    throw 'Additional pip source selectors require reconciliation.'
                }
                if ($line -match '^\s*index[-_]url\s*[:=]\s*(.*?)\s*$' -and
                    $Matches[1].TrimEnd('/') -cne $ToolchainValues['python_index_url'].TrimEnd('/')) {
                    throw 'Existing pip index conflicts with toolchain configuration.'
                }
            } elseif ($kind -eq 'uv') {
                if (-not $ToolchainValues.ContainsKey('python_index_url') -and
                    $line -notmatch '^\s*["'']?python-install-mirror["'']?\s*[=.]') { continue }
                if (-not $ToolchainValues.ContainsKey('python_install_mirror') -and
                    $line -match '^\s*["'']?python-install-mirror["'']?\s*[=.]') { continue }
                if ($line -match '^\s*["'']?pip["'']?\s*[=.]' -and
                    $line -match 'index|find-links|insecure-host') {
                    throw 'Inline or dotted uv pip sources require reconciliation.'
                }
                if ($line -match '^\s*["'']?explicit["'']?\s*[=.]' -and
                    $line -notmatch '^\s*["'']?explicit["'']?\s*=\s*false\s*(?:#.*)?$') {
                    throw 'Explicit-only uv indexes require reconciliation.'
                }
                if ($line -match '^\s*["'']?(?:extra-index-url|find-links|no-index|no-default-index|allow-insecure-host|index)["'']?\s*=') {
                    throw 'Legacy or additional uv sources require reconciliation.'
                }
                if ($line -match '^\s*\[\[\s*["'']?index["'']?\s*\]\]\s*(?:#.*)?$') { $indexCount++ }
                if ($line -match '^\s*["'']?default["'']?\s*=\s*true\s*(?:#.*)?$') { $defaultCount++ }
                if ($line -match '^\s*["'']?(url|index-url|python-install-mirror)["'']?\s*=\s*["'']([^"'']+)["'']\s*(?:#.*)?$') {
                    $field = if ($Matches[1] -eq 'python-install-mirror') { 'python_install_mirror' } else { 'python_index_url' }
                    if ($ToolchainValues.ContainsKey($field) -and
                        $Matches[2].TrimEnd('/') -cne $ToolchainValues[$field].TrimEnd('/')) {
                        throw 'Existing uv source conflicts with toolchain configuration.'
                    }
                } elseif ($line -match '^\s*["'']?(url|index|index-url|python-install-mirror)["'']?\s*[=.]') {
                    throw 'Unsupported uv source syntax requires reconciliation.'
                }
            } else {
                if ($line -match '^\s*[^=]+:registry\s*=') { throw 'Scoped npm registries require explicit reconciliation.' }
                if ($line -match '^\s*registry\s*=\s*(.*?)\s*$' -and
                    $Matches[1].TrimEnd('/') -cne $ToolchainValues['npm_registry'].TrimEnd('/')) {
                    throw 'Existing npm registry conflicts with toolchain configuration.'
                }
            }
        }
        if ($kind -eq 'uv' -and $ToolchainValues.ContainsKey('python_index_url') -and
            ($indexCount -gt 1 -or $indexCount -ne $defaultCount)) {
            throw 'Existing uv indexes require explicit reconciliation.'
        }
    }
    $ToolchainConfig = $file.FullName
    foreach ($name in @(
        'PIP_CONFIG_FILE', 'NPM_CONFIG_USERCONFIG', 'npm_config_userconfig',
        'NPM_CONFIG_GLOBALCONFIG', 'npm_config_globalconfig'
    )) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) {
            $ToolchainEnvironment[$name] = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($value)
        }
    }
    foreach ($name in $ToolchainEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $ToolchainEnvironment[$name], 'Process')
    }
    $env:COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG = $ToolchainConfig
}

$BootstrapSourceAnchor = $env:WORKTREE_MANAGER_SOURCE_ANCHOR
if (-not $BootstrapSourceAnchor) { $BootstrapSourceAnchor = (Get-Location).Path }
if (-not [IO.Path]::IsPathRooted($BootstrapSourceAnchor) -or
    ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
     [IO.Path]::GetPathRoot($BootstrapSourceAnchor) -match '^(?:[/\\]|[A-Za-z]:)$')) {
    throw 'Update request source anchor must be absolute.'
}

if ($env:BOOTSTRAP_KILLSWITCH_STATE_FILE -and
    -not $env:BOOTSTRAP_KILLSWITCH_STATE_FILE.StartsWith('~')) {
    $holdPath = $env:BOOTSTRAP_KILLSWITCH_STATE_FILE
    if (-not [IO.Path]::IsPathRooted($holdPath)) {
        $holdPath = Join-Path (Get-Location).Path $holdPath
    }
    $env:BOOTSTRAP_KILLSWITCH_STATE_FILE = [IO.Path]::GetFullPath($holdPath)
}

$BootstrapClock = [Diagnostics.Stopwatch]::StartNew()
function Write-BootstrapProgress(
    [string]$Message,
    [ValidateSet('running', 'warning', 'failed')][string]$State = 'running'
) {
    $stamp = [DateTime]::UtcNow.ToString('HH:mm:ss.fffZ', [Globalization.CultureInfo]::InvariantCulture)
    $elapsed = $BootstrapClock.Elapsed.TotalSeconds.ToString('F1', [Globalization.CultureInfo]::InvariantCulture)
    foreach ($line in [regex]::Split($Message, '\r\n|\n|\r')) {
        [Console]::Error.WriteLine("[$stamp] update/bootstrap $State $line [elapsed ${elapsed}s]")
    }
}

function Resolve-BootstrapExecutable([string]$Path) {
    $root = [IO.Path]::GetPathRoot($Path)
    if (-not $root) { throw 'Bootstrap executable must have an absolute path.' }
    $remaining = @($Path.Substring($root.Length) -split '[\\/]' | Where-Object { $_ })
    $current = $root
    $links = 0
    $ordinaryFile = $false
    while ($remaining.Count) {
        $component = $remaining[0]
        $remaining = @($remaining | Select-Object -Skip 1)
        if ($component -eq '.') { continue }
        if ($component -eq '..') {
            $parent = [IO.Path]::GetDirectoryName($current)
            if ($parent) { $current = $parent }
            continue
        }
        $candidate = [IO.Path]::Combine($current, $component)
        $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $ordinaryFile = $false
            $links++
            $targets = @($item.Target)
            if ($links -gt 40 -or $targets.Count -ne 1 -or -not $targets[0]) {
                throw 'Bootstrap executable has an unsupported link or excessive indirection.'
            }
            $target = [string]$targets[0]
            if (-not [IO.Path]::IsPathRooted($target)) { $target = [IO.Path]::Combine($current, $target) }
            $current = [IO.Path]::GetPathRoot($target)
            $remaining = @($target.Substring($current.Length) -split '[\\/]' |
                Where-Object { $_ }) + $remaining
            continue
        }
        if ([bool]$item.PSIsContainer -ne [bool]$remaining.Count) {
            throw 'Bootstrap executable does not identify a regular file.'
        }
        $ordinaryFile = -not $item.PSIsContainer
        $current = $candidate
    }
    if (-not $ordinaryFile) { throw 'Bootstrap executable does not identify a regular file.' }
    return $current
}

function Get-RegisteredBootstrapPython {
    foreach ($root in @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($version in @(Get-ChildItem -LiteralPath $root -ErrorAction Stop)) {
            $install = Join-Path $version.PSPath 'InstallPath'
            if (-not (Test-Path -LiteralPath $install)) { continue }
            $properties = Get-ItemProperty -LiteralPath $install -ErrorAction Stop
            $executable = $properties.PSObject.Properties['ExecutablePath']
            if ($executable -and $executable.Value -is [string] -and $executable.Value) {
                $executable.Value
                continue
            }
            $directory = $properties.PSObject.Properties['(default)']
            if ($directory -and $directory.Value -is [string] -and $directory.Value) {
                Join-Path $directory.Value 'python.exe'
            }
        }
    }
}

function Initialize-BootstrapSignatureVerification {
    $module = Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1'
    Import-Module -Name $module -ErrorAction Stop
}

function Find-PhysicalBootstrapPython([switch]$RequireSignature) {
    if ($RequireSignature) { Initialize-BootstrapSignatureVerification }
    $inspected = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $candidates = @(
        foreach ($name in @('python3', 'python')) {
            Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue |
                ForEach-Object { $_.Source }
        }
        if ($RequireSignature) { Get-RegisteredBootstrapPython }
    )
    foreach ($candidate in $candidates) {
        try {
            if ($candidate -match '[\\/]WindowsApps[\\/]') { continue }
            $physical = Resolve-BootstrapExecutable $candidate
            foreach ($attempt in 1..2) {
                if ($physical -match '[\\/]WindowsApps[\\/]') { break }
                if ($RequireSignature) {
                    if (-not $inspected.Add($physical)) { break }
                    if ([IO.Path]::GetExtension($physical) -ine '.exe') { break }
                    $signature = Get-AuthenticodeSignature -LiteralPath $physical -ErrorAction Stop
                    if (-not $signature -or $signature.Status -eq 'UnknownError') {
                        throw 'System Python signature verification could not complete.'
                    }
                    if ($signature.Status -ne 'Valid') { break }
                }
                $probe = @(& $physical -I -S -B -c "import json,sys; print(json.dumps({'version': list(sys.version_info[:2]), 'base': sys.prefix == sys.base_prefix, 'executable': sys._base_executable}))" 2>$null)
                if ($LASTEXITCODE -ne 0 -or $probe.Count -ne 1) { break }
                $info = $probe[0] | ConvertFrom-Json -ErrorAction Stop
                if ($info.base -isnot [bool] -or -not $info.base -or $info.version.Count -ne 2) { break }
                if (($info.version[0] -isnot [int] -and $info.version[0] -isnot [long]) -or
                    ($info.version[1] -isnot [int] -and $info.version[1] -isnot [long])) { break }
                if ($info.version[0] -lt 3 -or ($info.version[0] -eq 3 -and $info.version[1] -lt 11)) { break }
                if ($info.executable -isnot [string] -or -not [IO.Path]::IsPathRooted($info.executable)) { break }
                $base = Resolve-BootstrapExecutable $info.executable
                if ($base -ieq $physical) { return $physical }
                $physical = $base
            }
        } catch {
            if ($RequireSignature -and $_.Exception -isnot [Management.Automation.ItemNotFoundException]) {
                throw
            }
            continue
        }
    }
    return $null
}

function Test-BootstrapInteractive {
    return (
        $Host.Name -eq 'ConsoleHost' -and [Environment]::UserInteractive -and
        -not $env:COPILOT_AGENT_SESSION_ID -and -not $env:CI -and
        -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected -and
        -not ([Environment]::GetCommandLineArgs() -match '^-[Nn][Oo][Nn][Ii]\w*$')
    )
}

function Find-SystemBootstrapPython {
    $python = Find-PhysicalBootstrapPython -RequireSignature
    if (-not $python) {
        $winget = Get-Command winget -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if (-not $winget) { throw 'System Python bootstrap requires WinGet when no signed Python 3.11+ is available.' }
        if (-not (Test-BootstrapInteractive)) {
            throw 'System Python acquisition requires a real interactive Windows terminal; no package was installed.'
        }
        Write-BootstrapProgress 'No signed Python 3.11+ found; installing user-scoped Python through WinGet...'
        & $winget.Source install --id Python.Python.3.11 --exact --source winget --scope user `
            --accept-source-agreements --accept-package-agreements |
            ForEach-Object { [Console]::Error.WriteLine([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw "System Python acquisition failed (WinGet exit $LASTEXITCODE)." }
        $python = Find-PhysicalBootstrapPython -RequireSignature
        if (-not $python) {
            throw 'WinGet returned success, but no usable signed Python 3.11+ was found; refusing managed Python fallback.'
        }
    }
    $directory = [IO.Path]::GetDirectoryName($python)
    $env:PATH = Add-BootstrapPythonPath $env:PATH $directory
    return $python
}

function Add-BootstrapPythonPath([string]$Path, [string]$Directory) {
    if (-not [IO.Path]::IsPathRooted($Directory) -or $Directory -match '[;\x00-\x1f]') {
        throw 'Bootstrap Python directory cannot be represented safely on PATH.'
    }
    $remaining = @()
    if ($Path) {
        $remaining = @($Path -split ';' | Where-Object {
            -not [string]::Equals($_, $Directory, [StringComparison]::Ordinal)
        })
    }
    return (@($Directory) + $remaining) -join ';'
}

function Test-BootstrapUvVersion([string]$Uv) {
    $ErrorActionPreference = 'Continue'
    $output = @(& $Uv --version 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw ('uv version qualification failed: ' + ($output -join "`n"))
    }
    $output = @($output | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] })
    $match = [regex]::Match(($output -join "`n"), '\Auv[ \t]+([0-9]+\.[0-9]+\.[0-9]+)(?:[ \t(+]|$)')
    if (-not $match.Success) { throw 'uv returned an invalid version report.' }
    return [version]$match.Groups[1].Value -ge [version]'0.8.0'
}

function Find-BootstrapUv {
    foreach ($command in @(Get-Command uv -CommandType Application -All -ErrorAction SilentlyContinue)) {
        try {
            $candidate = Resolve-BootstrapExecutable $command.Source
            if (Test-BootstrapUvVersion $candidate) { return $candidate }
        } catch {
            if ($_ -match '(?i)(?:os error|WinError)\s+448\b') { throw }
        }
    }
    return $null
}

function Find-BootstrapPython {
    param(
        [string]$Uv,
        [ValidateSet('Inspect', 'Prepare')][string]$Operation = 'Prepare'
    )
    $ErrorActionPreference = 'Continue'
    $lookup = @(& $Uv python find --no-project --system --managed-python --no-python-downloads 3.11 2>&1)
    $lookupExit = $LASTEXITCODE
    if ($lookupExit -eq 0) {
        $output = @($lookup | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] })
        if ($output.Count -eq 1 -and -not [string]::IsNullOrWhiteSpace([string]$output[0])) {
            if ($Operation -eq 'Inspect') {
                $selected = [string]$output[0]
                $root = [IO.Path]::GetPathRoot($selected)
                if (-not [IO.Path]::IsPathRooted($selected) -or -not $root -or
                    ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
                        ($root -in @('\', '/') -or $root -match '^[A-Za-z]:$'))) {
                    throw 'Manager managed Python inspection returned an invalid executable.'
                }
                return Resolve-BootstrapExecutable $selected
            }
            return [string]$output[0]
        }
        throw 'Manager Python lookup returned an invalid executable.'
    }
    if (($lookup -join "`n") -match '(?i)(?:os error|WinError)\s+448\b') {
        throw ('Manager managed Python lookup is blocked by operating-system policy: ' + ($lookup -join "`n"))
    }
    if ($Operation -eq 'Inspect') {
        throw ('Recovery requires prepared uv-managed Python 3.11; run explicit Manager setup first. ' +
            'No interpreter was installed. Lookup failed: ' + ($lookup -join "`n"))
    }
    & $Uv python install --no-bin --no-registry 3.11 | ForEach-Object { [Console]::Error.WriteLine([string]$_) }
    if ($LASTEXITCODE -ne 0) { throw 'Manager base Python acquisition failed.' }
    $python = @(& $Uv python find --no-project --system --managed-python --no-python-downloads 3.11 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw ('Manager uv-managed Python 3.11 lookup failed: ' + ($python -join "`n"))
    }
    $python = @($python | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] })
    if ($python.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$python[0])) {
        throw 'Manager uv-managed Python 3.11 lookup returned an invalid executable.'
    }
    return [string]$python[0]
}

if ($args -ccontains '--recover-source' -or $args -ccontains '--describe-recovery-source') {
    if ($args.Count -ne 2 -or $args[0] -cne '--recover-source' -or
        -not [IO.Path]::IsPathRooted($args[1])) {
        throw 'Recovery requires only --recover-source <absolute-request-json-path>.'
    }
    $requestRoot = [IO.Path]::GetPathRoot($args[1])
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
        ($requestRoot -in @('\', '/') -or $requestRoot -match '^[A-Za-z]:$')) {
        throw 'Recovery request path must be fully qualified, not drive-relative.'
    }
    $request = [IO.Path]::GetFullPath($args[1])
    if (-not (Test-Path -LiteralPath $request -PathType Leaf)) { throw 'Recovery request is unavailable.' }
    $retainToolkit = $false
    try {
        if ((Get-Item -LiteralPath $request).Length -le 65536) {
            $retainToolkit = (([IO.File]::ReadAllText($request) | ConvertFrom-Json).operation -ceq 'install')
        }
    } catch { }
    # Neither configured transport nor an installed dispatcher supplies this toolkit.
    $uv = Find-BootstrapUv
    if (-not $uv) {
        throw 'Recovery requires uv 0.8.0+ on PATH and prepared uv-managed Python 3.11; run explicit Manager setup first. Nothing was provisioned.'
    }
    $python = Find-BootstrapPython $uv -Operation Inspect
    if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Recovery requires git; nothing was provisioned.'
    }
    $workspace = Join-Path ([IO.Path]::GetTempPath()) ('source-recovery.' + [Guid]::NewGuid().ToString('N'))
    $savedGit = @{}
    foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'GIT_*' })) {
        $savedGit[$item.Name] = $item.Value
        [Environment]::SetEnvironmentVariable($item.Name, $null, 'Process')
    }
    try {
        $env:GIT_CONFIG_NOSYSTEM = '1'
        $env:GIT_CONFIG_GLOBAL = [IO.Path]::Combine($workspace, 'no-global-config')
        $env:GIT_TERMINAL_PROMPT = '0'
        New-Item -ItemType Directory -Path $workspace | Out-Null
        $toolkit = Join-Path $workspace 'toolkit'
        & git -c core.hooksPath= -c core.fsmonitor=false -c core.longpaths=true `
            clone --quiet --single-branch --branch main -- `
            'https://github.com/ThomasMichon/copilot-extensions.git' $toolkit
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $python -I -S -B -X utf8 (Join-Path $toolkit 'tools\recover_source.py') --request $request
        $recoveryExit = $LASTEXITCODE
    } finally {
        foreach ($item in @(Get-ChildItem Env: | Where-Object { $_.Name -like 'GIT_*' })) {
            [Environment]::SetEnvironmentVariable($item.Name, $null, 'Process')
        }
        foreach ($name in $savedGit.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedGit[$name], 'Process')
        }
        if ($retainToolkit) {
            [Console]::Error.WriteLine("Recovery toolkit retained: $workspace")
        } elseif (Test-Path -LiteralPath $workspace) {
            Remove-Item -LiteralPath $workspace -Recurse -Force
        }
    }
    exit $recoveryExit
}

$Root = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } else { Join-Path $env:USERPROFILE '.worktree-manager' }
$Staging = Join-Path $Root 'staging'
$Config  = Join-Path $Root 'config.toml'

# Source (repo + ref): user-level config file [source] overrides, else defaults.
$Repo = 'https://github.com/ThomasMichon/copilot-extensions.git'
$Ref  = 'main'
function ConvertFrom-BootstrapSourceString([string]$Text) {
    if (-not $Text -or $Text[0] -cnotin @([char]'"', [char]"'")) {
        throw 'Unsupported or malformed source string.'
    }
    $quote = $Text[0]
    $value = [Text.StringBuilder]::new()
    for ($i = 1; $i -lt $Text.Length; $i++) {
        $character = $Text[$i]
        if ($character -ceq $quote) {
            if ($Text.Substring($i + 1) -notmatch '^\s*(#.*)?$' -or -not $value.Length) {
                throw 'Unsupported or malformed source string.'
            }
            return $value.ToString()
        }
        if ([char]::IsControl($character)) { throw 'Control characters in source string.' }
        if ($quote -ceq [char]'"' -and $character -ceq [char]'\') {
            $i++
            if ($i -ge $Text.Length -or $Text[$i] -cnotin @([char]'\', [char]'"')) {
                throw 'Unsupported or malformed source escape.'
            }
            $character = $Text[$i]
        }
        [void]$value.Append($character)
    }
    throw 'Unterminated source string.'
}
$explicitSource = ($args.Count -ge 4 -and $args[0] -ceq '--repair-update' -and $args[3] -ceq 'update') -or
    ($args.Count -and $args[0] -ceq '--install-source')
if ((Test-Path -LiteralPath $Config) -and -not $explicitSource) {
    $inSource = $false
    $seen = @{}
    $sourceSeen = $false
    foreach ($line in [IO.File]::ReadAllLines($Config)) {
        if ($line -match '^\s*\[') {
            $inSource = $line -cmatch '^\s*\[source\]\s*(#.*)?$'
            if ($line -cmatch '^\s*\[\[?\s*(?:source|["'']source["''])(?:[^A-Za-z0-9_-]|$)' -and
                -not $inSource) { throw 'Unsupported source table syntax.' }
            if ($inSource -and $sourceSeen) { throw 'Duplicate source table.' }
            if ($inSource) { $sourceSeen = $true }
            continue
        }
        if (-not $inSource -and $line -cmatch '^\s*["'']?source["'']?[\s.=]') { throw 'Unsupported inline source table.' }
        if ($inSource -and $line -cmatch '^\s*(repo|ref)\s*=\s*(.*)$') {
            $key = $Matches[1]
            $encodedValue = $Matches[2]
            if ($seen.ContainsKey($key)) { throw 'Duplicate source key.' }
            $seen[$key] = $true
            $value = ConvertFrom-BootstrapSourceString $encodedValue
            if ($key -ceq 'repo') { $Repo = $value } else { $Ref = $value }
        } elseif ($inSource -and $line -notmatch '^\s*(#.*)?$') {
            throw 'Unsupported source key syntax.'
        }
    }
}

$installerArgs = @()
$commandArgs = @($args)
$InstallSource = $null
if ($commandArgs.Count -and $commandArgs[0] -ceq '--install-source') {
    if ($commandArgs.Count -lt 2 -or -not [IO.Path]::IsPathRooted($commandArgs[1])) {
        throw '--install-source requires an absolute immutable source snapshot.'
    }
    $InstallSource = [IO.Path]::GetFullPath($commandArgs[1])
    $sourceItem = Get-Item -LiteralPath $InstallSource -Force
    if (-not $sourceItem.PSIsContainer -or ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw '--install-source requires an ordinary directory.'
    }
    $installerArgs += @('--install-source', $InstallSource)
    $commandArgs = @($commandArgs | Select-Object -Skip 2)
    if ($commandArgs.Count -and $commandArgs[0] -in @(
        '--install-source', '--recover-for-repair', '--repair-update', '--bootstrap-update', '--system-python'
    )) { throw 'Local installation source cannot be combined with another source or repair entry.' }
}
$SystemPythonRequested = $commandArgs.Count -and $commandArgs[0] -ceq '--system-python'
if ($SystemPythonRequested) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw '--system-python requires the Windows WinGet bootstrap; unsupported on POSIX.'
    }
    $commandArgs = @($commandArgs | Select-Object -Skip 1)
    if ($commandArgs.Count -and $commandArgs[0] -in @(
        '--system-python', '--recover-for-repair', '--repair-update', '--bootstrap-update'
    )) { throw 'System Python mode cannot be repeated or combined with bootstrap repair.' }
}
$RecoveryStaging = $null
$UpdateStaging = $null
$commandExit = $null
if ($commandArgs.Count -and $commandArgs[0] -ceq '--recover-for-repair') {
    $installerArgs += '--recover-for-repair'
    $commandArgs = @($commandArgs | Select-Object -Skip 1)
    if ($commandArgs.Count -and $commandArgs[0] -ceq '--json') {
        $installerArgs += '--json'
        $commandArgs = @($commandArgs | Select-Object -Skip 1)
    }
    if ($commandArgs.Count) { throw 'Repair recovery accepts only --json.' }
    $RecoveryStaging = Join-Path ([IO.Path]::GetTempPath()) ('manager-repair.' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $RecoveryStaging | Out-Null
    $Staging = Join-Path $RecoveryStaging 'source'
}
if ($commandArgs.Count -and $commandArgs[0] -ceq '--bootstrap-update') {
    if ($commandArgs.Count -lt 2 -or $commandArgs[1] -cne 'update') {
        throw 'Bootstrap update only accepts update.'
    }
    $installerArgs += '--repair-update'
    $commandArgs = @($commandArgs | Select-Object -Skip 1)
} elseif ($commandArgs.Count -and $commandArgs[0] -ceq '--repair-update') {
    if ($commandArgs.Count -lt 4 -or $commandArgs[3] -cne 'update') {
        throw 'Incomplete or invalid update bootstrap source/command.'
    }
    $Repo = $commandArgs[1]
    $Ref = $commandArgs[2]
    $installerArgs += '--repair-update'
    $commandArgs = @($commandArgs | Select-Object -Skip 3)
}
$ToolchainOnly = $commandArgs.Count -and $commandArgs[0] -ceq 'setup' -and
    ($commandArgs -ccontains '--toolchain-only')
if (($commandArgs.Count -and $commandArgs[0] -ceq 'update') -or $ToolchainOnly) {
    $acquisitionKind = if ($ToolchainOnly) { 'toolchain' } else { 'update' }
    $UpdateStaging = Join-Path $Root ('bootstrap-acquisition\' + $acquisitionKind + '.' + [Guid]::NewGuid().ToString('N'))
    $Staging = Join-Path $UpdateStaging 'source'
}
if (-not $Repo -or $Repo.StartsWith('-') -or -not $Ref -or $Ref.StartsWith('-') -or
    $Repo -match '[\x00-\x1f\x7f]' -or $Ref -match '[\x00-\x1f\x7f]') {
    throw 'Invalid configured Git source/ref; source preserved.'
}
if ($Repo -notmatch '^[^/\\:]+:' -or [IO.Path]::IsPathRooted($Repo)) {
    if (-not [IO.Path]::IsPathRooted($Repo)) { $Repo = Join-Path $BootstrapSourceAnchor $Repo }
    $Repo = [IO.Path]::GetFullPath($Repo)
}
try {
Write-BootstrapProgress 'Worktree Manager bootstrap'
$SystemPython = if ($SystemPythonRequested) { Find-SystemBootstrapPython } else { $null }

# -- Prerequisites (auto-provision; restart-aware; user-local first) ----------
# The one-liner must take a *bare* machine into the Manager (vision installer
# one-line-bootstrap). uv is auto-installed (user-local, no admin). git is
# installed best-effort where a package manager exists, else we fetch the payload
# as a GitHub tarball so the bootstrap never dead-ends without git.

$LocalBin = Join-Path $env:USERPROFILE '.local\bin'

$Uv = Find-BootstrapUv
if (-not $Uv) {
    Write-BootstrapProgress 'uv 0.8.0+ required - preparing it (user-local, no admin)...'
    try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression }
    catch { throw "uv install failed: $_" }
    # uv installs into ~\.local\bin; amend THIS session's PATH so we can continue
    # without a restart (the installer persists PATH for future shells).
    $env:PATH = $LocalBin + [IO.Path]::PathSeparator + $env:PATH
    $Uv = Find-BootstrapUv
    if (-not $Uv) { throw 'uv 0.8.0 or newer is required after prerequisite preparation.' }
}

$HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
if (-not $HaveGit -and -not $InstallSource) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-BootstrapProgress 'git not found - attempting winget install (may prompt for elevation)...'
        try {
            winget install --id Git.Git -e --source winget `
                --accept-source-agreements --accept-package-agreements | Out-Null
        } catch { }
        $gitCmd = Join-Path $env:ProgramFiles 'Git\cmd'
        if (Test-Path (Join-Path $gitCmd 'git.exe')) { $env:PATH = "$gitCmd;$env:PATH" }
        $HaveGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
    }
    if (-not $HaveGit) { Write-BootstrapProgress 'git unavailable - will fetch the payload as a tarball (no git).' }
}

# -- Fetch the payload (git clone/fetch when available, else GitHub tarball) ---
if ($InstallSource -and $PSScriptRoot -and
    (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'scripts\runtime_install.py') -PathType Leaf)) {
    $PayloadParent = Split-Path -Parent $PSScriptRoot
} elseif ($HaveGit) {
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    if (-not (Test-Path (Join-Path $Staging '.git'))) {
        if (Test-Path $Staging) { Remove-Item -Recurse -Force $Staging }
        git init --quiet -- $Staging
        if ($LASTEXITCODE -ne 0) {
            Write-BootstrapProgress "git init failed (exit $LASTEXITCODE)." failed
            exit $LASTEXITCODE
        }
    }
    git -C $Staging fetch --depth 1 -- $Repo $Ref | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress "git fetch of $Repo@$Ref failed (exit $LASTEXITCODE)." failed
        exit $LASTEXITCODE
    }
    if (Test-Path (Join-Path $Staging '.git\info\sparse-checkout')) {
        # Routine self-update stages only Manager; fresh repair also needs the engine SDK.
        git -C $Staging -c core.longpaths=true -c core.hooksPath= sparse-checkout disable
        if ($LASTEXITCODE -ne 0) {
            Write-BootstrapProgress "Expanding bootstrap source failed (exit $LASTEXITCODE)." failed
            exit $LASTEXITCODE
        }
    }
    git -C $Staging -c core.longpaths=true -c core.hooksPath= checkout -q --detach FETCH_HEAD
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress "git checkout of $Repo@$Ref failed (exit $LASTEXITCODE)." failed
        exit $LASTEXITCODE
    }
    git -C $Staging -c core.longpaths=true diff --quiet --no-ext-diff --no-textconv HEAD --
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress 'Git source checkout is incomplete or changed; refusing Manager construction.' failed
        exit 1
    }
    $PayloadParent = $Staging
} else {
    # Derive the codeload tarball URL from the (GitHub) source; a non-GitHub
    # source genuinely needs git.
    $TarUrl = $null
    $m = [regex]::Match($Repo, 'github\.com[/:]+([^/]+)/([^/]+?)(?:\.git)?/?$')
    if ($m.Success) { $TarUrl = "https://codeload.github.com/$($m.Groups[1].Value)/$($m.Groups[2].Value)/tar.gz/$Ref" }
    if (-not $TarUrl) { throw "git is required for a non-GitHub source ($Repo). Install git and re-run." }
    Write-BootstrapProgress 'fetching payload tarball (no git)...'
    New-Item -ItemType Directory -Force -Path $Staging | Out-Null
    $Tar = Join-Path $Staging 'payload.tar.gz'
    Invoke-WebRequest -Uri $TarUrl -OutFile $Tar -UseBasicParsing
    $Extract = Join-Path $Staging 'extract'
    if (Test-Path $Extract) { Remove-Item -Recurse -Force $Extract }
    New-Item -ItemType Directory -Force -Path $Extract | Out-Null
    tar -xzf $Tar -C $Extract   # bsdtar ships with Windows 10 1803+
    if ($LASTEXITCODE -ne 0) {
        Write-BootstrapProgress "Extracting the payload tarball failed (exit $LASTEXITCODE)." failed
        exit $LASTEXITCODE
    }
    $PayloadParent = (Get-ChildItem $Extract -Directory | Select-Object -First 1).FullName
    Remove-Item $Tar -Force
}

Push-Location (Join-Path $PayloadParent 'worktree-manager')
$PreviousSourceAnchor = $env:WORKTREE_MANAGER_SOURCE_ANCHOR
$PreviousBootstrapPython = $env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON
try {
    $env:WORKTREE_MANAGER_SOURCE_ANCHOR = $BootstrapSourceAnchor
    if ($installerArgs -notcontains '--recover-for-repair') {
        $entry = Join-Path $PWD 'scripts\runtime_install.py'
        if (-not (Select-String -LiteralPath $entry -CaseSensitive -Quiet `
            -Pattern '^# copilot-extensions:bootstrap-uv=1$')) {
            throw 'Selected Manager source does not support qualified UV handoff. Select a newer source; installation was not started.'
        }
        $installerArgs += @('--bootstrap-uv', $Uv)
    }
    if ($ToolchainConfig -or $ToolchainOnly) {
        $capabilities = @('toolchain-config', 'toolchain-context')
        if ($ToolchainOnly) { $capabilities += 'toolchain-bootstrap-only' }
        foreach ($capability in $capabilities) {
            if (-not (Select-String -LiteralPath (Join-Path $PWD 'scripts\runtime_install.py') `
                -CaseSensitive -Quiet -Pattern "^# copilot-extensions:${capability}=1$")) {
                throw 'Selected Manager source does not support toolchain configuration; installation was not started.'
            }
        }
    }
    $Python = if ($SystemPythonRequested) { $SystemPython } else { Find-BootstrapPython $Uv }
    $env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON = $Python
    $env:PATH = Add-BootstrapPythonPath $env:PATH ([IO.Path]::GetDirectoryName($Python))
    if ($commandArgs.Count) {
        & $Python -I -S -B -X utf8 (Join-Path $PWD 'scripts\runtime_install.py') --root $Root --payload $PWD @installerArgs -- @commandArgs
    } else {
        & $Python -I -S -B -X utf8 (Join-Path $PWD 'scripts\runtime_install.py') --root $Root --payload $PWD @installerArgs
    }
    $commandExit = $LASTEXITCODE
} finally {
    $env:WORKTREE_MANAGER_SOURCE_ANCHOR = $PreviousSourceAnchor
    $env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON = $PreviousBootstrapPython
    Pop-Location
}
if ($commandExit -ne 0) {
    Write-BootstrapProgress "Manager runtime command failed ($commandExit)." failed
}
if ($commandExit -eq 0 -and ($env:PATH -split ';') -notcontains $LocalBin) {
    Write-BootstrapProgress "Add '$LocalBin' to your PATH (uv and the worktree-manager binstub live there), then restart your shell." warning
}
exit $commandExit
} finally {
    if ($RecoveryStaging) { Remove-Item -LiteralPath $RecoveryStaging -Recurse -Force }
    if ($UpdateStaging -and $commandExit -eq 0 -and
        (Test-Path -LiteralPath $UpdateStaging)) {
        Remove-Item -LiteralPath $UpdateStaging -Recurse -Force
    }
}
