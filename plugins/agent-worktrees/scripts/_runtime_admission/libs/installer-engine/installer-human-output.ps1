# Source-owned initialization for declared, captured human installer output.
# Dot-sourced by the original entrypoint, under its original execution policy.
if ($env:COPILOT_EXTENSIONS_HUMAN_UTF8 -eq '1') {
    if (-not [Console]::IsOutputRedirected -or -not [Console]::IsErrorRedirected) {
        throw 'Human UTF-8 output requires redirected stdout and stderr.'
    }
    # Keep native-command decoding and pipeline stdin at their original
    # encodings. Only this producer's redirected text writers become UTF-8.
    & {
        $encoding = [Text.UTF8Encoding]::new($false)
        $stdout = [IO.StreamWriter]::new([Console]::OpenStandardOutput(), $encoding, 1024, $true)
        $stderr = [IO.StreamWriter]::new([Console]::OpenStandardError(), $encoding, 1024, $true)
        $stdout.AutoFlush = $true
        $stderr.AutoFlush = $true
        [Console]::SetOut($stdout)
        [Console]::SetError($stderr)
    }
}
