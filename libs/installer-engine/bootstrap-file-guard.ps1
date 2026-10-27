# Private prerequisite building block; not an interpreter admission decision.
function Get-BootstrapCoreFileBindings {
    $assembly = [object].Assembly
    if ($assembly.FullName -cne 'System.Private.CoreLib, Version=10.0.0.0, Culture=neutral, PublicKeyToken=7cec85d7bea7798e') {
        throw 'Stock bootstrap Core file binding assembly identity is unsupported.'
    }
    $flags = [Reflection.BindingFlags]'Static,Public,NonPublic'
    $native = $assembly.GetType('Interop+Kernel32', $true)
    $record = $assembly.GetType('Interop+Kernel32+BY_HANDLE_FILE_INFORMATION', $true)
    $open = $native.GetMethod('CreateFile', $flags, $null, [type[]]@(
        [string], [int], [IO.FileShare], [IO.FileMode], [int]
    ), $null)
    $info = $native.GetMethod('GetFileInformationByHandle', $flags, $null, [type[]]@(
        [Microsoft.Win32.SafeHandles.SafeFileHandle], $record.MakeByRefType()
    ), $null)
    $final = $assembly.GetType('System.IO.FileSystem', $true).GetMethod(
        '<GetFinalLinkTarget>g__GetFinalPathNameByHandle|50_0', $flags, $null, [type[]]@(
            [Microsoft.Win32.SafeHandles.SafeFileHandle], [char[]]
        ), $null)
    if ($null -eq $open -or $open.ReturnType -ne [Microsoft.Win32.SafeHandles.SafeFileHandle] -or
        $null -eq $info -or $info.ReturnType -ne [bool] -or -not $info.GetParameters()[1].IsOut -or
        $null -eq $final -or $final.ReturnType -ne [uint32] -or
        -not $final.IsDefined([Runtime.CompilerServices.CompilerGeneratedAttribute], $false)) {
        throw 'Stock bootstrap Core file binding signature is unsupported.'
    }
    foreach ($method in @($open, $info, $final)) {
        if (-not $method.IsStatic -or -not $method.IsAssembly -or
            ($method.Attributes -band [Reflection.MethodAttributes]::PinvokeImpl) -ne 0 -or
            $null -eq $method.GetMethodBody() -or
            $method.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false).Count -ne 0) {
            throw 'Stock bootstrap Core file binding requires an existing managed wrapper.'
        }
    }
    $imports = @($info.GetCustomAttributesData() | Where-Object {
        $_.AttributeType.FullName -ceq 'System.Runtime.InteropServices.LibraryImportAttribute'
    })
    if ($imports.Count -ne 1 -or $imports[0].ConstructorArguments[0].Value -cne 'kernel32.dll') {
        throw 'Stock bootstrap Core file information import is unsupported.'
    }
    $lastError = @($imports[0].NamedArguments | Where-Object {
        $_.MemberName -ceq 'SetLastError' -and $_.TypedValue.Value -eq $true
    })
    $marshalling = @($info.ReturnParameter.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false))
    $search = @($assembly.GetCustomAttributes([Runtime.InteropServices.DefaultDllImportSearchPathsAttribute], $false))
    if ($lastError.Count -ne 1 -or $marshalling.Count -ne 1 -or
        $marshalling[0].Value -ne [Runtime.InteropServices.UnmanagedType]::Bool -or
        $search.Count -ne 1 -or [int]$search[0].Paths -ne 2050) {
        throw 'Stock bootstrap Core file binding marshalling or library search is unsupported.'
    }
    return [pscustomobject]@{
        Open = $open; Information = $info; RecordType = $record; Core = $true; FinalPath = $final
        Fields = @{ FileAttributes = 'dwFileAttributes'; VolumeSerialNumber = 'dwVolumeSerialNumber'
            FileIndexHigh = 'nFileIndexHigh'; FileIndexLow = 'nFileIndexLow' }
    }
}

