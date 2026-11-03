<#
.SYNOPSIS
  One-command demo: start the local Docker stack and expose the dashboard on a
  temporary public HTTPS link via a Cloudflare quick tunnel.

.DESCRIPTION
  * Starts (or reuses) the docker compose stack in this repository.
  * Opens a Cloudflare "quick tunnel" (no account needed) to http://localhost:3000.
  * Prints the public https://....trycloudflare.com link and where the login is.
  * The link works only while this window stays open; press Ctrl+C to end it.

.PARAMETER Fresh
  Wipe stored alerts/feedback first (docker compose down -v) for a clean demo.

.PARAMETER NoTunnel
  Only start the stack, do not open a public link.

.EXAMPLE
  .\scripts\demo.ps1            # start stack + public link
  .\scripts\demo.ps1 -Fresh     # clean slate first
#>
[CmdletBinding()]
param(
    [switch]$Fresh,
    [switch]$NoTunnel
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Exe([string]$name, [string[]]$candidates) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    throw "$name not found. Install it with: winget install -e --id $(if ($name -eq 'docker') {'Docker.DockerDesktop'} else {'Cloudflare.cloudflared'})"
}

$docker = Find-Exe "docker" @(
    "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe",
    "$env:ProgramFiles\Docker\Docker\resources\bin\docker.exe"
)

# --- password -------------------------------------------------------------
$envFile = Join-Path $root ".env"
$pw = $null
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^DASHBOARD_PASSWORD=(.+)$' } | Select-Object -First 1
    if ($line) { $pw = ($line -split '=', 2)[1].Trim() }
}
if (-not $pw) {
    Write-Warning "DASHBOARD_PASSWORD is not set in .env - the public link would be open to anyone."
    Write-Warning "Add DASHBOARD_PASSWORD=<something> to .env and run again."
    exit 1
}
$user = "owner"
$uline = Get-Content $envFile | Where-Object { $_ -match '^DASHBOARD_USER=(.+)$' } | Select-Object -First 1
if ($uline) { $user = ($uline -split '=', 2)[1].Trim() }

# --- stack ------------------------------------------------------------------
if ($Fresh) {
    Write-Host "Resetting stored alerts (docker compose down -v)..." -ForegroundColor Yellow
    & $docker compose down -v | Out-Null
}
Write-Host "Starting the stack (docker compose up -d)..." -ForegroundColor Cyan
& $docker compose up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }

Write-Host "Waiting for the dashboard to answer..." -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:3000/healthz" -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { Start-Sleep -Seconds 2 }
}
if (-not $ok) { throw "dashboard did not become healthy on http://localhost:3000" }
Write-Host "Local dashboard: http://localhost:3000  (user '$user', password in .env)" -ForegroundColor Green

if ($NoTunnel) { exit 0 }

# --- tunnel -----------------------------------------------------------------
$cloudflared = Find-Exe "cloudflared" @(
    "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
    "$env:ProgramFiles\cloudflared\cloudflared.exe"
)
$log = Join-Path $env:TEMP "fraudstream-tunnel.log"
if (Test-Path $log) { Remove-Item $log -Force }
Write-Host "Opening a Cloudflare quick tunnel..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $cloudflared -ArgumentList @("tunnel", "--url", "http://localhost:3000", "--no-autoupdate") `
    -RedirectStandardError $log -NoNewWindow -PassThru

$url = $null
for ($i = 0; $i -lt 30 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    throw "tunnel did not report a URL; see $log"
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  PUBLIC LINK:  $url" -ForegroundColor Green
Write-Host "  Login user:   $user     (password: DASHBOARD_PASSWORD in .env)"
Write-Host "  The link works while this window is open. Press Ctrl+C to end."
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
try {
    Wait-Process -Id $proc.Id
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "Tunnel closed. The local stack keeps running (docker compose down to stop it)."
}
