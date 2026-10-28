function Remove-AwPaneArgsFile([string]$Path) {
    try {
        [IO.File]::Delete($Path)
    } catch [IO.IOException], [UnauthorizedAccessException] {
        Write-SetupLog (
            "Could not remove pane argument handoff; retained for expiry: " +
            $_.Exception.Message
        ) 'WARN'
    }
}

function Read-AwPaneArgsReceipt([string]$Path, [string]$Manifest) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $receipt = [IO.File]::ReadAllText($Path) | ConvertFrom-Json -ErrorAction Stop
        if (
            ($receipt.version -isnot [long] -and $receipt.version -isnot [int]) -or
            $receipt.version -ne 1 -or
            $receipt.manifest -ne [IO.Path]::GetFileName($Manifest) -or
            $receipt.status -notin @('consumed', 'rejected') -or
            $receipt.stage -notin @('manifest-open', 'manifest-json', 'manifest-schema', 'wrapper-path') -or
            ($receipt.pid -isnot [long] -and $receipt.pid -isnot [int]) -or
            $receipt.pid -le 0
        ) {
            throw 'Invalid pane argument receipt.'
        }
        return $receipt
    } catch {
        return [pscustomobject]@{ status = 'invalid'; stage = 'receipt-validation' }
    }
}

function New-AwPaneStartupCommand([string[]]$Command, [ref]$Manifest, [string]$RecordPath) {
    $Manifest.Value = $null
    if ($null -eq $Command) { return ,@() }
    if (
        $Command.Count -lt 5 -or
        [IO.Path]::GetFileNameWithoutExtension($Command[0]) -ne 'pwsh' -or
        (@($Command[1..2] | Sort-Object) -join ',') -ne '-NoLogo,-NoProfile' -or
        $Command[3] -ne '-File' -or -not [IO.Path]::IsPathRooted($Command[4]) -or
        -not $RecordPath -or -not [IO.Path]::IsPathRooted($RecordPath)
    ) { return ,$Command }
    $setup = [IO.FileInfo]::new($Command[4])
    $shell = $setup.Directory
    if (
        $setup.Name -notin @('default-setup.ps1', 'launch-command.ps1') -or
        $shell.Name -cnotmatch '^[0-9a-f]{32}\.shell$' -or
        $shell.Parent.Name -cnotmatch '^[0-9a-f]{32}$' -or
        $shell.Parent.Parent.FullName -ne
            (Join-Path ([IO.Path]::GetDirectoryName($RecordPath)) 'launch-attempts')
    ) { return ,$Command }
    $wrapper = Join-Path $shell.FullName 'launch-command.ps1'
    $root = Join-Path $shell.FullName 'pane-args'
    foreach ($asset in @($setup.FullName, $wrapper, $root)) {
        for ($path = $asset; $path; $path = Split-Path -Parent $path) {
            if ((Test-Path -LiteralPath $path) -and
                ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Startup argument paths must not traverse links or junctions.'
            }
        }
    }
    if (
        -not ([IO.File]::ReadAllLines($wrapper) -ccontains '# agent-worktrees:startup-manifest-v1') -or
        ($setup.Name -eq 'default-setup.ps1' -and
            -not ([IO.File]::ReadAllLines($setup.FullName) -ccontains '# agent-worktrees:native-setup-owner-v1'))
    ) { return ,$Command }
    $arguments = if ($setup.Name -eq 'default-setup.ps1') {
        # The transport wrapper must leave normal/recovery reconciliation to setup.
        @('--recovery', '--') + $Command
    } elseif ($Command.Count -gt 5) { $Command[5..($Command.Count - 1)] }
    else { return ,$Command }
    $null = [IO.Directory]::CreateDirectory($root)
    $Manifest.Value = Join-Path $root ('aw-pane-' + [Guid]::NewGuid().ToString('N') + '.json')
    $json = ConvertTo-Json -InputObject @{
        version = 1
        wrapper = $wrapper
        argv = @($arguments)
    } -Compress
    [IO.File]::WriteAllText($Manifest.Value, $json)
    return ,@($Command[0..3] + @($wrapper, '--startup-manifest', $Manifest.Value))
}
