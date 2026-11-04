# Read only the existing native owner's two lock functions, never execute the
# installer's entrypoint, bootstrap, staging, registration or installation code.
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot 'install.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Index native transaction owner source is invalid' }
$definitions = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -cin @('New-IndexMutex', 'Enter-IndexStampLock')
}, $false))
if ($definitions.Count -ne 2 -or @($definitions.Name | Select-Object -Unique).Count -ne 2) {
    throw 'Index native transaction owner functions are unavailable or ambiguous'
}
. ([scriptblock]::Create(($definitions.Extent.Text -join "`n")))
