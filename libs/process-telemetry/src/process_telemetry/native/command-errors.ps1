# Dot-source by the provider-qualified path. Import starts no process or storage work.
function Write-CommandErrorEvidence {
    param(
        [string]$Command, [string]$Source, [string]$Cause,
        [Nullable[long]]$ExitCode = $null, [string]$Directory
    )
    $lock = $null
    $data = $null
    try {
        if ($Command -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\z' -or
            $Source -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\z' -or
            $Cause -cnotmatch '^[a-z][a-z-]{0,63}\z') { throw 'Invalid native evidence identity.' }
        $support = [IO.Path]::GetDirectoryName($MyInvocation.MyCommand.ScriptBlock.File)
        $message = $null
        $catalog = Join-Path $support 'command-causes.tsv'
        if ([IO.FileInfo]::new($catalog).Length -gt 8192) { throw 'Native cause catalog exceeds its bound.' }
        foreach ($line in [IO.File]::ReadAllLines($catalog)) {
            $pair = $line.Split([char]9, 2)
            if ($pair.Length -eq 2 -and $pair[0] -ceq $Cause) { $message = $pair[1]; break }
        }
        if (-not $message -or $message.Length -gt 1600 -or $message -cmatch '[^\x20-\x7e]') {
            throw 'Unknown native evidence cause.'
        }
        . (Join-Path $support 'private-paths.ps1')
        if (-not $Directory) {
            $Directory = Join-Path $env:USERPROFILE '.copilot-extensions\logs\diagnostics\commands'
        }
        Initialize-CEPrivateDirectory $Directory
        $lock = Open-CEPrivateFile (Join-Path $Directory '.command-errors.lock') -TokenOwner
        $clock = [Diagnostics.Stopwatch]::StartNew()
        while ($true) {
            try { $lock.Lock(1073741824, 1); break }
            catch [IO.IOException] {
                if ($clock.Elapsed.TotalSeconds -ge 2) { throw }
                [Threading.Thread]::Sleep(5)
            }
        }
        $active = Join-Path $Directory 'command-errors.jsonl'
        $data = Open-CEPrivateFile $active
        $now = [DateTime]::UtcNow
        $id = [Guid]::NewGuid().ToString('N')
        $rotate = $data.Length -ge 5000000
        if ($data.Length -gt 0) {
            [void]$data.Seek(-1, [IO.SeekOrigin]::End)
            if ($data.ReadByte() -ne 10) { $rotate = $true }
            [void]$data.Seek(0, [IO.SeekOrigin]::Begin)
            $buffer = New-Object byte[] ([Math]::Min(4096, $data.Length))
            $count = $data.Read($buffer, 0, $buffer.Length)
            $first = [Text.Encoding]::UTF8.GetString($buffer, 0, $count).Split([char]10, 2)[0]
            $day = $now.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
            if ($first -cnotmatch ('"ts"\s*:\s*"' + $day + 'T')) { $rotate = $true }
        }
        if ($rotate) {
            $data.Dispose(); $data = $null
            $stamp = $now.ToString('yyyyMMddTHHmmssffffffZ', [Globalization.CultureInfo]::InvariantCulture)
            [IO.File]::Move($active, (Join-Path $Directory ("command-errors.$stamp-$id.jsonl")))
            $data = Open-CEPrivateFile $active
        }
        $record = [ordered]@{
            schema='command-error.v1'
            ts=$now.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'+00:00'", [Globalization.CultureInfo]::InvariantCulture)
            command=$Command; source=$Source; phase='entry'; code=$Cause; message=$message
            exit_code=$ExitCode; correlation_id=$id; completeness='producer-shaped'; pid=$PID
        }
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($record | ConvertTo-Json -Compress) + "`n")
        [void]$data.Seek(0, [IO.SeekOrigin]::End)
        $data.Write($bytes, 0, $bytes.Length)
        $data.Flush()
    } catch {
        try { [Console]::Error.WriteLine('command evidence unavailable (native writer); inspect command-error discovery') }
        catch {}
    } finally {
        foreach ($stream in @($data, $lock)) {
            if ($null -ne $stream) {
                try { $stream.Dispose() }
                catch {
                    try { [Console]::Error.WriteLine('command evidence unavailable (native writer); inspect command-error discovery') }
                    catch {}
                }
            }
        }
    }
}
