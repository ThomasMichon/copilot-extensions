function Test-WslGuest {
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) { return $false }
    foreach ($marker in @('/.dockerenv', '/run/.containerenv')) {
        if ([IO.File]::Exists($marker)) { return $false }
    }
    if ([IO.File]::Exists('/proc/1/cgroup')) {
        $cgroup = [IO.File]::ReadAllText('/proc/1/cgroup')
        if ($cgroup -match '/docker|/kubepods|containerd|/lxc|libpod') { return $false }
    }
    if (-not [IO.File]::Exists('/proc/sys/kernel/osrelease')) { return $false }
    return [IO.File]::ReadAllText('/proc/sys/kernel/osrelease').ToLowerInvariant().Contains('microsoft')
}

function Get-GuestMachineId {
    $identity = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes('/etc/machine-id')).Trim()
    if ($identity -cnotmatch '^[0-9a-fA-F]{32}$' -or $identity -match '^0{32}$') {
        Fail 'guest-identity-unavailable: the operating-system machine identity is invalid.'
    }
    return $identity.ToLowerInvariant()
}

function Assert-GuestDistributionName($Value) {
    if ($Value -isnot [string] -or [string]::IsNullOrEmpty($Value) -or $Value -match '[\x00-\x1f\x7f]') {
        Fail 'guest-identity-invalid: distribution must be a non-empty single-line string.'
    }
    return $Value
}

function Get-GuestDistributionKey([string]$Value) {
    $result = $Value
    foreach ($character in 65..90) {
        $result = $result.Replace([char]$character, [char]($character + 32))
    }
    return $result
}

function Get-GuestEnvironmentPath([string]$Profile) {
    return Join-Path (Join-Path $Profile '.copilot-extensions') 'guest-environment.json'
}

function Read-GuestEnvironment([string]$Profile) {
    $path = Get-GuestEnvironmentPath $Profile
    try { $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop }
    catch [Management.Automation.ItemNotFoundException] { return $null }
    $parent = Get-Item -LiteralPath (Split-Path -Parent $path) -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Fail 'guest-identity-invalid: guest registration may not be linked.'
    }
    if ($item.PSIsContainer -or $item.Length -gt 65536) {
        Fail 'guest-identity-invalid: guest registration must be a bounded regular file.'
    }
    $metadata = @(& stat '-c' '%u %a' '--' $path)
    if ($LASTEXITCODE -ne 0 -or $metadata.Count -ne 1 -or
        $metadata[0] -notmatch '^([0-9]+) ([0-7]+)$') {
        Fail 'guest-identity-invalid: cannot inspect guest registration ownership.'
    }
    $owner = [uint32]$Matches[1]
    $mode = [Convert]::ToInt32($Matches[2], 8)
    $currentUid = @(& id '-u')
    if ($LASTEXITCODE -ne 0 -or $currentUid.Count -ne 1 -or $currentUid[0] -notmatch '^[0-9]+$') {
        Fail 'guest-identity-unavailable: cannot determine the current account identity.'
    }
    if ($owner -ne [uint32]$currentUid[0] -or ($mode -band 0x12)) {
        Fail 'guest-identity-invalid: guest registration must be owned and writable only by this user.'
    }
    $registration = Read-Json $path
    Assert-JsonObject $registration 'guest registration'
    $keys = @($registration.PSObject.Properties.Name)
    if ($keys.Count -ne 5 -or @($keys | Where-Object {
        $_ -cnotin @('schema', 'version', 'distribution', 'machineId', 'homeRealPath')
    }).Count -ne 0) {
        Fail 'guest-identity-invalid: unsupported or malformed guest registration.'
    }
    $version = Get-PropertyValue $registration 'version'
    if ((Get-StringProperty $registration 'schema') -cne 'copilot-extensions.guest-environment' -or
        ($version -isnot [int] -and $version -isnot [long]) -or $version -ne 1) {
        Fail 'guest-identity-invalid: unsupported or malformed guest registration.'
    }
    [void](Assert-GuestDistributionName (Get-PropertyValue $registration 'distribution'))
    if ((Get-StringProperty $registration 'homeRealPath') -cne $Profile -or
        (Get-StringProperty $registration 'machineId') -cne (Get-GuestMachineId)) {
        Fail 'guest-identity-foreign: registration does not identify this operating-system guest and profile.'
    }
    return $registration
}

