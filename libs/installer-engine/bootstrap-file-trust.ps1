# Private prerequisite building block; not complete interpreter/source admission.
# The caller loads bootstrap-file-guard.ps1 and retains its guard through this call.
function Get-BootstrapStockTrustBindings {
    $null = Get-BootstrapStockFileBindings
    $assembly = [Management.Automation.PSObject].Assembly
    $native = $assembly.GetType('System.Management.Automation.Security.NativeMethods', $true)
    $flags = [Reflection.BindingFlags]'Static,Public,NonPublic'
    $definitions = @(
        @{ Key = 'Verify'; Name = 'WinVerifyTrust'; Return = [uint32]; Library = 'wintrust.dll';
            Parameters = [type[]]@([IntPtr], [IntPtr], [IntPtr]) },
        @{ Key = 'Provider'; Name = 'WTHelperProvDataFromStateData'; Return = [IntPtr]; Library = 'wintrust.dll';
            Parameters = [type[]]@([IntPtr]) },
        @{ Key = 'Signer'; Name = 'WTHelperGetProvSignerFromChain'; Return = [IntPtr]; Library = 'wintrust.dll';
            Parameters = [type[]]@([IntPtr], [uint32], [uint32], [uint32]) },
        @{ Key = 'Load'; Name = 'LoadLibraryExW'; Return = [IntPtr]; Library = 'kernel32.dll';
            Parameters = [type[]]@([string], [IntPtr], [uint32]) },
        @{ Key = 'Free'; Name = 'FreeLibrary'; Return = [bool]; Library = 'kernel32.dll';
            Parameters = [type[]]@([IntPtr]) }
    )
    $bindings = @{}
    foreach ($definition in $definitions) {
        $method = $native.GetMethod($definition.Name, $flags, $null, $definition.Parameters, $null)
        if ($null -eq $method -or $method.ReturnType -ne $definition.Return -or
            -not $method.IsStatic -or
            ($method.Attributes -band [Reflection.MethodAttributes]::PinvokeImpl) -eq 0) {
            throw 'Stock bootstrap trust binding signature is unsupported.'
        }
        $imports = @($method.GetCustomAttributes([Runtime.InteropServices.DllImportAttribute], $false))
        if ($imports.Count -ne 1 -or $imports[0].Value -cne $definition.Library -or
            $imports[0].EntryPoint -cne $definition.Name -or -not $imports[0].SetLastError -or
            -not $imports[0].PreserveSig -or
            $imports[0].CharSet -ne [Runtime.InteropServices.CharSet]::Unicode -or
            $imports[0].CallingConvention -ne [Runtime.InteropServices.CallingConvention]::Winapi) {
            throw 'Stock bootstrap trust binding native declaration is unsupported.'
        }
        foreach ($parameter in @($method.ReturnParameter) + @($method.GetParameters())) {
            if ($parameter.GetCustomAttributes([Runtime.InteropServices.MarshalAsAttribute], $false).Count -ne 0) {
                throw 'Stock bootstrap trust binding marshalling is unsupported.'
            }
        }
        $bindings[$definition.Key] = $method
    }
    $sizeOf = [Runtime.InteropServices.Marshal].GetMethod('SizeOf', [type[]]@([type]))
    $offsetOf = [Runtime.InteropServices.Marshal].GetMethod('OffsetOf', [type[]]@([type], [string]))
    $layouts = @(
        @{ Name = 'WINTRUST_FILE_INFO'; Size = 32; Fields = @(
            @('cbStruct', [uint32], 0), @('pcwszFilePath', [string], 8),
            @('hFileNotUsed', [IntPtr], 16), @('pgKnownSubjectNotUsed', [IntPtr], 24)) },
        @{ Name = 'WinTrust_Choice'; Size = 8; Fields = @(
            @('pFile', [IntPtr], 0), @('pCatalog', [IntPtr], 0), @('pBlob', [IntPtr], 0),
            @('pSgnr', [IntPtr], 0), @('pCert', [IntPtr], 0)) },
        @{ Name = 'WINTRUST_DATA'; Size = 80; Fields = @(
            @('cbStruct', [uint32], 0), @('pPolicyCallbackData', [IntPtr], 8),
            @('pSIPClientData', [IntPtr], 16), @('dwUIChoice', [uint32], 24),
            @('fdwRevocationChecks', [uint32], 28), @('dwUnionChoice', [uint32], 32),
            @('Choice', $assembly.GetType($native.FullName + '+WinTrust_Choice', $true), 40),
            @('dwStateAction', [uint32], 48), @('hWVTStateData', [IntPtr], 56),
            @('pwszURLReference', [string], 64), @('dwProvFlags', [uint32], 72),
            @('dwUIContext', [uint32], 76)) },
        @{ Name = 'CRYPT_PROVIDER_SGNR'; Size = 64; Fields = @(
            @('cbStruct', [uint32], 0), @('csCertChain', [uint32], 12),
            @('pasCertChain', [IntPtr], 16), @('dwError', [uint32], 40)) },
        @{ Name = 'CRYPT_PROVIDER_CERT'; Size = 88; Fields = @(
            @('cbStruct', [uint32], 0), @('pCert', [IntPtr], 8)) }
    )
    foreach ($layout in $layouts) {
        $record = $assembly.GetType($native.FullName + '+' + $layout.Name, $true)
        $explicit = $layout.Name -ceq 'WinTrust_Choice'
        if (-not $record.IsValueType -or $record.IsExplicitLayout -ne $explicit -or
            $record.IsLayoutSequential -eq $explicit -or $record.StructLayoutAttribute.Pack -ne 8 -or
            $sizeOf.Invoke($null, [object[]]@($record)) -ne $layout.Size) {
            throw 'Stock bootstrap trust record layout is unsupported.'
        }
        foreach ($definition in $layout.Fields) {
            $field = $record.GetField($definition[0], [Reflection.BindingFlags]'Instance,Public,NonPublic')
            if ($null -eq $field -or $field.FieldType -ne $definition[1] -or
                $offsetOf.Invoke($null, [object[]]@($record, $definition[0])).ToInt64() -ne $definition[2]) {
                throw 'Stock bootstrap trust record field is unsupported.'
            }
        }
    }
    return [pscustomobject]$bindings
}

