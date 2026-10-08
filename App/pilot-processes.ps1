# Only identify pilot-owned workers by the exact installed runner script path.
# Never stop unrelated pwsh/python processes.
function Get-PilotRunnerProcesses {
    param([Parameter(Mandatory)][string]$AppDirectory)
    $runnerPath = Join-Path $AppDirectory 'run.ps1'
    $pattern = [regex]::Escape($runnerPath)
    @(
        Get-CimInstance Win32_Process -Filter "Name='pwsh.exe' OR Name='powershell.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern }
    )
}

function Stop-PilotOrphanedProcessTrees {
    param([Parameter(Mandatory)][string]$AppDirectory)

    $roots = @(Get-PilotRunnerProcesses -AppDirectory $AppDirectory)
    if ($roots.Count -eq 0) { return 0 }

    # Capture descendants before termination so orphan survivors can be detected.
    $all = @(Get-CimInstance Win32_Process)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    $frontier = @($roots | ForEach-Object { [int]$_.ProcessId })
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($id in $frontier) { [void]$ids.Add($id) }
        foreach ($p in $all) {
            if ($frontier -contains [int]$p.ParentProcessId -and -not $ids.Contains([int]$p.ProcessId)) {
                $next += [int]$p.ProcessId
            }
        }
        $frontier = @($next | Select-Object -Unique)
    }

    $taskkill = Join-Path $env:WINDIR 'System32\taskkill.exe'
    foreach ($root in $roots) {
        # taskkill /T restricts termination to this specific, verified pilot process tree.
        # A process can exit naturally after enumeration; the postcheck is authoritative.
        try {
            & $taskkill /PID ([string]$root.ProcessId) /T /F 2>$null | Out-Null
        } catch { }
    }
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 500
        $live = @(Get-CimInstance Win32_Process | Where-Object { $ids.Contains([int]$_.ProcessId) })
        if ($live.Count -eq 0) { return $roots.Count }
    }
    throw 'Pilot process descendants survived termination; refusing to restart and risk duplicate writers.'
}
