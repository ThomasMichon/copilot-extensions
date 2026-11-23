function Invoke-UvBootstrapCapturedProcess {
    param(
        [Parameter(Mandatory)][string]$BootstrapPython,
        [Parameter(Mandatory)][string]$ProcessHelper,
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgList,
        [hashtable]$Environment,
        [Parameter(Mandatory)][ref]$ErrorOutput,
        [ValidateRange(1001, 2147483647)][int]$TimeoutMilliseconds
    )
    $clock = [Diagnostics.Stopwatch]::StartNew()
    foreach ($path in @($BootstrapPython, $ProcessHelper)) {
        if (-not [IO.Path]::IsPathRooted($path) -or -not [IO.File]::Exists($path)) {
            throw 'Prepared tree capture requires existing absolute helper paths.'
        }
    }
    # QPC is Python's monotonic clock on Windows, not a renewed child timeout.
    # The original one-second reserve covers nested settlement, relay and the
    # direct capture owner's final emergency retirement.
    $deadline = ([Diagnostics.Stopwatch]::GetTimestamp() /
        [double][Diagnostics.Stopwatch]::Frequency) +
        (($TimeoutMilliseconds - $clock.ElapsedMilliseconds) / 1000.0) - 0.5
    $arguments = @('-I', '-S', '-B', '-X', 'utf8', $ProcessHelper,
        '--deadline', $deadline.ToString('R', [Globalization.CultureInfo]::InvariantCulture),
        '--parent-pid', [string]$PID,
        '--', $FilePath) + $ArgList
    $remaining = [int]($TimeoutMilliseconds - $clock.ElapsedMilliseconds)
    if ($remaining -le 250) { throw 'Prepared tree capture deadline expired before launch.' }
    Invoke-Hidden -FilePath $BootstrapPython -ArgList $arguments -Environment $Environment `
        -InputText '' -ErrorOutput $ErrorOutput -TimeoutMilliseconds $remaining -CleanupMilliseconds 250 `
        -CaptureEncoding ([Text.UTF8Encoding]::new($false))
}

function Get-UvBootstrapEnvironment {
    return @{
        UV_PYTHON = $null; UV_PYTHON_PREFERENCE = $null
        UV_MANAGED_PYTHON = $null; UV_NO_MANAGED_PYTHON = $null
        VIRTUAL_ENV = $null; CONDA_PREFIX = $null
        PYTHONPATH = $null; PYTHONHOME = $null
        UV_WORKING_DIR = $null; UV_WORKING_DIRECTORY = $null
        UV_ISOLATED = $null; UV_ENV_FILE = $null
        UV_PROJECT = $null; UV_FROZEN = $null; UV_LOCKED = $null; UV_NO_SYNC = $null
    }
}

