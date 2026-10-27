# Private current-mapping query; not canonical-path or interpreter admission.
function Get-BootstrapStockDriveBindings {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or [IntPtr]::Size -ne 8) {
        throw 'Stock bootstrap drive queries require qualified 64-bit Windows PowerShell.'
    }
    $assembly = [Management.Automation.PSObject].Assembly
    $core = $PSVersionTable.PSEdition -eq 'Core'
    $version = if ($core) { '7.6.0.500' } else { '3.0.0.0' }
    if ($assembly.FullName -cne "System.Management.Automation, Version=$version, Culture=neutral, PublicKeyToken=31bf3856ad364e35") {
        throw 'Stock bootstrap drive binding assembly identity is unsupported.'
    }
    $typeName = if ($core) { 'Interop+Windows' } else { 'Microsoft.PowerShell.Commands.FileSystemProvider+NativeMethods' }
    $name = if ($core) { '<QueryDosDevice>g____PInvoke|56_0' } else { 'QueryDosDevice' }
    $parameters = if ($core) {
        [type[]]@([char].MakePointerType(), [char].MakePointerType(), [uint32])
    } else {
        [type[]]@([string], [Text.StringBuilder], [int])
    }
    $method = $assembly.GetType($typeName, $true).GetMethod(
        $name, [Reflection.BindingFlags]'Static,Public,NonPublic,DeclaredOnly', $null, $parameters, $null)
    if ($null -eq $method -or $method.ReturnType -ne [int] -or -not $method.IsStatic -or
        -not $method.IsAssembly -or
        ($method.Attributes -band [Reflection.MethodAttributes]::PinvokeImpl) -eq 0 -or
        $null -ne $method.GetMethodBody()) {
        throw 'Stock bootstrap drive binding signature is unsupported.'
    }
    $imports = @($method.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false))
    $library = if ($core) { 'api-ms-win-core-file-l1-1-0.dll' } else { 'kernel32.dll' }
    $entry = if ($core) { 'QueryDosDeviceW' } else { 'QueryDosDevice' }
    $charSet = if ($core) { [Runtime.InteropServices.CharSet]::None } else { [Runtime.InteropServices.CharSet]::Unicode }
    if ($imports.Count -ne 1 -or $imports[0].Value -cne $library -or $imports[0].EntryPoint -cne $entry -or
        $imports[0].ExactSpelling -ne $core -or $imports[0].SetLastError -ne (-not $core) -or
        $imports[0].CharSet -ne $charSet -or -not $imports[0].PreserveSig -or
        $imports[0].CallingConvention -ne [Runtime.InteropServices.CallingConvention]::Winapi) {
        throw 'Stock bootstrap drive binding native declaration is unsupported.'
    }
    foreach ($parameter in @($method.ReturnParameter) + @($method.GetParameters())) {
        if ($parameter.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false).Count -ne 0) {
            throw 'Stock bootstrap drive binding marshalling is unsupported.'
        }
    }
    return [pscustomobject]@{ Query = $method; Core = $core }
}

function New-BootstrapStockDriveAllocation {
    param([string]$Name, [switch]$OutputBuffer)
    if ($OutputBuffer) { return [Runtime.InteropServices.Marshal]::AllocHGlobal(65536) }
    return [Runtime.InteropServices.Marshal]::StringToHGlobalUni($Name)
}

function Close-BootstrapStockDriveAllocation {
    param([Parameter(Mandatory)][IntPtr]$Pointer)
    [Runtime.InteropServices.Marshal]::FreeHGlobal($Pointer)
}

function Get-BootstrapStockCurrentDriveMapping {
    param([Parameter(Mandatory)][string]$Drive)
    $ErrorActionPreference = 'Stop'
    if ($Drive -cnotmatch '\A[A-Za-z]:\z') {
        throw 'Bootstrap drive query requires exactly one drive letter and colon.'
    }
    $bindings = Get-BootstrapStockDriveBindings
    $capacity = 32768
    $output = [IntPtr]::Zero
    $name = [IntPtr]::Zero
    $mapping = $null
    try {
        if ($bindings.Core) {
            $buffer = [char[]]::new($capacity)
            for ($index = 0; $index -lt $capacity; $index++) { $buffer[$index] = [char]0xffff }
            $output = New-BootstrapStockDriveAllocation -OutputBuffer
            [Runtime.InteropServices.Marshal]::Copy($buffer, 0, $output, $capacity)
            $name = New-BootstrapStockDriveAllocation -Name $Drive
            # Existing reflection accepts IntPtr for pointer parameters; no new interop type.
            $arguments = [object[]]@($name, $output, [uint32]$capacity)
        } else {
            $buffer = [Text.StringBuilder]::new($capacity, $capacity)
            $arguments = [object[]]@($Drive, $buffer, [int]$capacity)
        }
        $length = $bindings.Query.Invoke($null, $arguments)
        $errorCode = if (-not $bindings.Core) { [Runtime.InteropServices.Marshal]::GetLastWin32Error() } else { $null }
        if ($length -eq 0) {
            if ($bindings.Core) {
                throw 'Bootstrap drive query failed; the stock raw thunk does not capture native last error.'
            }
            throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap drive query failed.')
        }
        if ($length -lt 3 -or $length -gt $capacity) {
            throw 'Bootstrap drive query returned an invalid bounded length.'
        }
        if ($bindings.Core) {
            [Runtime.InteropServices.Marshal]::Copy($output, $buffer, 0, $capacity)
            if ($buffer[$length - 1] -ne [char]0 -or $buffer[$length - 2] -ne [char]0) {
                throw 'Bootstrap drive query returned an unterminated device mapping.'
            }
            $end = [Array]::IndexOf($buffer, [char]0, 0, [int]$length)
            if ($end -le 0 -or $end -gt $length - 2) {
                throw 'Bootstrap drive query returned an empty current mapping.'
            }
            $mapping = [string]::new($buffer, 0, $end)
        } else {
            # StringBuilder exposes the first string, not the native MULTI_SZ tail.
            $mapping = $buffer.ToString()
            if (-not $mapping -or $mapping.IndexOf([char]0) -ge 0 -or $mapping.Length -gt $length - 2) {
                throw 'Bootstrap drive query returned an invalid current mapping.'
            }
        }
    } finally {
        try {
            if ($name -ne [IntPtr]::Zero) { Close-BootstrapStockDriveAllocation -Pointer $name }
        } finally {
            if ($output -ne [IntPtr]::Zero) { Close-BootstrapStockDriveAllocation -Pointer $output }
        }
    }
    return $mapping
}
