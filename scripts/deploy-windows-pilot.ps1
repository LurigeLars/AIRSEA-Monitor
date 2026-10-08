# Safely upgrade one local shadow pilot from AIS schema v1 to v2.
# No credentials, host paths, vessel observations or database contents are uploaded.
# Run from a clean checkout of this repository with: -Apply
[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$Root = Join-Path $env:LOCALAPPDATA 'MarketObservationPilot'
$LiveApp = Join-Path $Root 'App'
$Db = Join-Path $Root 'Data\pilot.sqlite'
$TaskName = 'MarketObservationPilotShadow'
$Nonce = [guid]::NewGuid().ToString('N').Substring(0, 12)
$BackupDirectory = Join-Path $env:LOCALAPPDATA ('AIRSEA-Monitor-Backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Nonce)
$PreStopDb = Join-Path $BackupDirectory 'pilot-prestop.sqlite'
$FinalDb = Join-Path $BackupDirectory 'pilot-final.sqlite'
$BackupApp = Join-Path $BackupDirectory 'App-v1'
$StagedApp = Join-Path $Root ('App.v2-staged-' + $Nonce)
$PreviousApp = Join-Path $Root ('App.v1-previous-' + $Nonce)
$FailedApp = Join-Path $Root ('App.v2-failed-' + $Nonce)
$ChangeStarted = $false
$DbMaybeChanged = $false
$StoppedAndVerified = $false
$Complete = $false

function Invoke-PythonChecked {
    param([Parameter(Mandatory)][string[]]$Arguments)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Python validation step failed.' }
}
function Get-RunnerCount {
    return @(Get-PilotRunnerProcesses -AppDirectory $LiveApp).Count
}
function Wait-TaskStopped {
    for ($n = 0; $n -lt 30; $n++) {
        if ((Get-RunnerCount) -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw 'Pilot runner still present after stopping; refusing to change files.'
}
function Wait-TaskStarted {
    for ($n = 0; $n -lt 40; $n++) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        if ($task.State -eq 'Running' -and (Get-RunnerCount) -eq 1) {
            Start-Sleep -Seconds 3
            if ((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running' -and
                (Get-RunnerCount) -eq 1) { return }
        }
        Start-Sleep -Seconds 1
    }
    throw 'Pilot runner failed to start and stabilize.'
}

try {
    if (-not $Apply) {
        Write-Host 'NO ACTION: add -Apply to run the controlled local rollout.'
        exit 0
    }
    if (-not (Test-Path -LiteralPath $LiveApp -PathType Container)) {
        throw 'Pilot application is missing.'
    }
    if (-not (Test-Path -LiteralPath $Db -PathType Leaf)) {
        throw 'Pilot SQLite database is missing.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'Data\salt.bin') -PathType Leaf)) {
        throw 'Pilot pseudonymization salt is missing.'
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($task.State -ne 'Running') { throw 'Expected exactly one running shadow task.' }

    . (Join-Path $LiveApp 'pilot-processes.ps1')
    if ((Get-RunnerCount) -ne 1) { throw 'Expected exactly one verified pilot runner.' }
    if (Test-Path -LiteralPath (Join-Path $LiveApp 'ais_v2.py')) {
        throw 'Pilot already has v2 source; refusing to overwrite an existing upgrade.'
    }
    if (Test-Path -LiteralPath $StagedApp) { throw 'Staging directory collision.' }
    if (Test-Path -LiteralPath $PreviousApp) { throw 'Previous-app directory collision.' }

    $gitBranch = (& git -C $Repo branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $gitBranch -ne 'main') {
        throw 'Run this from a checked-out AIRSEA-Monitor main branch.'
    }
    $gitDirty = @(& git -C $Repo status --porcelain)
    if ($LASTEXITCODE -ne 0 -or $gitDirty.Count -ne 0) {
        throw 'The release checkout has uncommitted changes.'
    }

    # Verify source and synthetic tests before touching the live task.
    Push-Location $Repo
    try {
        Invoke-PythonChecked -Arguments @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
        Invoke-PythonChecked -Arguments @('scripts/check_publication.py')
        Invoke-PythonChecked -Arguments @('scripts/validate_migration.py', '--db', $Db)
    }
    finally { Pop-Location }

    # Backup and fully prepare the staged application while v1 remains running.
    New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
    Copy-Item -LiteralPath $LiveApp -Destination $BackupApp -Recurse -Force -ErrorAction Stop
    Invoke-PythonChecked -Arguments @(
        (Join-Path $Repo 'scripts/snapshot_sqlite.py'),
        '--db', $Db, '--dest', $PreStopDb, '--expect-version', '1'
    )
    Copy-Item -LiteralPath $LiveApp -Destination $StagedApp -Recurse -Force -ErrorAction Stop
    foreach ($name in @('ais_v2.py', 'pilot_core.py', 'report.py')) {
        Copy-Item -LiteralPath (Join-Path (Join-Path $Repo 'App') $name) -Destination $StagedApp -Force
    }
    Invoke-PythonChecked -Arguments @('-m', 'py_compile',
        (Join-Path $StagedApp 'ais_v2.py'),
        (Join-Path $StagedApp 'pilot_core.py'),
        (Join-Path $StagedApp 'report.py'))

    # Enter the downtime window. Disabled means no automatic task retrigger.
    $ChangeStarted = $true
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    $null = Stop-PilotOrphanedProcessTrees -AppDirectory $LiveApp
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Wait-TaskStopped
    $StoppedAndVerified = $true

    # Post-stop backup is authoritative for rollback: includes latest WAL contents.
    Invoke-PythonChecked -Arguments @(
        (Join-Path $Repo 'scripts/snapshot_sqlite.py'),
        '--db', $Db, '--dest', $FinalDb, '--expect-version', '1'
    )

    # Atomic same-volume application directory swaps.
    Rename-Item -LiteralPath $LiveApp -NewName (Split-Path $PreviousApp -Leaf)
    Rename-Item -LiteralPath $StagedApp -NewName 'App'

    # Migration must happen only when the legacy collector is fully stopped.
    $DbMaybeChanged = $true
    Invoke-PythonChecked -Arguments @(
        (Join-Path $Repo 'scripts/upgrade_sqlite.py'), '--db', $Db
    )

    Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Wait-TaskStarted
    $Complete = $true
    Write-Host 'PASS: AIS v2 is deployed; scheduled pilot has one verified runner.'
    Write-Host 'Local rollback backup retained. No secrets or AIS data uploaded.'
}
catch {
    # Sanitized error messages only; no file paths, credentials or vessel data.
    Write-Warning 'AIS rollout did not finish. Beginning local rollback where needed.'
    if ($ChangeStarted -and -not $Complete) {
        $rollbackOk = $true
        try {
            Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
            $null = Stop-PilotOrphanedProcessTrees -AppDirectory $LiveApp
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Wait-TaskStopped
            if (Test-Path -LiteralPath $PreviousApp -PathType Container) {
                if (Test-Path -LiteralPath $LiveApp) {
                    Rename-Item -LiteralPath $LiveApp -NewName (Split-Path $FailedApp -Leaf)
                }
                Rename-Item -LiteralPath $PreviousApp -NewName 'App'
            }
            elseif (-not (Test-Path -LiteralPath $LiveApp -PathType Container)) {
                Copy-Item -LiteralPath $BackupApp -Destination $LiveApp -Recurse -Force
            }
            if ($DbMaybeChanged) {
                if (-not (Test-Path -LiteralPath $FinalDb -PathType Leaf)) {
                    throw 'Verified final backup missing, cannot safely restore.'
                }
                # Both sidecars are only removed after all verified workers stop.
                foreach ($suffix in @('-wal', '-shm')) {
                    Remove-Item -LiteralPath ($Db + $suffix) -Force -ErrorAction SilentlyContinue
                }
                Copy-Item -LiteralPath $FinalDb -Destination $Db -Force
            }
            Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
            Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
            Wait-TaskStarted
        }
        catch { $rollbackOk = $false }
        if ($rollbackOk) {
            Write-Host 'ROLLBACK PASS: original application and live task were restored.'
        }
        else {
            Write-Warning 'ROLLBACK INCOMPLETE: leave task disabled and recover from retained local backups.'
        }
    }
    Write-Error -Message 'FAIL: deployment was not confirmed. Inspect local backups before retrying.' -ErrorAction Continue
    exit 1
}
