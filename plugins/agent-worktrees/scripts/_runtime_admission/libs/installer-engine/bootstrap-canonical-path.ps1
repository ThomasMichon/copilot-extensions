# Private physical-path custody; not an interpreter or helper admission decision.
function Get-BootstrapStockCanonicalBindings {
    $files = Get-BootstrapStockFileBindings
    $core = $files.Core
    $assembly = [object].Assembly
    $expected = if ($core) {
        'System.Private.CoreLib, Version=10.0.0.0, Culture=neutral, PublicKeyToken=7cec85d7bea7798e'
    } else {
        'mscorlib, Version=4.0.0.0, Culture=neutral, PublicKeyToken=b77a5c561934e089'
    }
    if ($assembly.FullName -cne $expected) {
        throw 'Stock bootstrap canonical binding assembly identity is unsupported.'
    }
    $flags = [Reflection.BindingFlags]'Static,Public,NonPublic,DeclaredOnly'
    $typeName = if ($core) { 'Interop+Kernel32' } else { 'Microsoft.Win32.Win32Native' }
    $native = $assembly.GetType($typeName, $true)
    $record = $assembly.GetType($typeName + '+WIN32_FIND_DATA', $true)
    $find = $native.GetMethod('FindFirstFile', $flags, $null,
        [type[]]@([string], $record.MakeByRefType()), $null)
    $driveAssembly = if ($core) { [IO.DriveInfo].Assembly } else { $assembly }
    if ($core -and $driveAssembly.FullName -cne
        'System.IO.FileSystem.DriveInfo, Version=10.0.0.0, Culture=neutral, PublicKeyToken=b03f5f7f11d50a3a') {
        throw 'Stock bootstrap drive-type assembly identity is unsupported.'
    }
    $drive = $driveAssembly.GetType($typeName, $true).GetMethod(
        'GetDriveType', $flags, $null, [type[]]@([string]), $null)
    if ($null -eq $find -or $find.ReturnType.FullName -cne 'Microsoft.Win32.SafeHandles.SafeFindHandle' -or
        $find.ReturnType.Assembly -ne $assembly -or
        -not [Runtime.InteropServices.SafeHandle].IsAssignableFrom($find.ReturnType) -or
        $null -eq $drive -or $drive.ReturnType -ne [int]) {
        throw 'Stock bootstrap canonical binding signature is unsupported.'
    }
    foreach ($method in @($find, $drive)) {
        $search = @($method.DeclaringType.Assembly.GetCustomAttributes(
            [Runtime.InteropServices.DefaultDllImportSearchPathsAttribute], $false))
        if (-not $method.IsStatic -or -not $method.IsAssembly -or
            $search.Count -ne 1 -or [int]$search[0].Paths -ne 2050) {
            throw 'Stock bootstrap canonical binding visibility or library search is unsupported.'
        }
        foreach ($parameter in @($method.ReturnParameter) + @($method.GetParameters())) {
            if ($parameter.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false).Count) {
                throw 'Stock bootstrap canonical binding marshalling is unsupported.'
            }
        }
        $imports = @($method.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false))
        if ($core) {
            if ($imports.Count -ne 0 -or $null -eq $method.GetMethodBody() -or
                ($method.Attributes -band [Reflection.MethodAttributes]::PinvokeImpl) -ne 0) {
                throw 'Stock bootstrap canonical binding requires an existing managed wrapper.'
            }
        } else {
            $charSet = if ($method -eq $find) {
                [Runtime.InteropServices.CharSet]::Unicode
            } else {
                [Runtime.InteropServices.CharSet]::Auto
            }
            if ($imports.Count -ne 1 -or $imports[0].Value -cne 'kernel32.dll' -or
                $imports[0].EntryPoint -cne $method.Name -or $imports[0].CharSet -ne $charSet -or
                $imports[0].ExactSpelling -or -not $imports[0].SetLastError -or
                -not $imports[0].PreserveSig -or
                $imports[0].CallingConvention -ne [Runtime.InteropServices.CallingConvention]::Winapi -or
                $null -ne $method.GetMethodBody() -or
                ($method.Attributes -band [Reflection.MethodAttributes]::PinvokeImpl) -eq 0) {
                throw 'Stock bootstrap canonical native declaration is unsupported.'
            }
        }
    }
    if ($core) {
        $imports = @($drive.GetCustomAttributesData() | Where-Object {
            $_.AttributeType.FullName -ceq 'System.Runtime.InteropServices.LibraryImportAttribute'
        })
        if ($imports.Count -ne 1 -or $imports[0].ConstructorArguments[0].Value -cne 'kernel32.dll') {
            throw 'Stock bootstrap drive-type import is unsupported.'
        }
        $named = @{}
        foreach ($argument in $imports[0].NamedArguments) { $named[$argument.MemberName] = $argument.TypedValue.Value }
        if ($named.EntryPoint -cne 'GetDriveTypeW' -or $named.StringMarshalling -ne 2 -or
            $named.SetLastError -ne $true) {
            throw 'Stock bootstrap drive-type import policy is unsupported.'
        }
    }
    Assert-BootstrapStockFindRecord -Record $record -Core:$core
    $copy = [Runtime.InteropServices.Marshal].GetMethod(
        'StructureToPtr', [type[]]@([object], [IntPtr], [bool]))
    return [pscustomobject]@{ Files = $files; Find = $find; Record = $record; DriveType = $drive; CopyRecord = $copy }
}

