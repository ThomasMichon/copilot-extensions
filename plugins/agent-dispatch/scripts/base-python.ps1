function Get-DispatchBasePreferencePath {
    $path = Join-Path $InstallDir 'base-python.json'
    try { $attributes = [IO.File]::GetAttributes($path) }
    catch [IO.FileNotFoundException] { return $path }
    catch [IO.DirectoryNotFoundException] { return $path }
    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($attributes -band [IO.FileAttributes]::Directory)) {
        throw "Dispatch base Python preference must be a regular file: $path"
    }
    return $path
}

function Get-DispatchPythonIdentity {
    param([Parameter(Mandatory)][string]$Python)
    $diagnostic = ''
    $probe = 'import json,platform,struct,sys; print(json.dumps(dict(implementation=sys.implementation.name,version=list(sys.version_info[:3]),machine=platform.machine(),bits=struct.calcsize("P")*8,executable=sys.executable,base_executable=sys._base_executable)))'
    $output = Invoke-Hidden -FilePath $Python -ArgList @('-I', '-B', '-c', $probe) -ErrorOutput ([ref]$diagnostic)
    if ($LASTEXITCODE -ne 0) {
        throw "Dispatch base Python probe failed (exit ${LASTEXITCODE}): $output$diagnostic"
    }
    $identityJson = "$output"
    if (-not $identityJson.TrimStart().StartsWith('{')) {
        throw 'Dispatch base Python probe must return a JSON object'
    }
    $identity = $identityJson | ConvertFrom-Json -ErrorAction Stop
    if ($identity.implementation -cne 'cpython' -or
        @($identity.version).Count -ne 3 -or
        $identity.version[0] -ne 3 -or $identity.version[1] -lt 10 -or
        $identity.bits -ne 64 -or $identity.machine -notin @('AMD64', 'ARM64', 'x86_64', 'aarch64') -or
        $identity.executable -isnot [string] -or $identity.base_executable -isnot [string] -or
        -not [IO.Path]::IsPathRooted($identity.executable) -or
        -not [IO.Path]::IsPathRooted($identity.base_executable)) {
        throw 'Dispatch requires a supported 64-bit CPython 3.10+ interpreter'
    }
    return $identity
}

function Assert-DispatchSignedBasePython {
    param([Parameter(Mandatory)][string]$Python)
    if (-not [IO.Path]::IsPathRooted($Python) -or
        $Python -notmatch '^(?:[A-Za-z]:[\\/]|\\\\)' -or
        [IO.Path]::GetExtension($Python) -ine '.exe') {
        throw 'Dispatch base Python must be an absolute executable path'
    }
    $item = Get-Item -LiteralPath $Python -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Dispatch base Python must be a regular executable: $Python"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $item.FullName -ErrorAction Stop
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
        $signature.SignerCertificate.GetNameInfo([Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -cne 'Python Software Foundation') {
        throw "Dispatch base Python requires a valid Python Software Foundation signature: $Python"
    }
    $identity = Get-DispatchPythonIdentity -Python $item.FullName
    if (-not [string]::Equals([IO.Path]::GetFullPath($identity.executable), $item.FullName, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetFullPath($identity.base_executable), $item.FullName, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Dispatch selected Python must be a base interpreter, not a virtual environment'
    }
    return $item.FullName
}

function Resolve-DispatchBasePython {
    $preference = Get-DispatchBasePreferencePath
    $python = $env:AGENT_DISPATCH_BASE_PYTHON
    if (-not $python -and [IO.File]::Exists($preference)) {
        $content = [IO.File]::ReadAllText($preference)
        if (-not $content.TrimStart().StartsWith('{')) {
            throw "Invalid Dispatch base Python preference (expected a JSON object): $preference"
        }
        $document = $content | ConvertFrom-Json -ErrorAction Stop
        if (-not $document -or ($document.version -isnot [int] -and $document.version -isnot [long]) -or $document.version -ne 1 -or
            $document.python -isnot [string] -or -not $document.python.Trim()) {
            throw "Invalid Dispatch base Python preference: $preference"
        }
        $python = $document.python
    }
    if ($python) { return Assert-DispatchSignedBasePython -Python $python }
    return $null
}

function Assert-DispatchCandidateBase {
    param([Parameter(Mandatory)][string]$Python, [string]$BasePython)
    if (-not $BasePython) { return }
    $identity = Get-DispatchPythonIdentity -Python $Python
    if (-not [string]::Equals([IO.Path]::GetFullPath($identity.base_executable),
        [IO.Path]::GetFullPath($BasePython), [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Dispatch candidate uses a different base Python; refusing to modify or reuse it'
    }
}

function Publish-DispatchBasePython {
    param([string]$Python)
    if (-not $Python) { return }
    $path = Get-DispatchBasePreferencePath
    $document = @{ version = 1; python = $Python } | ConvertTo-Json -Compress
    Publish-FileAtomically -Path $path -Content $document -Encoding (New-Object Text.UTF8Encoding($false))
}
