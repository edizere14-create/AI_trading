[CmdletBinding()]
param(
    [string]$BindHost = "127.0.0.1",
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501,
    [string]$LogFile = ".\\logs\\run_app.log",
    [int]$WaitSeconds = 5,
    [string]$Symbol,
    [double]$Amount = 1.0,
    [double]$PriceMultiplier = 0.5
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

Write-Host "[SMOKE-ALL] Running local stack smoke..."
& .\scripts\smoke_local.ps1 -BindHost $BindHost -ApiPort $ApiPort -UiPort $UiPort -LogFile $LogFile -WaitSeconds $WaitSeconds
if ($LASTEXITCODE -ne 0) {
    Write-Error "SMOKE_LOCAL_ALL_RESULT=FAIL (local smoke failed)"
    exit 1
}

Write-Host "[SMOKE-ALL] Running Kraken demo smoke..."
if (-not [string]::IsNullOrWhiteSpace($Symbol)) {
    & .\scripts\run_kraken_demo_smoke.ps1 -Symbol $Symbol -Amount $Amount -PriceMultiplier $PriceMultiplier
} else {
    & .\scripts\run_kraken_demo_smoke.ps1 -Amount $Amount -PriceMultiplier $PriceMultiplier
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "SMOKE_LOCAL_ALL_RESULT=FAIL (kraken smoke failed)"
    exit 1
}

Write-Host "SMOKE_LOCAL_ALL_RESULT=OK"
exit 0
