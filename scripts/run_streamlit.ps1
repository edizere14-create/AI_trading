[CmdletBinding()]
param(
	[string]$AppPath = "streamlit_app.py",
	[int]$Port = 8501,
	[string]$ApiUrl = "http://127.0.0.1:8000",
	[string]$WsUrl = "ws://127.0.0.1:8000/ws/price",
	[string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
	$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
	$PythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }
}

$env:API_BASE_URL = $ApiUrl
$env:WS_URL = $WsUrl

Write-Host "Starting Streamlit on http://127.0.0.1:$Port"
Write-Host "API_BASE_URL=$ApiUrl"
Write-Host "WS_URL=$WsUrl"

& $PythonExe -m streamlit run $AppPath --server.port $Port
