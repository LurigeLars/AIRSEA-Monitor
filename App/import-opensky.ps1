# Import the downloaded OpenSky OAuth JSON into per-user Windows DPAPI storage.
# Never print credentials to terminal, logs or command-line arguments.
param([Parameter(Mandatory = $true)][ValidateNotNullOrEmpty()][string]$Source)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
    throw 'OpenSky credentials file not found. Supply an existing local file using -Source.'
}
$file = Get-Item -LiteralPath $Source
if ($file.Length -gt 16384 -or $file.Length -lt 30) { throw 'Unexpected OpenSky credentials file size.' }
$raw = Get-Content -LiteralPath $Source -Raw -Encoding UTF8
$parsed = $raw | ConvertFrom-Json -AsHashtable
if (-not ($parsed -is [System.Collections.IDictionary])) { throw 'Expected JSON object.' }
$id = $parsed['clientId']
$secret = $parsed['clientSecret']
if (($id -isnot [string]) -or ($secret -isnot [string]) -or $id.Length -lt 3 -or $secret.Length -lt 3) {
    throw 'OpenSky credentials.json must have clientId and clientSecret strings.'
}
$normalized = @{clientId=$id;clientSecret=$secret} | ConvertTo-Json -Compress
$dir = Join-Path $env:LOCALAPPDATA 'OpenSky'
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$dest = Join-Path $dir 'credentials.dpapi'
$temporary = Join-Path $dir ('credentials-' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    $encrypted = ConvertTo-SecureString -String $normalized -AsPlainText -Force | ConvertFrom-SecureString
    Set-Content -LiteralPath $temporary -Value $encrypted -Encoding ascii -NoNewline
    $check = Get-Content -LiteralPath $temporary | ConvertTo-SecureString
    $c = [pscredential]::new('opensky', $check)
    $verify = $c.GetNetworkCredential().Password | ConvertFrom-Json -AsHashtable
    if ($verify['clientId'] -cne $id -or $verify['clientSecret'] -cne $secret) {
        throw 'DPAPI round-trip verification failed.'
    }
    Move-Item -LiteralPath $temporary -Destination $dest -Force
    Write-Host 'OpenSky credentials imported into Windows DPAPI (current user).'
    Write-Host 'The original plaintext source file remains. Delete it after a successful authenticated test.'
} finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    Remove-Variable raw, parsed, id, secret, normalized, encrypted, check, c, verify -ErrorAction SilentlyContinue
}
