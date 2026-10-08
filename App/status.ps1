$ErrorActionPreference = 'Stop'
$name = 'MarketObservationPilotShadow'
$app = Join-Path $env:LOCALAPPDATA 'MarketObservationPilot\App'
$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $name
    Write-Host "Task: $($task.State)"
    Write-Host "LastTaskResult: $($info.LastTaskResult)"
    Write-Host "LastRunTime: $($info.LastRunTime)"
} else { Write-Host 'Task: not installed' }
. (Join-Path $PSScriptRoot 'pilot-processes.ps1')
$roots = @(Get-PilotRunnerProcesses -AppDirectory $app)
Write-Host "Verified pilot runners: $($roots.Count)"
if ($roots.Count -gt 1 -or ($roots.Count -gt 0 -and $task -and $task.State -ne 'Running')) {
    Write-Warning 'Orphaned/duplicate pilot runner(s) detected. Do not start another copy.'
}
$db = Join-Path $env:LOCALAPPDATA 'MarketObservationPilot\Data\pilot.sqlite'
if (Test-Path -LiteralPath $db) {
    & uv run --python 3.12 python (Join-Path $PSScriptRoot 'report.py') --db $db
} else { Write-Host 'No database yet.' }
foreach ($file in @('runner.log','collector.log')) {
    $path = Join-Path $env:LOCALAPPDATA "MarketObservationPilot\Data\$file"
    if (Test-Path -LiteralPath $path) {
        Write-Host "Recent sanitized $file lines:"
        Get-Content -LiteralPath $path -Tail 12
    }
}
