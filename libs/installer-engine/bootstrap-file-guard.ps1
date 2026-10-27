# Private prerequisite building block; not an interpreter admission decision.
function Get-BootstrapStockFileBindings {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        $PSVersionTable.PSEdition -eq 'Core' -or
        [IntPtr]::Size -ne 8) {
        throw 'Stock bootstrap file guards require qualified 64-bit Windows PowerShell.'
    }
    $framework = [Diagnostics.Process].Assembly
    $automation = [Management.Automation.PSObject].Assembly
    if ($framework.FullName -cne 'System, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089' -or
        $automation.FullName -cne 'System.Management.Automation, Version=3.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35') {
        throw 'Stock bootstrap file binding assembly identity is unsupported.'
    }
    $flags = [Reflection.BindingFlags]'Static,Public,NonPublic'
    $openType = $framework.GetType('Microsoft.Win32.UnsafeNativeMethods', $true)
    $infoType = $automation.GetType('Microsoft.PowerShell.Commands.InternalSymbolicLinkLinkCodeMethods', $true)
    $recordType = $automation.GetType('Microsoft.PowerShell.Commands.InternalSymbolicLinkLinkCodeMethods+BY_HANDLE_FILE_INFORMATION', $true)
    $open = $openType.GetMethod('CreateFile', $flags, $null, [type[]]@(
        [string], [int], [int], [IntPtr], [int], [int], [IntPtr]
    ), $null)
    $info = $infoType.GetMethod('GetFileInformationByHandle', $flags, $null, [type[]]@(
        [IntPtr], $recordType.MakeByRefType()
    ), $null)
    if ($null -eq $open -or $open.ReturnType -ne [Microsoft.Win32.SafeHandles.SafeFileHandle] -or
        $null -eq $info -or $info.ReturnType -ne [bool] -or -not $info.GetParameters()[1].IsOut) {
        throw 'Stock bootstrap file binding signature is unsupported.'
    }
    foreach ($method in @($open, $info)) {
        $imports = @($method.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false))
        if ($imports.Count -ne 1 -or $imports[0].Value -cne 'kernel32.dll' -or
            $imports[0].EntryPoint -cne $method.Name -or -not $imports[0].SetLastError -or
            -not $method.IsStatic -or
            ($method.Attributes -band [Reflection.MethodAttributes]::PinvokeImpl) -eq 0) {
            throw 'Stock bootstrap file binding native declaration is unsupported.'
        }
    }
    if ($open.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false)[0].CharSet -ne
            [Runtime.InteropServices.CharSet]::Auto -or
        $info.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false)[0].CharSet -ne
            [Runtime.InteropServices.CharSet]::Unicode) {
        throw 'Stock bootstrap file binding character encoding is unsupported.'
    }
    # Select Type overloads explicitly: PowerShell otherwise marshals RuntimeType itself.
    $sizeOf = [Runtime.InteropServices.Marshal].GetMethod('SizeOf', [type[]]@([type]))
    $offsetOf = [Runtime.InteropServices.Marshal].GetMethod('OffsetOf', [type[]]@([type], [string]))
    if (-not $recordType.IsValueType -or -not $recordType.IsLayoutSequential -or
        $sizeOf.Invoke($null, [object[]]@($recordType)) -ne 52) {
        throw 'Stock bootstrap file identity layout is unsupported.'
    }
    $fields = @{
        FileAttributes = 0
        VolumeSerialNumber = 28
        FileIndexHigh = 44
        FileIndexLow = 48
    }
    foreach ($name in $fields.Keys) {
        $field = $recordType.GetField($name, [Reflection.BindingFlags]'Instance,Public,NonPublic')
        if ($null -eq $field -or $field.FieldType -ne [uint32] -or
            $offsetOf.Invoke($null, [object[]]@($recordType, $name)).ToInt64() -ne $fields[$name]) {
            throw 'Stock bootstrap file identity field is unsupported.'
        }
    }
    return [pscustomobject]@{ Open = $open; Information = $info; RecordType = $recordType }
}

function Get-BootstrapStockFileIdentity {
    param(
        [Parameter(Mandatory)][Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
        [Parameter(Mandatory)]$Bindings
    )
    $retained = $false
    try {
        $Handle.DangerousAddRef([ref]$retained)
        $arguments = [object[]]@(
            $Handle.DangerousGetHandle(), [Activator]::CreateInstance($Bindings.RecordType)
        )
        $ok = $Bindings.Information.Invoke($null, $arguments)
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if (-not $ok) {
            throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap file identity query failed.')
        }
        $record = $arguments[1]
        $fieldFlags = [Reflection.BindingFlags]'Instance,Public,NonPublic'
        return [pscustomobject]@{
            Attributes = [uint32]$Bindings.RecordType.GetField('FileAttributes', $fieldFlags).GetValue($record)
            Volume = [uint32]$Bindings.RecordType.GetField('VolumeSerialNumber', $fieldFlags).GetValue($record)
            IndexHigh = [uint32]$Bindings.RecordType.GetField('FileIndexHigh', $fieldFlags).GetValue($record)
            IndexLow = [uint32]$Bindings.RecordType.GetField('FileIndexLow', $fieldFlags).GetValue($record)
        }
    } finally {
        if ($retained) { $Handle.DangerousRelease() }
    }
}

function Open-BootstrapStockFileGuard {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Directory
    )
    $ErrorActionPreference = 'Stop'
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or
        $Path.Contains('/') -or $Path.Length -gt 32767 -or
        $Path -match '[\x00-\x1f]' -or
        -not [string]::Equals([IO.Path]::GetFullPath($Path), $Path, [StringComparison]::Ordinal)) {
        throw 'Bootstrap file guard requires an ordinary absolute normalized drive path.'
    }
    foreach ($segment in $Path.Substring(3).Split('\')) {
        if ($segment -and ($segment.EndsWith('.') -or $segment.EndsWith(' '))) {
            throw 'Bootstrap file guard refuses a path with an aliased component.'
        }
    }
    $bindings = Get-BootstrapStockFileBindings
    # Attribute-only opens do not participate in sharing checks; include FILE_LIST_DIRECTORY.
    $access = if ($Directory) { 0x81 } else { -2147483648 }
    # Read sharing only: no write/delete sharing; do not follow a final reparse point.
    $handle = $bindings.Open.Invoke($null, [object[]]@(
        $Path, [int]$access, [int]1, [IntPtr]::Zero, [int]3, [int]0x02200000, [IntPtr]::Zero
    ))
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    try {
        if ($null -eq $handle -or $handle.IsInvalid) {
            throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap file guard open failed.')
        }
        $identity = Get-BootstrapStockFileIdentity -Handle $handle -Bindings $bindings
        if (($identity.Attributes -band 0x400) -ne 0) {
            throw 'Bootstrap file guard refuses a final reparse point.'
        }
        if ((($identity.Attributes -band 0x10) -ne 0) -ne [bool]$Directory) {
            throw 'Bootstrap file guard object kind does not match the requested kind.'
        }
        $result = [pscustomobject]@{
            Path = $Path
            Handle = $handle
            Identity = $identity
        }
        $handle = $null
        return $result
    } finally {
        if ($null -ne $handle) { $handle.Dispose() }
    }
}