function Assert-BootstrapStockTrustModule {
    param([string]$Path, [IntPtr]$Expected = [IntPtr]::Zero)
    $process = [Diagnostics.Process]::GetCurrentProcess()
    try {
        $modules = @($process.Modules)
        if ($modules.Count -gt 4096) { throw 'Bootstrap trust module inventory exceeds its bounded limit.' }
        $matches = @($modules | Where-Object { $_.ModuleName -ieq 'wintrust.dll' })
        if ($matches.Count -gt 1) { throw 'Bootstrap trust module identity is ambiguous.' }
        foreach ($module in $matches) {
            if (-not [string]::Equals($module.FileName, $Path, [StringComparison]::OrdinalIgnoreCase) -or
                ($Expected -ne [IntPtr]::Zero -and $module.BaseAddress -ne $Expected)) {
                throw 'Bootstrap trust refuses a redirected native module.'
            }
        }
        if ($Expected -ne [IntPtr]::Zero -and $matches.Count -ne 1) {
            throw 'Bootstrap trust loaded module identity is unavailable.'
        }
    } finally { $process.Dispose() }
}

function Open-BootstrapStockTrustLibrary {
    param([Parameter(Mandatory)]$Bindings)
    $path = [IO.Path]::Combine(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::System), 'wintrust.dll'
    )
    Assert-BootstrapStockTrustModule -Path $path
    # Pin our own reference before any stock name-based trust import can bind.
    $module = $Bindings.Load.Invoke($null, [object[]]@($path, [IntPtr]::Zero, [uint32]0x800))
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($module -eq [IntPtr]::Zero) {
        throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap trust library load failed.')
    }
    try {
        Assert-BootstrapStockTrustModule -Path $path -Expected $module
        $result = $module
        $module = [IntPtr]::Zero
        return $result
    } finally {
        if ($module -ne [IntPtr]::Zero) { Close-BootstrapStockTrustLibrary -Bindings $Bindings -Module $module }
    }
}

function Close-BootstrapStockTrustLibrary {
    param([Parameter(Mandatory)]$Bindings, [Parameter(Mandatory)][IntPtr]$Module)
    $closed = $Bindings.Free.Invoke($null, [object[]]@($Module))
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if (-not $closed) {
        throw [ComponentModel.Win32Exception]::new($errorCode, 'Bootstrap trust library release failed.')
    }
}