function Resolve-WslDistribution([string]$Profile) {
    if (-not (Test-WslGuest)) { return $null }
    $observed = $env:WSL_DISTRO_NAME
    if ($observed) { [void](Assert-GuestDistributionName $observed) }
    $registration = Read-GuestEnvironment $Profile
    if ($null -ne $registration) {
        $distribution = Get-StringProperty $registration 'distribution'
        if ($observed -and (Get-GuestDistributionKey $observed) -cne (Get-GuestDistributionKey $distribution)) {
            Fail 'guest-identity-conflict: observed distribution differs from the guest registration.'
        }
        return $distribution
    }
    if ($observed) { return $observed }
    Fail 'guest-identity-unregistered: this WSL entry has no distribution identity. Run guest-register --distribution <name> through that named direct WSL entry; no runtime ownership metadata has been changed.'
}

function Register-GuestEnvironment([string]$DistributionName) {
    [void](Assert-GuestDistributionName $DistributionName)
    if (-not (Test-WslGuest)) {
        Fail 'guest-identity-conflict: guest-register requires a real WSL guest, not a host or container.'
    }
    if (-not $env:WSL_DISTRO_NAME -or
        (Get-GuestDistributionKey $DistributionName) -cne (Get-GuestDistributionKey $env:WSL_DISTRO_NAME)) {
        Fail 'guest-identity-unverified: run guest-register through the matching named direct WSL entry.'
    }
    $current = Get-CurrentEnvironment
    $path = Get-GuestEnvironmentPath $current.homeRealPath
    if (Test-Path -LiteralPath (Split-Path -Parent $path)) {
        $parent = Get-Item -LiteralPath (Split-Path -Parent $path) -Force
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Fail 'guest-identity-invalid: guest registration directory may not be linked.'
        }
    }
    Acquire-Lock (Join-Path (Split-Path -Parent $path) '.guest-environment.lock') 'guest-environment' 'os-profile'
    try {
        $current.wslDistro = Resolve-WslDistribution $current.homeRealPath
        $registration = Read-GuestEnvironment $current.homeRealPath
        $created = $null -eq $registration
        if ($created) {
            $registration = [ordered]@{
                schema = 'copilot-extensions.guest-environment'
                version = 1
                distribution = $current.wslDistro
                machineId = Get-GuestMachineId
                homeRealPath = $current.homeRealPath
            }
            Write-AtomicJson $path $registration -Private
        }
    } finally { Release-Lock }
    return [pscustomobject][ordered]@{
        action = 'guest-register'; status = 'ready'; created = $created
        registration = $path; environment = $current
    }
}

function Get-GuestEnvironmentStatus([string]$ExpectedDistributionName) {
    $current = Get-CurrentEnvironment
    $default = $null
    if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        $root = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'
        if (Test-Path -LiteralPath $root) {
            $settings = Get-ItemProperty -LiteralPath $root
            if ($settings.PSObject.Properties['DefaultDistribution']) {
                if ($settings.DefaultDistribution -notmatch '^\{[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}$') {
                    Fail 'guest-default-invalid: the host default WSL registration is malformed.'
                }
                $defaultEntry = Get-ItemProperty -LiteralPath (Join-Path $root $settings.DefaultDistribution)
                $default = Assert-GuestDistributionName $defaultEntry.DistributionName
            }
        }
    }
    $matchesDefault = $null
    $status = 'ready'
    if ($current.wslDistro -and $ExpectedDistributionName -and
        (Get-GuestDistributionKey $current.wslDistro) -cne (Get-GuestDistributionKey $ExpectedDistributionName)) {
        Fail 'guest-identity-conflict: requested distribution differs from the current guest.'
    }
    if ($ExpectedDistributionName) {
        if ($null -eq $default -and -not $current.wslDistro) { $status = 'unavailable' }
        else {
            if ($null -ne $default) {
                $matchesDefault = (Get-GuestDistributionKey $default) -ceq (Get-GuestDistributionKey $ExpectedDistributionName)
                if (-not $matchesDefault) { $status = 'mismatch' }
            }
        }
    }
    $registration = Get-GuestEnvironmentPath $current.homeRealPath
    return [pscustomobject][ordered]@{
        action = 'guest-status'; status = $status; environment = $current
        registrationState = $(if ($current.wslDistro) {
            if (Test-Path -LiteralPath $registration) { 'registered' } else { 'unregistered' }
        } else { 'not-applicable' })
        registration = $(if ($current.wslDistro -and (Test-Path -LiteralPath $registration)) { $registration } else { $null })
        hostDefault = [ordered]@{
            status = $(if ($null -ne $default) { 'observed' } else { 'unavailable' })
            distribution = $default
            expectedDistribution = $(if ($ExpectedDistributionName) { $ExpectedDistributionName } else { $null })
            matches = $matchesDefault
        }
    }
}
