[CmdletBinding()]
param(
    [string]$BindHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501,
    [string]$LogFile = ".\\logs\\run_app.log",
    [int]$WaitSeconds = 5
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

Write-Host "[SMOKE] Restarting stack..."
& .\scripts\run_app.ps1 -Restart -BindHost $BindHost -ApiPort $ApiPort -UiPort $UiPort -LogFile $LogFile

Start-Sleep -Seconds $WaitSeconds

$apiUrl = "http://$BindHost`:$ApiPort/health"
$uiUrl = "http://$BindHost`:$UiPort/"

$apiStatus = "ERR"
$uiStatus = "ERR"

try {
    $apiResp = Invoke-WebRequest -Uri $apiUrl -UseBasicParsing -TimeoutSec 10
    $apiStatus = "$($apiResp.StatusCode)"
} catch {
    $apiStatus = "ERR"
}

try {
    $uiResp = Invoke-WebRequest -Uri $uiUrl -UseBasicParsing -TimeoutSec 10
    $uiStatus = "$($uiResp.StatusCode)"
} catch {
    $uiStatus = "ERR"
}

Write-Host "SMOKE_API_STATUS=$apiStatus"
Write-Host "SMOKE_UI_STATUS=$uiStatus"

if (($apiStatus -eq "200") -and ($uiStatus -eq "200")) {
    Write-Host "SMOKE_LOCAL_RESULT=OK"
    exit 0
}

Write-Error "SMOKE_LOCAL_RESULT=FAIL"
exit 1
