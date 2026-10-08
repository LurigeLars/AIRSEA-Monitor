# Stop both the registered task and any leftover pilot worker trees.
$ErrorActionPreference = 'Stop'
$name = 'MarketObservationPilotShadow'
$app = Join-Path $env:LOCALAPPDATA 'MarketObservationPilot\App'
$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Disable-ScheduledTask -TaskName $name | Out-Null
}
. (Join-Path $PSScriptRoot 'pilot-processes.ps1')
$stopped = Stop-PilotOrphanedProcessTrees -AppDirectory $app
Write-Host "Shadow pilot stopped and disabled; stopped remaining worker tree(s): $stopped. Existing observations retained."