function Assert-BootstrapStockFindRecord {
    param([Parameter(Mandatory)][type]$Record, [switch]$Core)
    $size = [Runtime.InteropServices.Marshal].GetMethod('SizeOf', [type[]]@([type]))
    $offset = [Runtime.InteropServices.Marshal].GetMethod('OffsetOf', [type[]]@([type], [string]))
    $flags = [Reflection.BindingFlags]'Instance,Public,NonPublic,DeclaredOnly'
    $pack = if ($Core) { 0 } else { 8 }
    if (-not $Record.IsValueType -or -not $Record.IsLayoutSequential -or
        $Record.StructLayoutAttribute.Pack -ne $pack -or
        $Record.StructLayoutAttribute.CharSet -ne [Runtime.InteropServices.CharSet]::Unicode -or
        $size.Invoke($null, [object[]]@($Record)) -ne 592 -or $Record.GetFields($flags).Count -ne 10) {
        throw 'Stock bootstrap find record layout is unsupported.'
    }
    foreach ($buffer in @(@('_cFileName', 44, 260), @('_cAlternateFileName', 564, 14))) {
        $field = $Record.GetField($buffer[0], $flags)
        if ($null -eq $field -or
            $offset.Invoke($null, [object[]]@($Record, $buffer[0])).ToInt64() -ne $buffer[1]) {
            throw 'Stock bootstrap find buffer offset is unsupported.'
        }
        $fixed = @($field.GetCustomAttributes([Runtime.CompilerServices.FixedBufferAttribute], $false))
        $type = $field.FieldType
        $element = $type.GetField('FixedElementField', $flags)
        if ($fixed.Count -ne 1 -or $fixed[0].ElementType -ne [char] -or $fixed[0].Length -ne $buffer[2] -or
            $field.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false).Count -ne 0 -or
            $type.Assembly -ne $Record.Assembly -or $type.DeclaringType -ne $Record -or
            -not $type.IsValueType -or -not $type.IsLayoutSequential -or
            $type.StructLayoutAttribute.CharSet -ne [Runtime.InteropServices.CharSet]::Unicode -or
            $size.Invoke($null, [object[]]@($type)) -ne 2 * $buffer[2] -or
            $type.GetFields($flags).Count -ne 1 -or $null -eq $element -or $element.FieldType -ne [char] -or
            $offset.Invoke($null, [object[]]@($type, 'FixedElementField')).ToInt64() -ne 0) {
            throw 'Stock bootstrap fixed find buffer is unsupported.'
        }
    }
    foreach ($field in $Record.GetFields($flags)) {
        if ($field.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false).Count) {
            throw 'Stock bootstrap find record field marshalling is unsupported.'
        }
        if ($field.FieldType -eq [int] -or $field.FieldType -eq [uint32] -or
            $field.Name -in @('_cFileName', '_cAlternateFileName')) { continue }
        $time = $field.FieldType
        if ($time.Name -cne 'FILE_TIME' -or $time.Assembly -ne $Record.Assembly -or
            $time.DeclaringType -ne $Record.DeclaringType -or
            -not $time.IsValueType -or -not $time.IsLayoutSequential -or
            $size.Invoke($null, [object[]]@($time)) -ne 8 -or $time.GetFields($flags).Count -ne 2) {
            throw 'Stock bootstrap find record value-field closure is unsupported.'
        }
        foreach ($part in $time.GetFields($flags)) {
            if (($part.FieldType -ne [int] -and $part.FieldType -ne [uint32]) -or
                $part.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false).Count) {
                throw 'Stock bootstrap find time record is not a plain value type.'
            }
        }
    }
}

function New-BootstrapStockFindAllocation {
    [Runtime.InteropServices.Marshal]::AllocHGlobal(592)
}

function Close-BootstrapStockFindAllocation {
    param([Parameter(Mandatory)][IntPtr]$Pointer)
    [Runtime.InteropServices.Marshal]::FreeHGlobal($Pointer)
}

