[CmdletBinding()]
param(
	[string]$BindHost = "127.0.0.1",
	[int]$Port = 8000,
	[switch]$Restart,
	[switch]$Reload,
	[string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
	$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
	$PythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }
}

$apiUrl = "http://$BindHost`:$Port"
function Stop-PortListeners {
	param([int]$PortToStop)

	$listeners = Get-NetTCPConnection -LocalPort $PortToStop -State Listen -ErrorAction SilentlyContinue
	if (-not $listeners) {
		return
	}

	$processIds = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
	foreach ($procId in $processIds) {
		try {
			Stop-Process -Id $procId -Force -ErrorAction Stop
			Write-Host "Stopped PID=$procId on port $PortToStop"
		} catch {
			Write-Warning "Could not stop PID=$procId on port ${PortToStop}: $($_.Exception.Message)"
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

if ($Restart) {
	Write-Host "Restart requested: stopping listeners on port $Port"
	Stop-PortListeners -PortToStop $Port
	Start-Sleep -Seconds 1
}

$isHealthy = Test-BackendHealthy -Url $apiUrl

if ($isHealthy) {
	Write-Host "Backend already running at $apiUrl"
	exit 0
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
	Write-Error "Port $Port is already in use by another process. Stop it first or choose a different port."
	exit 1
}

$args = @("-m", "uvicorn", "app.main:app", "--host", $BindHost, "--port", "$Port")
if ($Reload) { $args += "--reload" }

Write-Host "Starting backend on $apiUrl ..."
$backendProc = Start-Process -FilePath $PythonExe -ArgumentList $args -WorkingDirectory $repoRoot -PassThru

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

Write-Host "Backend is healthy at $apiUrl (PID=$($backendProc.Id))"
