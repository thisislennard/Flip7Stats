<#
  Baut dist\Flip7Live.exe.
  Schritte: Python prüfen → Abhängigkeiten installieren → Tests → Frontend-Dateien prüfen → PyInstaller →
  Ergebnis prüfen (EXE startet, antwortet, beendet sich) → Auslieferungsdateien in dist\.

  Aufruf (PowerShell):  .\scripts\build_windows.ps1            # mit Tests
                        .\scripts\build_windows.ps1 -SkipTests # nur bauen
#>
param(
    [switch]$SkipTests,
    [string]$Python = "",
    # Optional: Fingerabdruck eines Code-Signing-Zertifikats im Zertifikatspeicher. Ohne Signatur blockiert Windows
    # die EXE auf PCs mit aktivierter Smart App Control.
    [string]$CertificateThumbprint = $env:FLIP7_SIGN_THUMBPRINT,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Yellow }

# 1. Python-Umgebung
Step "Python-Umgebung prüfen"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if (-not $Python) { $Python = (Get-Command python -ErrorAction Stop).Source }
    & $Python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"
$ver = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.12") { throw "Python 3.12 oder neuer benötigt (gefunden: $ver)" }
Write-Host "Python $ver"

# 2. Abhängigkeiten
Step "Abhängigkeiten installieren"
& $py -m pip install --disable-pip-version-check -q -r requirements-dev.txt
if ($LASTEXITCODE) { throw "pip install fehlgeschlagen" }
# SQLAlchemy ohne kompilierte Beschleuniger: Smart App Control blockiert unsignierte DLLs in der EXE.
$sqla = (Select-String -Path requirements.txt -Pattern '^SQLAlchemy==').Line
$env:DISABLE_SQLALCHEMY_CEXT = "1"
& $py -m pip install --disable-pip-version-check -q --force-reinstall --no-deps --no-binary sqlalchemy $sqla
Remove-Item Env:DISABLE_SQLALCHEMY_CEXT
if ($LASTEXITCODE) { throw "SQLAlchemy (reines Python) konnte nicht installiert werden" }

# 3. Tests
if (-not $SkipTests) {
    Step "Tests ausführen"
    & $py -m pytest -q
    if ($LASTEXITCODE) { throw "Tests fehlgeschlagen – Build abgebrochen" }
}

# 4. Frontend-Abhängigkeiten (liegen lokal im Repository, kein CDN)
Step "Frontend-Dateien prüfen"
$required = @(
    "app\static\js\vendor\socket.io.min.js", "app\static\js\vendor\chart.umd.min.js",
    "app\static\fonts\SpaceGrotesk-Variable.woff2", "app\static\fonts\Manrope-Variable.woff2",
    "app\static\assets\flip7live.ico", "app\static\assets\favicon.svg", "app\desktop\webview2\WebView2Loader.dll"
)
foreach ($f in $required) { if (-not (Test-Path $f)) { throw "Fehlt: $f" } }
Write-Host "Alle Frontend-Dateien vorhanden"

# 5. PyInstaller
Step "PyInstaller"
& $py -m PyInstaller --noconfirm --clean build.spec
if ($LASTEXITCODE) { throw "PyInstaller fehlgeschlagen" }

$exe = Join-Path $root "dist\Flip7Live.exe"
if (-not (Test-Path $exe)) { throw "dist\Flip7Live.exe fehlt" }

# 5b. Optional signieren (signtool aus dem Windows SDK)
if ($CertificateThumbprint) {
    Step "EXE signieren"
    $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match "\\x64\\" } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { throw "signtool.exe nicht gefunden (Windows SDK installieren)" }
    & $signtool.FullName sign /sha1 $CertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 /d "Flip 7 Live" $exe
    if ($LASTEXITCODE) { throw "Signieren fehlgeschlagen" }
} else {
    Write-Host "Hinweis: EXE wird nicht signiert. Auf PCs mit Smart App Control blockiert Windows unsignierte Programme." -ForegroundColor DarkYellow
}

# 6. Ergebnis prüfen: EXE in einem Wegwerf-Datenordner starten, Antwort abwarten, sauber beenden
Step "Build-Ergebnis prüfen"
$testHome = Join-Path $env:TEMP ("flip7live-buildtest-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force (Join-Path $testHome "config") | Out-Null
'{"open_browser": false, "network_mode": "wlan_only", "port": 5099, "setup_completed": true}' |
    Set-Content -Encoding utf8 (Join-Path $testHome "config\settings.json")
$env:FLIP7LIVE_HOME = $testHome
$proc = Start-Process -FilePath $exe -ArgumentList "--no-window", "--portable" -PassThru
try {
    $info = $null
    for ($i = 0; $i -lt 60 -and -not $info; $i++) {
        Start-Sleep -Milliseconds 500
        $f = Join-Path $testHome "config\instance.json"
        if (Test-Path $f) { $info = Get-Content $f -Raw | ConvertFrom-Json }
    }
    if (-not $info) { throw "EXE hat nicht gestartet (siehe $testHome\logs\application.log)" }
    $ping = Invoke-RestMethod "http://127.0.0.1:$($info.port)/api/v1/system/ping"
    if (-not $ping.ok) { throw "EXE antwortet nicht korrekt" }
    Write-Host "EXE läuft und antwortet auf Port $($info.port)"
    # Sauber beenden über die Admin-Funktion
    $open = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($info.port)/internal/open" -ContentType "application/json" `
        -Headers @{ "X-Flip7-IPC" = $info.ipc_secret } -Body '{"next": "/"}'
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $page = Invoke-WebRequest -UseBasicParsing -Uri $open.url -WebSession $session
    $csrf = [regex]::Match($page.Content, 'name="csrf-token" content="([^"]+)"').Groups[1].Value
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($info.port)/api/v1/system/shutdown" -WebSession $session `
        -ContentType "application/json" -Headers @{ "X-CSRF-Token" = $csrf } -Body "{}" | Out-Null
    if (-not $proc.WaitForExit(15000)) { throw "EXE hat sich nicht beendet" }
    Write-Host "EXE sauber beendet (Code $($proc.ExitCode))"
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    Remove-Item Env:FLIP7LIVE_HOME
    Remove-Item -Recurse -Force $testHome -ErrorAction SilentlyContinue
}

# 7. Auslieferung
Step "Auslieferungsdateien"
Copy-Item README.md dist\README.md -Force
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash
"$hash  Flip7Live.exe" | Set-Content -Encoding ascii dist\SHA256SUMS.txt
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "`nFertig: dist\Flip7Live.exe ($size MB)" -ForegroundColor Green
Write-Host "SHA256: $hash"
