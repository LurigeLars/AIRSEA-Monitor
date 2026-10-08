param([switch] $PurgeData)
$ErrorActionPreference = 'Stop'
$taskName = 'MarketObservationPilotShadow'
$app = Join-Path $env:LOCALAPPDATA 'MarketObservationPilot\App'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}
. (Join-Path $PSScriptRoot 'pilot-processes.ps1')
$stopped = Stop-PilotOrphanedProcessTrees -AppDirectory $app
if ($task) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
if ($PurgeData) {
    Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'MarketObservationPilot\Data') -Recurse -Force
    Write-Host 'Local pilot observations purged. Both DPAPI credential stores were left unchanged.'
}
Write-Host "Pilot task unregistered; stopped leftover worker tree(s): $stopped. No Trade Spine changes."
