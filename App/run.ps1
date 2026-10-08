# MarketObservationPilot runner. Runner diagnostics and Python output use separate logs.
# No secret contents, tokens or exception messages are logged.
$ErrorActionPreference = 'Stop'
$dataDir = Join-Path $env:LOCALAPPDATA 'MarketObservationPilot\Data'
$aisStore = Join-Path $env:LOCALAPPDATA 'AISStream\api-key.dpapi'
$openSkyStore = Join-Path $env:LOCALAPPDATA 'OpenSky\credentials.dpapi'
$runnerLog = Join-Path $dataDir 'runner.log'
$collectorLog = Join-Path $dataDir 'collector.log'
$step = 'init'

function Write-Sanitized([string]$message) {
    $when = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    Add-Content -LiteralPath $runnerLog -Value "$when $message" -Encoding utf8
}

try {
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
    Write-Sanitized 'runner entered (separate diagnostic log)'
    $step = 'ais_key_present'
    if (-not (Test-Path -LiteralPath $aisStore -PathType Leaf)) {
        throw [System.IO.FileNotFoundException]::new('AIS DPAPI key unavailable')
    }
    $step = 'uv_lookup'
    $uvCommand = Get-Command 'uv.exe' -ErrorAction SilentlyContinue
    if (-not $uvCommand) { $uvCommand = Get-Command 'uv' -ErrorAction SilentlyContinue }
    if ($uvCommand) {
        $uvExe = $uvCommand.Source
    } else {
        $candidates = @(
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
            (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe')
        )
        $uvExe = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $uvExe) { throw [System.IO.FileNotFoundException]::new('uv.exe not found') }

    $step = 'ais_dpapi_decrypt'
    $aisSecure = Get-Content -LiteralPath $aisStore | ConvertTo-SecureString
    $aisCredential = [pscredential]::new('aisstream', $aisSecure)
    $env:AISSTREAM_API_KEY = $aisCredential.GetNetworkCredential().Password
    Remove-Variable aisCredential, aisSecure -ErrorAction SilentlyContinue

    $step = 'opensky_dpapi_decrypt'
    if (Test-Path -LiteralPath $openSkyStore -PathType Leaf) {
        $osSecure = Get-Content -LiteralPath $openSkyStore | ConvertTo-SecureString
        $osCredential = [pscredential]::new('opensky', $osSecure)
        $osJson = $osCredential.GetNetworkCredential().Password | ConvertFrom-Json -AsHashtable
        if (($osJson -isnot [System.Collections.IDictionary]) -or
            ($osJson['clientId'] -isnot [string]) -or ($osJson['clientSecret'] -isnot [string]) -or
            [string]::IsNullOrWhiteSpace($osJson['clientId']) -or
            [string]::IsNullOrWhiteSpace($osJson['clientSecret'])) {
            throw [System.FormatException]::new('Invalid OpenSky credential structure')
        }
        $env:OPENSKY_CLIENT_ID = [string]$osJson['clientId']
        $env:OPENSKY_CLIENT_SECRET = [string]$osJson['clientSecret']
        Remove-Variable osSecure, osCredential, osJson -ErrorAction SilentlyContinue
    }

    $step = 'python_collector'
    Set-Location -LiteralPath $PSScriptRoot
    Write-Sanitized 'runner starting collector (no secrets logged)'
    & $uvExe run --python 3.12 --with websockets python .\collector.py --db (Join-Path $dataDir 'pilot.sqlite') --days 14 *>> $collectorLog
    $result = $LASTEXITCODE
    if ($result -ne 0) { throw [System.InvalidOperationException]::new('Collector exited nonzero') }
    Write-Sanitized 'runner collector finished normally'
}
catch {
    $kind = $_.Exception.GetType().Name
    try { Write-Sanitized "runner failed stage=$step kind=$kind" } catch {}
    exit 1
}
finally {
    Remove-Item Env:\AISSTREAM_API_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:\OPENSKY_CLIENT_ID -ErrorAction SilentlyContinue
    Remove-Item Env:\OPENSKY_CLIENT_SECRET -ErrorAction SilentlyContinue
    Remove-Variable aisCredential, aisSecure, osSecure, osCredential, osJson -ErrorAction SilentlyContinue
}