function Get-BootstrapStockFileBindings {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        [IntPtr]::Size -ne 8) {
        throw 'Stock bootstrap file guards require qualified 64-bit Windows PowerShell.'
    }
    if ($PSVersionTable.PSEdition -eq 'Core') {
        $bindings = Get-BootstrapCoreFileBindings
        Assert-BootstrapStockFileRecord -Bindings $bindings
        return $bindings
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
    $bindings = [pscustomobject]@{
        Open = $open; Information = $info; RecordType = $recordType; Core = $false; FinalPath = $null
        Fields = @{ FileAttributes = 'FileAttributes'; VolumeSerialNumber = 'VolumeSerialNumber'
            FileIndexHigh = 'FileIndexHigh'; FileIndexLow = 'FileIndexLow' }
    }
    Assert-BootstrapStockFileRecord -Bindings $bindings
    return $bindings
}

function Assert-BootstrapStockFileRecord {
    param([Parameter(Mandatory)]$Bindings)
    $recordType = $Bindings.RecordType
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
        $fieldName = $Bindings.Fields[$name]
        $field = $recordType.GetField($fieldName, [Reflection.BindingFlags]'Instance,Public,NonPublic')
        if ($null -eq $field -or $field.FieldType -ne [uint32] -or
            $offsetOf.Invoke($null, [object[]]@($recordType, $fieldName)).ToInt64() -ne $fields[$name]) {
            throw 'Stock bootstrap file identity field is unsupported.'
        }
    }
}