function Resolve-UvBootstrapPython {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RequestFile,
        [Parameter(Mandatory)][scriptblock]$InvokeCaptured,
        [ValidateSet('Inspect', 'Prepare')][string]$Operation = 'Inspect',
        [switch]$IsolateSelection
    )

    $callbackParameters = $InvokeCaptured.Ast.ParamBlock
    if ($IsolateSelection -and ($null -eq $callbackParameters -or
        -not @($callbackParameters.Parameters | Where-Object {
            $_.Name.VariablePath.UserPath -ieq 'Environment'
        }).Count)) {
        throw 'Bootstrap uv capture must accept child Environment overrides without changing the caller environment.'
    }
    $requestRoot = [IO.Path]::GetPathRoot($RequestFile)
    if (-not [IO.Path]::IsPathRooted($RequestFile) -or -not $requestRoot -or
        ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
            ($requestRoot -in @('\', '/') -or $requestRoot -match '^[A-Za-z]:$'))) {
        throw 'Bootstrap Python request must have a fully qualified path.'
    }
    $request = Get-Item -LiteralPath $RequestFile -Force -ErrorAction Stop
    if ($request.PSIsContainer -or $request.Length -gt 64 -or
        ($request.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Bootstrap Python request must be a small ordinary file.'
    }
    $bytes = [IO.File]::ReadAllBytes($request.FullName)
    $requirement = [Text.Encoding]::ASCII.GetString($bytes)
    if ($requirement -cnotmatch '\Acpython@3\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\r?\n\z') {
        throw 'Bootstrap Python request must declare one exact stable CPython version.'
    }
    $requirement = $requirement.TrimEnd([char]13, [char]10)
    $commands = @(Get-Command uv -CommandType Application -ErrorAction SilentlyContinue)
    if (-not $commands.Count) {
        return [pscustomobject]@{
            Status = 'unavailable'; Reason = 'uv-not-on-path'
            Uv = $null; Python = $null; Requirement = $requirement
        }
    }
    $uv = $commands[0].Source
    if (-not [IO.Path]::IsPathRooted($uv) -or -not [IO.File]::Exists($uv) -or
        $uv -match '[\\/]WindowsApps[\\/]') {
        throw 'PATH uv must identify an existing application, not an execution alias.'
    }
    # The caller retains its deadline, containment, cancellation and stream owner.
    $invoke = {
        param([string[]]$Arguments)
        $options = @{FilePath=$uv; ArgList=$Arguments}
        if ($IsolateSelection) {
            $options.Environment = Get-UvBootstrapEnvironment
            $options.Environment.UV_CONFIG_FILE = $null
            $options.Environment.UV_NO_CONFIG = '1'
        }
        $result = & $InvokeCaptured @options
        if ($null -eq $result -or $result -is [array] -or
            ($result.ExitCode -isnot [int] -and $result.ExitCode -isnot [long]) -or
            $result.ExitCode -lt [int]::MinValue -or $result.ExitCode -gt [int]::MaxValue -or
            $result.Stdout -isnot [string] -or
            $result.Stderr -isnot [string]) {
            throw 'Bootstrap uv invocation did not return a confirmed captured result.'
        }
        if (($result.Stdout + $result.Stderr) -match '(?i)(?:os error|WinError)\s+448\b') {
            throw ('Bootstrap uv execution is blocked by operating-system policy: ' + $result.Stderr + $result.Stdout)
        }
        return $result
    }
    $version = & $invoke -Arguments @('--version')
    if ($version.ExitCode -ne 0) {
        throw ('Bootstrap uv version qualification failed: ' + $version.Stderr)
    }
    $match = [regex]::Match($version.Stdout, '\Auv[ \t]+([0-9]+\.[0-9]+\.[0-9]+)(?:[ \t(+]|\r?\n|\z)')
    if (-not $match.Success -or [version]$match.Groups[1].Value -lt [version]'0.8.0') {
        throw 'Bootstrap Python requires PATH uv 0.8.0 or newer; use the prerequisite setup owner.'
    }
    if ($Operation -eq 'Prepare') {
        $installed = & $invoke -Arguments @('python', 'install', '--no-bin', '--no-registry', $requirement)
        if ($installed.ExitCode -ne 0) {
            throw ('Bootstrap managed Python preparation failed: ' + $installed.Stderr)
        }
    }
    $found = & $invoke -Arguments @(
        'python', 'find', '--no-project', '--system', '--managed-python',
        '--no-python-downloads', $requirement
    )
    if ($found.ExitCode -in @(1, 2) -and $Operation -eq 'Inspect' -and
        $found.Stderr -match '(?im)^(?:error: )?No interpreter found (?:for|matching)\b') {
        return [pscustomobject]@{
            Status = 'unavailable'; Reason = 'managed-python-not-prepared'
            Uv = $uv; Python = $null; Requirement = $requirement
        }
    }
    if ($found.ExitCode -ne 0) {
        throw ('Bootstrap managed Python lookup failed: ' + $found.Stderr)
    }
    $python = $found.Stdout.TrimEnd([char]13, [char]10)
    $pythonRoot = [IO.Path]::GetPathRoot($python)
    if (-not $python -or $python -match '[\r\n\x00]' -or
        -not [IO.Path]::IsPathRooted($python) -or -not $pythonRoot -or
        ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
            ($pythonRoot -in @('\', '/') -or $pythonRoot -match '^[A-Za-z]:$')) -or
        -not [IO.File]::Exists($python)) {
        throw 'Bootstrap uv lookup did not return one existing absolute Python executable.'
    }
    return [pscustomobject]@{
        Status = 'selected'; Reason = 'uv-managed-python'
        Uv = $uv; Python = $python; Requirement = $requirement
    }
}