function Get-BootstrapStockLongLeafName {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Bindings)
    $ErrorActionPreference = 'Stop'
    $find = $null
    $pointer = [IntPtr]::Zero
    $name = $null
    try {
        $arguments = [object[]]@($Path, [Activator]::CreateInstance($Bindings.Record))
        $find = $Bindings.Find.Invoke($null, $arguments)
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($null -eq $find -or $find.IsInvalid -or $find.IsClosed) {
            throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap long-name query failed.')
        }
        if ($arguments[1].GetType() -ne $Bindings.Record) {
            throw 'Bootstrap long-name query returned an unsupported record type.'
        }
        # Core's name getter returns a non-boxable span. Copy only the qualified existing value type.
        $pointer = New-BootstrapStockFindAllocation
        if ($pointer -eq [IntPtr]::Zero) { throw 'Bootstrap long-name allocation returned a null pointer.' }
        $null = $Bindings.CopyRecord.Invoke($null, [object[]]@($arguments[1], $pointer, [bool]$false))
        $characters = [char[]]::new(260)
        [Runtime.InteropServices.Marshal]::Copy([IntPtr]::Add($pointer, 44), $characters, 0, 260)
        $end = [Array]::IndexOf($characters, [char]0)
        if ($end -le 0) { throw 'Bootstrap long-name query returned an empty or unterminated name.' }
        $name = [string]::new($characters, 0, $end)
        if ($name -match '[\\/\x00-\x1f]') { throw 'Bootstrap long-name query returned a non-leaf name.' }
    } finally {
        try {
            if ($pointer -ne [IntPtr]::Zero) { Close-BootstrapStockFindAllocation -Pointer $pointer }
        } finally {
            # SafeFindHandle owns FindClose, not the ordinary file-handle closer.
            if ($null -ne $find) { $find.Dispose() }
        }
    }
    return $name
}

function Open-BootstrapStockCanonicalPathGuard {
    param([Parameter(Mandatory)][string]$Path, [switch]$Directory)
    $ErrorActionPreference = 'Stop'
    Assert-BootstrapStockOrdinaryPath -Path $Path -MaximumComponents 128
    $segments = @($Path.Substring(3).Split('\') | Where-Object { $_ })
    if ($segments.Count -eq 0 -and -not $Directory) {
        throw 'Bootstrap canonical guard requires a file below the drive root.'
    }
    foreach ($segment in $segments) {
        if ($segment -match '[*?<>|"]' -or
            $segment -match '\A(?i:CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3]) *(?:\.|\z)') {
            throw 'Bootstrap canonical guard refuses a reserved or wildcard component.'
        }
    }
    $bindings = Get-BootstrapStockCanonicalBindings
    $mapping = Get-BootstrapStockCurrentDriveMapping -Drive $Path.Substring(0, 2)
    if ($mapping -cnotmatch '\A\\Device\\HarddiskVolume[1-9][0-9]{0,9}\z') {
        throw 'Bootstrap canonical guard requires a direct physical volume mapping.'
    }
    $physical = '\\?\GLOBALROOT' + $mapping + '\'
    if ($physical.Length + $Path.Length - 3 -gt 32767) {
        throw 'Bootstrap canonical physical path exceeds its bounded length.'
    }
    $entries = [Collections.Generic.List[object]]::new($segments.Count + 1)
    $result = $null
    $failure = $null
    try {
        $entries.Add((New-BootstrapStockFileGuard -Path $physical -Directory -Bindings $bindings.Files))
        if ($bindings.DriveType.Invoke($null, [object[]]@($physical)) -ne 3) {
            throw 'Bootstrap canonical guard requires a fixed physical volume.'
        }
        for ($index = 0; $index -lt $segments.Count; $index++) {
            $physical += $segments[$index]
            $isDirectory = $Directory -or $index -lt $segments.Count - 1
            $entries.Add((New-BootstrapStockFileGuard -Path $physical -Directory:$isDirectory -Bindings $bindings.Files))
            $longName = Get-BootstrapStockLongLeafName -Path $physical -Bindings $bindings
            if (-not [string]::Equals($longName, $segments[$index], [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Bootstrap canonical guard refuses a short-name or noncanonical component.'
            }
            if ($index -lt $segments.Count - 1) { $physical += '\' }
        }
        $result = [pscustomobject]@{
            Path = $physical; AddressedPath = $Path; Mapping = $mapping
            Entries = $entries.ToArray(); Leaf = $entries[$entries.Count - 1]
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
                    throw [AggregateException]::new('Bootstrap canonical admission and cleanup failed.',
                        [Exception[]]@($failure, $_.Exception))
                }
                throw
            }
        }
    }
}
