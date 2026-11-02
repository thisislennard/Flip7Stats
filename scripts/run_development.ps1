<#
  Startet Flip 7 Live aus dem Quellcode (Protokoll in der Konsole).
  Aufruf:  .\scripts\run_development.ps1                 # normale Daten unter %LOCALAPPDATA%\Flip7Live
           .\scripts\run_development.ps1 -DataDir .\dev  # eigener Datenordner (z. B. zum Ausprobieren)
#>
param(
    [string]$DataDir = "",
    [switch]$NoTray,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -r requirements-dev.txt
}
if ($DataDir) { $env:FLIP7LIVE_HOME = (Resolve-Path -LiteralPath (New-Item -ItemType Directory -Force $DataDir)).Path }
$launcherArgs = @("launcher.py", "--console")
if ($NoTray) { $launcherArgs += "--no-tray" }
if ($NoBrowser) { $launcherArgs += "--no-browser" }
& .\.venv\Scripts\python.exe @launcherArgs