function Get-BootstrapStockFileIdentity {
    param(
        [Parameter(Mandatory)][Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
        [Parameter(Mandatory)]$Bindings
    )
    $retained = $false
    try {
        $Handle.DangerousAddRef([ref]$retained)
        $nativeHandle = if ($Bindings.Core) { $Handle } else { $Handle.DangerousGetHandle() }
        $arguments = [object[]]@(
            $nativeHandle, [Activator]::CreateInstance($Bindings.RecordType)
        )
        $ok = $Bindings.Information.Invoke($null, $arguments)
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if (-not $ok) {
            throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap file identity query failed.')
        }
        $record = $arguments[1]
        $fieldFlags = [Reflection.BindingFlags]'Instance,Public,NonPublic'
        return [pscustomobject]@{
            Attributes = [uint32]$Bindings.RecordType.GetField($Bindings.Fields.FileAttributes, $fieldFlags).GetValue($record)
            Volume = [uint32]$Bindings.RecordType.GetField($Bindings.Fields.VolumeSerialNumber, $fieldFlags).GetValue($record)
            IndexHigh = [uint32]$Bindings.RecordType.GetField($Bindings.Fields.FileIndexHigh, $fieldFlags).GetValue($record)
            IndexLow = [uint32]$Bindings.RecordType.GetField($Bindings.Fields.FileIndexLow, $fieldFlags).GetValue($record)
        }
    } finally {
        if ($retained) { $Handle.DangerousRelease() }
    }
}

function Get-BootstrapStockFinalPath {
    param(
        [Parameter(Mandatory)][Microsoft.Win32.SafeHandles.SafeFileHandle]$Handle,
        [Parameter(Mandatory)]$Bindings
    )
    if (-not $Bindings.Core -or $null -eq $Bindings.FinalPath) {
        throw 'Stock bootstrap final-path binding requires qualified PowerShell Core.'
    }
    $retained = $false
    try {
        $Handle.DangerousAddRef([ref]$retained)
        $buffer = [char[]]::new(32768)
        $length = $Bindings.FinalPath.Invoke($null, [object[]]@($Handle, $buffer))
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($length -eq 0) {
            throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap final-path query failed.')
        }
        if ($length -ge $buffer.Length -or $buffer[$length] -ne [char]0) {
            throw 'Bootstrap final-path query exceeds its bounded buffer.'
        }
        $path = [string]::new($buffer, 0, [int]$length)
        if ($path.IndexOf([char]0) -ge 0) { throw 'Bootstrap final-path query returned an embedded terminator.' }
        return $path
    } finally {
        if ($retained) { $Handle.DangerousRelease() }
    }
}

function Assert-BootstrapStockOrdinaryPath {
    param([Parameter(Mandatory)][string]$Path, [int]$MaximumComponents = 0)
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or
        $Path.Contains('/') -or $Path.Length -gt 32767 -or
        ($Path.Length -gt 3 -and $Path.EndsWith('\')) -or
        $Path -match '[\x00-\x1f]') {
        throw 'Bootstrap file guard requires an ordinary absolute normalized drive path.'
    }
    if ($MaximumComponents -gt 0 -and
        @($Path.Substring(3).Split('\') | Where-Object { $_ }).Count -gt $MaximumComponents) {
        throw 'Bootstrap ancestor guard exceeds its bounded component count.'
    }
    if (-not [string]::Equals([IO.Path]::GetFullPath($Path), $Path, [StringComparison]::Ordinal)) {
        throw 'Bootstrap file guard requires an ordinary absolute normalized drive path.'
    }
    foreach ($segment in $Path.Substring(3).Split('\')) {
        if ($segment -and ($segment.EndsWith('.') -or $segment.EndsWith(' '))) {
            throw 'Bootstrap file guard refuses a path with an aliased component.'
        }
    }
}

function Open-BootstrapStockFileGuard {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Directory
    )
    $ErrorActionPreference = 'Stop'
    Assert-BootstrapStockOrdinaryPath -Path $Path
    $bindings = Get-BootstrapStockFileBindings
    # Attribute-only opens do not participate in sharing checks; include FILE_LIST_DIRECTORY.
    $access = if ($Directory) { 0x81 } else { -2147483648 }
    # Read sharing only: no write/delete sharing; do not follow a final reparse point.
    $arguments = if ($bindings.Core) {
        [object[]]@($Path, [int]$access, [IO.FileShare]::Read, [IO.FileMode]::Open, [int]0x02200000)
    } else {
        [object[]]@($Path, [int]$access, [int]1, [IntPtr]::Zero, [int]3, [int]0x02200000, [IntPtr]::Zero)
    }
    $handle = $bindings.Open.Invoke($null, $arguments)
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

function Close-BootstrapStockPathGuard {
    param([Parameter(Mandatory)]$Guard)
    $failures = [Collections.Generic.List[Exception]]::new()
    for ($index = $Guard.Entries.Count - 1; $index -ge 0; $index--) {
        try { $Guard.Entries[$index].Handle.Dispose() }
        catch { $failures.Add($_.Exception) }
    }
    if ($failures.Count -gt 0) {
        throw [AggregateException]::new('Bootstrap ancestor guard cleanup failed.', $failures.ToArray())
    }
}

function Open-BootstrapStockPathGuard {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$Directory
    )
    $ErrorActionPreference = 'Stop'
    Assert-BootstrapStockOrdinaryPath -Path $Path -MaximumComponents 128
    $segments = @($Path.Substring(3).Split('\') | Where-Object { $_ })
    if ($segments.Count -eq 0 -and -not $Directory) {
        throw 'Bootstrap ancestor guard requires a file below the drive root.'
    }
    $entries = [Collections.Generic.List[object]]::new($segments.Count + 1)
    $result = $null
    $failure = $null
    try {
        $current = $Path.Substring(0, 3)
        $entries.Add((Open-BootstrapStockFileGuard -Path $current -Directory))
        for ($index = 0; $index -lt $segments.Count; $index++) {
            $current = [IO.Path]::Combine($current, $segments[$index])
            $isDirectory = $Directory -or $index -lt $segments.Count - 1
            $entries.Add((Open-BootstrapStockFileGuard -Path $current -Directory:$isDirectory))
        }
        $result = [pscustomobject]@{
            Path = $Path
            Entries = $entries.ToArray()
            Leaf = $entries[$entries.Count - 1]
        }
        return $result
    } catch {
        $failure = $_.Exception
        throw
    } finally {
        if ($null -eq $result) {
            try {
                Close-BootstrapStockPathGuard -Guard ([pscustomobject]@{ Entries = $entries.ToArray() })
            } catch {
                if ($null -ne $failure) {
                    throw [AggregateException]::new('Bootstrap ancestor admission and cleanup failed.',
                        [Exception[]]@($failure, $_.Exception))
                }
                throw
            }
        }
    }
}
