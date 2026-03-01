param(
	[string]$AppName = "ai-trading-api",
	[string]$DockerImage = "ai-trading-api:latest",
	[string]$EnvFile = ".env.production",
	[int]$HostPort = 8000,
	[int]$ContainerPort = 8000,
	[int]$HealthCheckRetries = 20,
	[int]$HealthCheckDelaySeconds = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "Starting deployment for $AppName" -ForegroundColor Cyan

if (-not (Test-Path -Path $EnvFile)) {
	Write-Host "ERROR: Missing environment file: $EnvFile" -ForegroundColor Red
	exit 1
}

Write-Host "Environment file found: $EnvFile" -ForegroundColor Green

Write-Host "Validating required tools..." -ForegroundColor Yellow
foreach ($tool in @("docker")) {
	if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
		  Write-Host "ERROR: Required tool not found in PATH: $tool" -ForegroundColor Red
		exit 1
	}
}
Write-Host "Tooling validation passed" -ForegroundColor Green

Write-Host "Building Docker image: $DockerImage" -ForegroundColor Yellow
docker build -t $DockerImage .

$runningContainer = docker ps -q --filter "name=^$AppName$"
if ($runningContainer) {
	Write-Host "Stopping existing container: $AppName" -ForegroundColor Yellow
	docker stop $AppName | Out-Null
}

$existingContainer = docker ps -aq --filter "name=^$AppName$"
if ($existingContainer) {
	Write-Host "Removing existing container: $AppName" -ForegroundColor Yellow
	docker rm $AppName | Out-Null
}

Write-Host "Running Alembic migrations..." -ForegroundColor Yellow
docker run --rm --env-file $EnvFile $DockerImage alembic upgrade head
Write-Host "Migrations applied" -ForegroundColor Green

Write-Host "Starting new container: $AppName" -ForegroundColor Yellow
docker run -d `
	--name $AppName `
	--env-file $EnvFile `
	-p "${HostPort}:${ContainerPort}" `
	--restart unless-stopped `
	$DockerImage | Out-Null

Write-Host "Waiting for healthy service on http://localhost:$HostPort/health" -ForegroundColor Yellow
$isHealthy = $false
for ($attempt = 1; $attempt -le $HealthCheckRetries; $attempt++) {
	try {
		$response = Invoke-RestMethod -Method Get -Uri "http://localhost:$HostPort/health" -TimeoutSec 5
		if ($null -ne $response -and $response.status -eq "healthy") {
			$isHealthy = $true
			  Write-Host "Health check passed on attempt $attempt" -ForegroundColor Green
			break
		}
	}
	catch {
	}

	Start-Sleep -Seconds $HealthCheckDelaySeconds
}

if (-not $isHealthy) {
	Write-Host "ERROR: Health check failed after $HealthCheckRetries attempts" -ForegroundColor Red
	Write-Host "Recent container logs:" -ForegroundColor Yellow
	docker logs --tail 100 $AppName
	exit 1
}

Write-Host "Cleaning up dangling Docker images..." -ForegroundColor Yellow
docker image prune -f | Out-Null

Write-Host "Deployment complete for $AppName" -ForegroundColor Green
