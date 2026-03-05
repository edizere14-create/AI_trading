[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501
)

$ErrorActionPreference = "Stop"

function Stop-PortListener {
    param([int]$Port)

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) {
        Write-Host "No listener on port $Port"
        return
    }

    $processIds = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $processIds) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Stopped PID=$procId on port $Port"
        } catch {
            Write-Warning "Could not stop PID=$procId on port ${Port}: $($_.Exception.Message)"
        }
    }
}

Stop-PortListener -Port $ApiPort
Stop-PortListener -Port $UiPort