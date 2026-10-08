# Synthetic Windows DPAPI smoke test for OpenSky credential import.
# This test never reads personal credential files or contacts OpenSky.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Importer = Join-Path $PSScriptRoot '..\App\import-opensky.ps1'
$Scratch = Join-Path $env:RUNNER_TEMP ('airsea-smoke-' + [guid]::NewGuid().ToString('N'))
$PreviousLocalAppData = $env:LOCALAPPDATA

try {
    $env:LOCALAPPDATA = Join-Path $Scratch 'localappdata'
    $null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA -Force
    $Source = Join-Path $Scratch 'synthetic.json'
    $ExpectedId = 'sample-client-not-real'
    $ExpectedSecret = 'sample-secret-not-real'
    @{ clientId = $ExpectedId; clientSecret = $ExpectedSecret } |
        ConvertTo-Json -Compress |
        Set-Content -LiteralPath $Source -Encoding utf8

    $Tokens = $null
    $ParseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        $Importer, [ref]$Tokens, [ref]$ParseErrors
    )
    if ($ParseErrors.Count -ne 0) { throw 'Importer contains PowerShell parser errors.' }

    $Stdout = & $Importer -Source $Source 6>&1 | Out-String
    if ($Stdout.Contains($ExpectedId) -or $Stdout.Contains($ExpectedSecret)) {
        throw 'Importer output leaked synthetic credentials.'
    }
    $EncryptedFile = Join-Path $env:LOCALAPPDATA 'OpenSky\credentials.dpapi'
    if (-not (Test-Path -LiteralPath $EncryptedFile -PathType Leaf)) {
        throw 'Encrypted credential output was not created.'
    }
    $Encrypted = Get-Content -LiteralPath $EncryptedFile -Raw
    if ($Encrypted.Contains($ExpectedId) -or $Encrypted.Contains($ExpectedSecret)) {
        throw 'Encrypted credential file contains plaintext input.'
    }
    $Secure = ConvertTo-SecureString -String $Encrypted
    $Recovered = [pscredential]::new('test-user', $Secure).GetNetworkCredential().Password |
        ConvertFrom-Json -AsHashtable
    if ($Recovered['clientId'] -cne $ExpectedId -or $Recovered['clientSecret'] -cne $ExpectedSecret) {
        throw 'DPAPI credential round-trip did not match synthetic input.'
    }

    $BeforeInvalid = Get-Content -LiteralPath $EncryptedFile -Raw
    $BadSource = Join-Path $Scratch 'malformed.json'
    Set-Content -LiteralPath $BadSource -Value 'invalid json' -Encoding utf8
    $Rejected = $false
    try {
        & $Importer -Source $BadSource 6>$null | Out-Null
    } catch {
        $Rejected = $true
    }
    if (-not $Rejected) { throw 'Malformed credentials were accepted.' }
    $AfterInvalid = Get-Content -LiteralPath $EncryptedFile -Raw
    if ($BeforeInvalid -cne $AfterInvalid) {
        throw 'Invalid input overwrote previously imported credentials.'
    }
    Write-Host 'OpenSky import: syntax, isolated DPAPI round-trip, output redaction and invalid-input preservation PASS.'
}
finally {
    $env:LOCALAPPDATA = $PreviousLocalAppData
    Remove-Item -LiteralPath $Scratch -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Variable ExpectedId, ExpectedSecret, Recovered, Secure, Encrypted, Stdout -ErrorAction SilentlyContinue
}
