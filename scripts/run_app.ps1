[CmdletBinding()]
param(
    [string]$BindHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501,
    [string]$AppPath = "streamlit_app.py",
    [switch]$Restart,
    [switch]$ReloadBackend,
    [string]$LogFile = "",
    [switch]$AppendLog,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$logEnabled = -not [string]::IsNullOrWhiteSpace($LogFile)
$logPath = ""
$appendMode = [bool]$AppendLog
if ($logEnabled) {
    if ([System.IO.Path]::IsPathRooted($LogFile)) {
        $logPath = $LogFile
    } else {
        $logPath = Join-Path $repoRoot $LogFile
    }

    $logDir = Split-Path -Parent $logPath
    if ($logDir -and -not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    if ((Test-Path $logPath) -and (-not $appendMode)) {
        try {
            Remove-Item $logPath -Force -ErrorAction Stop
        } catch {
            $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
            $baseName = [System.IO.Path]::GetFileNameWithoutExtension($logPath)
            $extension = [System.IO.Path]::GetExtension($logPath)
            if ([string]::IsNullOrWhiteSpace($extension)) {
                $extension = ".log"
            }
            $fallbackLogPath = Join-Path $logDir ("{0}_{1}{2}" -f $baseName, $timestamp, $extension)
            Write-Warning "Log file is in use; switching to $fallbackLogPath"
            $logPath = $fallbackLogPath
            $appendMode = $false
        }
    }

    Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] run_app started"
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $PythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }
}

$apiUrl = "http://$BindHost`:$ApiPort"
$wsUrl = "ws://$BindHost`:$ApiPort/ws/price"

function Stop-PortListeners {
    param([int]$Port)
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) {
        return
    }

    $processIds = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $processIds) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Stopped PID=$procId on port $Port"
            if ($logEnabled) {
                Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] Stopped PID=$procId on port $Port"
            }
        } catch {
            Write-Warning "Could not stop PID=$procId on port ${Port}: $($_.Exception.Message)"
            if ($logEnabled) {
                Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] Could not stop PID=$procId on port ${Port}: $($_.Exception.Message)"
            }
        }
    }
}

function Test-BackendHealthy {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri "$Url/momentum/status" -UseBasicParsing -TimeoutSec 3
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Test-UiHealthy {
    param([string]$BindAddress, [int]$Port)
    try {
        $resp = Invoke-WebRequest -Uri ("http://{0}:{1}/" -f $BindAddress, $Port) -UseBasicParsing -TimeoutSec 3
        return $resp.StatusCode -eq 200
    } catch {
        return $false
    }
}

if ($Restart) {
    Write-Host "Restart requested: stopping listeners on ports $ApiPort and $UiPort"
    Stop-PortListeners -Port $ApiPort
    Stop-PortListeners -Port $UiPort
    Start-Sleep -Seconds 1
}

if (Test-BackendHealthy -Url $apiUrl) {
    Write-Host "Backend already running at $apiUrl"
} else {
    $listener = Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Error "Port $ApiPort is in use by another process and backend health check failed."
        exit 1
    }

    $backendArgs = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$ApiPort")
    if ($ReloadBackend) { $backendArgs += "--reload" }

    Write-Host "Starting backend on $apiUrl ..."
    if ($logEnabled) {
        Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] Starting backend on $apiUrl"
        $backendOutLog = "$logPath.backend.out.log"
        $backendErrLog = "$logPath.backend.err.log"
        if ((Test-Path $backendOutLog) -and (-not $appendMode)) { Remove-Item $backendOutLog -Force }
        if ((Test-Path $backendErrLog) -and (-not $appendMode)) { Remove-Item $backendErrLog -Force }
        $backendProc = Start-Process -FilePath $PythonExe -ArgumentList $backendArgs -WorkingDirectory $repoRoot -PassThru -RedirectStandardOutput $backendOutLog -RedirectStandardError $backendErrLog
    } else {
        $backendProc = Start-Process -FilePath $PythonExe -ArgumentList $backendArgs -WorkingDirectory $repoRoot -PassThru
    }

    $maxWaitSec = 20
    $healthy = $false
    for ($i = 0; $i -lt $maxWaitSec; $i++) {
        Start-Sleep -Seconds 1
        if (Test-BackendHealthy -Url $apiUrl) {
            $healthy = $true
            break
        }
        if ($backendProc.HasExited) {
            break
        }
    }

    if (-not $healthy) {
        if (-not $backendProc.HasExited) {
            Stop-Process -Id $backendProc.Id -Force
        }
        Write-Error "Backend failed to become healthy at $apiUrl within $maxWaitSec seconds."
        exit 1
    }
}

$env:API_BASE_URL = $apiUrl
$env:WS_URL = $wsUrl

if (Test-UiHealthy -BindAddress $BindHost -Port $UiPort) {
    Write-Host "Streamlit already running at http://$BindHost`:$UiPort"
    Write-Host "API_BASE_URL=$apiUrl"
    Write-Host "WS_URL=$wsUrl"
    if ($logEnabled) {
        Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] Streamlit already running at http://$BindHost`:$UiPort"
    }
    exit 0
}

$uiListener = Get-NetTCPConnection -LocalPort $UiPort -State Listen -ErrorAction SilentlyContinue
if ($uiListener) {
    Write-Error "Port $UiPort is in use by another process and UI health check failed."
    exit 1
}

Write-Host "Starting Streamlit on http://$BindHost`:$UiPort"
Write-Host "API_BASE_URL=$apiUrl"
Write-Host "WS_URL=$wsUrl"

$streamlitArgs = @("-m", "streamlit", "run", $AppPath, "--server.port", "$UiPort", "--server.address", $BindHost)
if ($logEnabled) {
    Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] Starting Streamlit on http://$BindHost`:$UiPort"
    $uiOutLog = "$logPath.ui.out.log"
    $uiErrLog = "$logPath.ui.err.log"
    if ((Test-Path $uiOutLog) -and (-not $appendMode)) { Remove-Item $uiOutLog -Force }
    if ((Test-Path $uiErrLog) -and (-not $appendMode)) { Remove-Item $uiErrLog -Force }
    $uiProc = Start-Process -FilePath $PythonExe -ArgumentList $streamlitArgs -WorkingDirectory $repoRoot -PassThru -RedirectStandardOutput $uiOutLog -RedirectStandardError $uiErrLog
} else {
    $uiProc = Start-Process -FilePath $PythonExe -ArgumentList $streamlitArgs -WorkingDirectory $repoRoot -PassThru
}

$uiMaxWaitSec = 20
$uiHealthy = $false
for ($i = 0; $i -lt $uiMaxWaitSec; $i++) {
    Start-Sleep -Seconds 1
    if (Test-UiHealthy -BindAddress $BindHost -Port $UiPort) {
        $uiHealthy = $true
        break
    }
    if ($uiProc.HasExited) {
        break
    }
}

if (-not $uiHealthy) {
    if (-not $uiProc.HasExited) {
        Stop-Process -Id $uiProc.Id -Force
    }
    Write-Error "Streamlit failed to become healthy at http://$BindHost`:$UiPort within $uiMaxWaitSec seconds."
    exit 1
}

Write-Host "Streamlit is healthy at http://$BindHost`:$UiPort"
if ($logEnabled) {
    Add-Content -Path $logPath -Value "[$(Get-Date -Format o)] Streamlit healthy at http://$BindHost`:$UiPort (PID=$($uiProc.Id))"
}
