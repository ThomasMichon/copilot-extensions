# Windows native binding of python_cache_permissions; import has no effects.
function Test-CEPrivatePath {
    param([string]$Path, [switch]$Ancestor, [switch]$File, [switch]$TokenOwner)
    $attributes = [IO.File]::GetAttributes($Path)
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ([bool]($attributes -band [IO.FileAttributes]::Directory) -eq [bool]$File)) {
        throw 'Unsafe private path kind.'
    }
    $sections = [Security.AccessControl.AccessControlSections]'Access,Owner'
    $info = if ($File) { [IO.FileInfo]::new($Path) } else { [IO.DirectoryInfo]::new($Path) }
    $acl = if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::GetAccessControl($info, $sections)
    } else { $info.GetAccessControl($sections) }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $user = $identity.User
    $trusted = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')
    if ($Ancestor) { $trusted += 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464' }
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $owners = @($user.Value)
    if ($TokenOwner -and $identity.Owner.Value -in $trusted) { $owners += $identity.Owner.Value }
    if (($Ancestor -and $owner -notin $trusted) -or (-not $Ancestor -and $owner -notin $owners)) {
        throw 'Foreign private path owner.'
    }
    $trusted += 'S-1-3-4'
    $mask = if ($Ancestor) { 0x500D0140L } else { 0xF00F01FFL }
    # GetAccessRules can omit unsupported ACEs, so admit only known SDDL kinds first.
    $sddl = $acl.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
    if (-not $sddl.StartsWith('D:') -or $sddl.Contains('NO_ACCESS_CONTROL') -or
        $sddl -match '\((?!A;|D;|AU;|ML;)') { throw 'Unsupported private path access rule.' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($Ancestor -and ($rule.PropagationFlags -band
            [Security.AccessControl.PropagationFlags]::InheritOnly)) { continue }
        if ($rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            (([long]$rule.FileSystemRights -band $mask) -ne 0) -and
            $rule.IdentityReference.Value -notin $trusted) { throw 'Non-private access rule.' }
    }
}

function New-CEPrivateSecurity {
    param([switch]$Directory)
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $security = if ($Directory) { [Security.AccessControl.DirectorySecurity]::new() }
        else { [Security.AccessControl.FileSecurity]::new() }
    $security.SetOwner($user)
    $security.SetAccessRuleProtection($true, $false)
    $inherit = if ($Directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' }
        else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($sid in @($user.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid),
            [Security.AccessControl.FileSystemRights]::FullControl, $inherit,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
    }
    return $security
}

function Initialize-CEPrivateDirectory {
    param([string]$Path)
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Length -gt 8192 -or
        $Path -match '(^|[\\/])\.\.?([\\/]|$)') { throw 'Private path must be absolute and ordinary.' }
    $pending = [Collections.Generic.List[string]]::new()
    $current = [IO.Path]::GetFullPath($Path)
    while (-not [IO.Directory]::Exists($current)) {
        if ([IO.File]::Exists($current) -or $pending.Count -ge 64) { throw 'Private parent unavailable.' }
        $pending.Add($current)
        $current = [IO.Path]::GetDirectoryName($current)
        if (-not $current) { throw 'Private parent unavailable.' }
    }
    $ancestor = $current
    $count = $pending.Count
    while ($ancestor) {
        $count++
        if ($count -gt 64) { throw 'Private path exceeds its depth bound.' }
        Test-CEPrivatePath $ancestor -Ancestor
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    for ($index = $pending.Count - 1; $index -ge 0; $index--) {
        $security = New-CEPrivateSecurity -Directory
        try {
            if ($PSVersionTable.PSEdition -eq 'Core') {
                [IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new($pending[$index]), $security)
            } else { [void][IO.Directory]::CreateDirectory($pending[$index], $security) }
        } catch { if (-not [IO.Directory]::Exists($pending[$index])) { throw } }
        Test-CEPrivatePath $pending[$index]
    }
    Test-CEPrivatePath $Path
}

function Open-CEPrivateFile {
    param([string]$Path, [switch]$TokenOwner)
    if ([IO.File]::Exists($Path)) {
        Test-CEPrivatePath $Path -File -TokenOwner:$TokenOwner
        return [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
            [IO.FileShare]'ReadWrite,Delete')
    }
    $security = New-CEPrivateSecurity
    try {
        if ($PSVersionTable.PSEdition -eq 'Core') {
            return [IO.FileSystemAclExtensions]::Create([IO.FileInfo]::new($Path),
                [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::FullControl,
                [IO.FileShare]'ReadWrite,Delete', 4096, [IO.FileOptions]::None, $security)
        }
        return [IO.FileStream]::new($Path, [IO.FileMode]::CreateNew,
            [Security.AccessControl.FileSystemRights]::FullControl, [IO.FileShare]'ReadWrite,Delete',
            4096, [IO.FileOptions]::None, $security)
    } catch {
        if (-not [IO.File]::Exists($Path)) { throw }
        Test-CEPrivatePath $Path -File -TokenOwner:$TokenOwner
        return [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
            [IO.FileShare]'ReadWrite,Delete')
    }
}