function Get-BootstrapStockFileTrust {
    param([Parameter(Mandatory)]$Guard)
    $ErrorActionPreference = 'Stop'
    $bindings = Get-BootstrapStockTrustBindings
    if ($Guard.Handle -isnot [Microsoft.Win32.SafeHandles.SafeFileHandle] -or
        $Guard.Handle.IsClosed -or $Guard.Handle.IsInvalid -or
        $Guard.Path -isnot [string] -or -not $Guard.Path) {
        throw 'Bootstrap trust requires a live caller-owned file guard.'
    }
    $identity = Get-BootstrapStockFileIdentity -Handle $Guard.Handle -Bindings (Get-BootstrapStockFileBindings)
    if (($identity.Attributes -band 0x410) -ne 0) {
        throw 'Bootstrap trust requires a guarded ordinary file.'
    }
    $module = [IntPtr]::Zero
    $path = [IntPtr]::Zero
    $file = [IntPtr]::Zero
    $data = [IntPtr]::Zero
    $action = [IntPtr]::Zero
    $retained = $false
    $stateInitialized = $false
    try {
        $Guard.Handle.DangerousAddRef([ref]$retained)
        $module = Open-BootstrapStockTrustLibrary -Bindings $bindings
        $path = [Runtime.InteropServices.Marshal]::StringToHGlobalUni($Guard.Path)
        $file = [Runtime.InteropServices.Marshal]::AllocHGlobal(32)
        $data = [Runtime.InteropServices.Marshal]::AllocHGlobal(80)
        $action = [Runtime.InteropServices.Marshal]::AllocHGlobal(16)
        [Runtime.InteropServices.Marshal]::Copy([byte[]]::new(32), 0, $file, 32)
        [Runtime.InteropServices.Marshal]::Copy([byte[]]::new(80), 0, $data, 80)
        $stateInitialized = $true
        [Runtime.InteropServices.Marshal]::Copy(
            ([Guid]'00AAC56B-CD44-11D0-8CC2-00C04FC295EE').ToByteArray(), 0, $action, 16
        )
        # Use the qualified legacy stock layout, without a signature-settings extension.
        [Runtime.InteropServices.Marshal]::WriteInt32($file, 0, 32)
        [Runtime.InteropServices.Marshal]::WriteIntPtr($file, 8, $path)
        [Runtime.InteropServices.Marshal]::WriteIntPtr($file, 16, $Guard.Handle.DangerousGetHandle())
        [Runtime.InteropServices.Marshal]::WriteInt32($data, 0, 80)
        [Runtime.InteropServices.Marshal]::WriteInt32($data, 24, 2)
        [Runtime.InteropServices.Marshal]::WriteInt32($data, 32, 1)
        [Runtime.InteropServices.Marshal]::WriteIntPtr($data, 40, $file)
        [Runtime.InteropServices.Marshal]::WriteInt32($data, 48, 1)
        [Runtime.InteropServices.Marshal]::WriteInt32($data, 72, 0x1000)
        $status = $bindings.Verify.Invoke($null, [object[]]@([IntPtr]::new(-1), $action, $data))
        $publisher = $null
        if ($status -eq 0) {
            $state = [Runtime.InteropServices.Marshal]::ReadIntPtr($data, 56)
            if ($state -eq [IntPtr]::Zero) { throw 'Verified bootstrap signer state is unavailable.' }
            $provider = $bindings.Provider.Invoke($null, [object[]]@($state))
            if ($provider -eq [IntPtr]::Zero) { throw 'Verified bootstrap signer provider is unavailable.' }
            $signer = $bindings.Signer.Invoke($null, [object[]]@($provider, [uint32]0, [uint32]0, [uint32]0))
            if ($signer -eq [IntPtr]::Zero -or
                [Runtime.InteropServices.Marshal]::ReadInt32($signer, 0) -lt 64 -or
                [Runtime.InteropServices.Marshal]::ReadInt32($signer, 40) -ne 0 -or
                [Runtime.InteropServices.Marshal]::ReadInt32($signer, 12) -le 0) {
                throw 'Verified bootstrap signer certificate is unavailable.'
            }
            $chain = [Runtime.InteropServices.Marshal]::ReadIntPtr($signer, 16)
            if ($chain -eq [IntPtr]::Zero -or
                [Runtime.InteropServices.Marshal]::ReadInt32($chain, 0) -lt 88) {
                throw 'Verified bootstrap signer certificate chain is unavailable.'
            }
            $certificate = [Runtime.InteropServices.Marshal]::ReadIntPtr($chain, 8)
            if ($certificate -eq [IntPtr]::Zero) { throw 'Verified bootstrap signer certificate is empty.' }
            $leaf = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certificate)
            try {
                $publisher = $leaf.GetNameInfo(
                    [Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false
                )
                if (-not $publisher) { throw 'Verified bootstrap publisher is empty.' }
            } finally { $leaf.Dispose() }
        }
        $result = [pscustomobject]@{ Status = [uint32]$status; Publisher = $publisher }
    } finally {
        try {
            # Provider-owned pointers remain valid only until CLOSE, while every input is retained.
            if ($stateInitialized -and
                [Runtime.InteropServices.Marshal]::ReadIntPtr($data, 56) -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::WriteInt32($data, 48, 2)
                $closeStatus = $bindings.Verify.Invoke($null, [object[]]@([IntPtr]::new(-1), $action, $data))
                if ($closeStatus -ne 0) {
                    throw ('Bootstrap signer state close failed: 0x{0:X8}.' -f [uint32]$closeStatus)
                }
            }
        } finally {
            foreach ($pointer in @($action, $data, $file, $path)) {
                if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::FreeHGlobal($pointer) }
            }
            try {
                if ($module -ne [IntPtr]::Zero) {
                    Close-BootstrapStockTrustLibrary -Bindings $bindings -Module $module
                }
            } finally {
                if ($retained) { $Guard.Handle.DangerousRelease() }
            }
        }
    }
    return $result
}
