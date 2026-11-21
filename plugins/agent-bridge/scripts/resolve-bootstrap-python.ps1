. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
. (Join-Path $PSScriptRoot 'bootstrap-uv.ps1')

function Resolve-PayloadBootstrapPython {
    [CmdletBinding()]
    param(
        [ValidateSet('Inspect', 'Prepare')][string]$Operation = 'Inspect',
        [switch]$AllowUnprepared,
        [string]$RuntimeRoot,
        [hashtable]$InstallationRequest,
        [switch]$PreparedRuntimeOnly,
        [ValidateRange(1, 2147483647)][int]$TimeoutMilliseconds = 30000
    )

    $bootstrapClock = [Diagnostics.Stopwatch]::StartNew()
    $selectorMask = @{
        UV_PYTHON = $null
        UV_PYTHON_PREFERENCE = $null
        UV_MANAGED_PYTHON = $null
        UV_NO_MANAGED_PYTHON = $null
    }
    $invokeCaptured = {
        param([string]$FilePath, [string[]]$ArgList, [hashtable]$Environment = $selectorMask)
        $remaining = $TimeoutMilliseconds - $bootstrapClock.ElapsedMilliseconds
        if ($remaining -le 1) {
            throw [TimeoutException]::new('WATCHDOG: bootstrap Python selection exhausted the caller deadline.')
        }
        $cleanup = [int][Math]::Min(3000, [Math]::Max(1, [Math]::Floor($remaining / 4)))
        $diagnostic = ''
        $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList `
            -TimeoutMilliseconds ([int]$remaining) -CleanupMilliseconds $cleanup `
            -Environment $Environment `
            -ErrorOutput ([ref]$diagnostic)
        [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Stdout = [string]$output
            Stderr = $diagnostic
        }
    }
    if ($PreparedRuntimeOnly -and ($Operation -ne 'Inspect' -or $AllowUnprepared)) {
        throw 'Prepared-runtime validation cannot prepare or accept a missing runtime.'
    }
    if ($Operation -eq 'Inspect' -and -not $AllowUnprepared) {
        if (-not $PSBoundParameters.ContainsKey('RuntimeRoot')) {
            $contextPointer = $env:COPILOT_EXTENSIONS_CONTEXT
            $requestedRoot = $null
            if ($InstallationRequest -and $InstallationRequest['parameters'] -is [Collections.IDictionary]) {
                $parameters = $InstallationRequest['parameters']
                if ($parameters['Context']) { $contextPointer = [string]$parameters['Context'] }
                if ($parameters['LegacyRoot']) { $requestedRoot = [string]$parameters['LegacyRoot'] }
            }
            $RuntimeRoot = if ($contextPointer) {
                $pointer = $contextPointer
                $pointerRoot = [IO.Path]::GetPathRoot($pointer)
                if (-not [IO.Path]::IsPathRooted($pointer) -or -not $pointerRoot -or
                    $pointerRoot -in @('\', '/') -or $pointerRoot -match '^[A-Za-z]:$') {
                    throw 'Bridge bootstrap context must be a fully qualified receipt path.'
                }
                $pointer = [IO.Path]::GetFullPath($pointer)
                $pluginRoot = Split-Path -Parent $pointer
                $pluginsRoot = Split-Path -Parent $pluginRoot
                $cellRoot = Split-Path -Parent $pluginsRoot
                $marketplacesRoot = Split-Path -Parent $cellRoot
                if ((Split-Path -Leaf $pointer) -cne 'install.json' -or
                    (Split-Path -Leaf $pluginRoot) -cne 'agent-bridge' -or
                    (Split-Path -Leaf $pluginsRoot) -cne 'plugins' -or
                    (Split-Path -Leaf $cellRoot) -cnotmatch '^[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}$' -or
                    (Split-Path -Leaf $marketplacesRoot) -cne 'marketplaces') {
                    throw 'Bridge bootstrap context must name a canonical agent-bridge install.json.'
                }
                foreach ($path in @($pointer, $pluginRoot, $pluginsRoot, $cellRoot, $marketplacesRoot)) {
                    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
                    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                        throw 'Bridge bootstrap context may not traverse linked installation paths.'
                    }
                }
                # The locator selects only an isolated stdlib helper; SDK admission still follows.
                $pluginRoot
            } elseif ($requestedRoot) {
                $requestedRoot
            } elseif ($env:AGENT_BRIDGE_INSTALL_DIR) {
                $env:AGENT_BRIDGE_INSTALL_DIR
            } elseif ($env:AGENT_BRIDGE_CONFIG_DIR) {
                $env:AGENT_BRIDGE_CONFIG_DIR
            } else { Join-Path $HOME '.agent-bridge' }
        }
        if ($RuntimeRoot) {
            $root = [IO.Path]::GetPathRoot($RuntimeRoot)
            if (-not [IO.Path]::IsPathRooted($RuntimeRoot) -or -not $root -or
                ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
                    ($root -in @('\', '/') -or $root -match '^[A-Za-z]:$'))) {
                throw 'Bridge bootstrap runtime root must be a fully qualified path.'
            }
        }
        $previousRoot = $env:AGENT_RT_ROOT
        $runtimePython = $null
        try {
            $env:AGENT_RT_ROOT = $RuntimeRoot
            $AgentRtPy = $null
            . (Join-Path $PSScriptRoot 'resolve-runtime.ps1') -TraceMode read-only
            $runtimePython = $AgentRtPy
        } finally {
            $env:AGENT_RT_ROOT = $previousRoot
        }
        if ($runtimePython) {
            # Bootstrap uses only isolated stdlib; application admission still follows.
            $probe = & $invokeCaptured -FilePath $runtimePython -ArgList @(
                '-I', '-S', '-B', '-c', 'import sys; sys.exit(sys.version_info < (3,10))'
            )
            if ($probe.ExitCode -eq 0) { return $runtimePython }
            if ($probe.ExitCode -ne 1 -or $probe.Stdout -or $probe.Stderr) {
                throw ('Prepared Bridge bootstrap execution failed: ' + $probe.Stderr)
            }
        }
        if ($PreparedRuntimeOnly) {
            throw 'The selected Bridge runtime is not prepared for offline bootstrap.'
        }
    }

    $selection = Resolve-UvBootstrapPython `
        -RequestFile (Join-Path $PSScriptRoot 'bootstrap-python-version.txt') `
        -Operation $Operation -InvokeCaptured $invokeCaptured -IsolateSelection
    if ($selection.Status -ne 'selected') {
        if ($AllowUnprepared -and $Operation -eq 'Inspect') { return $null }
        throw (
            'Bootstrap prerequisite is unavailable (' + $selection.Reason + '). ' +
            'Use explicit Bridge installation to prepare the declared uv-managed Python; ' +
            'ordinary communication does not download Python.'
        )
    }
    return $selection.Python
}
