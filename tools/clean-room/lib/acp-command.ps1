function ConvertTo-CleanRoomBashLiteral([string]$Value) {
    $singleQuoteEscape = "'" + '"' + "'" + '"' + "'"
    return "'" + $Value.Replace("'", $singleQuoteEscape) + "'"
}

function New-CleanRoomAcpCommand([object[]]$PluginDirs = @()) {
    $command = 'copilot --acp --stdio --allow-all --experimental'
    foreach ($pluginDir in $PluginDirs) {
        $quotedDir = ConvertTo-CleanRoomBashLiteral ([string]$pluginDir)
        $command += " --plugin-dir $quotedDir"
    }
    return $command
}
