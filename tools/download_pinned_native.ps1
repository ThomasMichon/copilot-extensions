param(
    [Parameter(Mandatory)][uri]$Uri,
    [Parameter(Mandatory)][string]$OutFile,
    [ValidateRange(1, 120)][int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
if ($Uri.UserInfo -or ($Uri.Scheme -ne 'https' -and -not ($Uri.Scheme -eq 'http' -and $Uri.IsLoopback))) {
    throw 'Pinned native archives require anonymous HTTPS'
}
$deadline = [System.Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds($TimeoutSeconds))
$client = [System.Net.Http.HttpClient]::new()
$client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
$client.MaxResponseContentBufferSize = 128 * 1024 * 1024
try {
    for ($attempt = 0; $attempt -le 2; $attempt++) {
        $response = $null
        $delaySeconds = 2
        try {
            $response = $client.GetAsync($Uri, $deadline.Token).GetAwaiter().GetResult()
            if ($response.IsSuccessStatusCode) {
                $bytes = $response.Content.ReadAsByteArrayAsync($deadline.Token).GetAwaiter().GetResult()
                [System.IO.File]::WriteAllBytesAsync($OutFile, $bytes, $deadline.Token).GetAwaiter().GetResult()
                return
            }
            $status = [int]$response.StatusCode
            if ($status -notin @(408, 429, 500, 502, 503, 504)) {
                throw "Pinned native archive returned HTTP $status"
            }
            Write-Host "Pinned native archive returned HTTP $status"
            $retryAfter = $response.Headers.RetryAfter
            if ($retryAfter) {
                if ($retryAfter.Delta) { $delaySeconds = [Math]::Max(2, $retryAfter.Delta.TotalSeconds) }
                elseif ($retryAfter.Date) {
                    $delaySeconds = [Math]::Max(2, ($retryAfter.Date - [DateTimeOffset]::UtcNow).TotalSeconds)
                }
            }
        }
        catch [System.Net.Http.HttpRequestException] {
            if ($deadline.IsCancellationRequested) { throw }
            Write-Host 'Pinned native archive transport failed'
        }
        finally {
            if ($response) { $response.Dispose() }
        }
        if ($attempt -eq 2) { throw 'Pinned native archive retries exhausted' }
        [System.Threading.Tasks.Task]::Delay([TimeSpan]::FromSeconds($delaySeconds), $deadline.Token).GetAwaiter().GetResult()
    }
}
catch {
    if ($deadline.IsCancellationRequested) { throw 'Pinned native archive acquisition deadline exceeded' }
    throw
}
finally {
    $client.Dispose()
    $deadline.Dispose()
}
