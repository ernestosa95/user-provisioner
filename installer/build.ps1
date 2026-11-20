<#
.SYNOPSIS
  Arma el instalador de Windows de Alta de Usuarios (AD + Suitestensa).

.DESCRIPTION
  1. Descarga Python embebido (no requiere Python instalado en la VM de compilacion).
  2. Instala las dependencias de requirements.txt dentro de ese Python.
  3. Copia la aplicacion y descarga WinSW (envoltorio de servicio).
  4. Compila el instalador con Inno Setup 6 (ISCC.exe).

  Requisitos en la VM: PowerShell 5+, acceso a internet e Inno Setup 6 (https://jrsoftware.org/isdl.php).
  Resultado: installer\dist\UserProvisioner-Setup-<version>.exe

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File installer\build.ps1 -Version 1.0.0
#>
param(
    [string]$Version = "1.0.0",
    [string]$PythonVersion = "3.12.10",
    [string]$WinSWVersion = "2.12.0",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = Split-Path -Parent $PSScriptRoot
$Build = Join-Path $PSScriptRoot "build"
$Cache = Join-Path $PSScriptRoot "cache"
$Py = Join-Path $Build "python"
$App = Join-Path $Build "provisioner"

function Get-Cached([string]$Url, [string]$Name) {
    New-Item -ItemType Directory -Force $Cache | Out-Null
    $dest = Join-Path $Cache $Name
    if (-not (Test-Path $dest)) {
        Write-Host "Descargando $Url"
        Invoke-WebRequest -Uri $Url -OutFile $dest -UseBasicParsing
    }
    return $dest
}

Write-Host "== Limpiando $Build"
if (Test-Path $Build) { Remove-Item -Recurse -Force $Build }
New-Item -ItemType Directory -Force $Py, $App | Out-Null

Write-Host "== Python embebido $PythonVersion"
$zip = Get-Cached "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" "python-$PythonVersion-embed-amd64.zip"
Expand-Archive -Path $zip -DestinationPath $Py -Force
$short = ($PythonVersion.Split(".")[0..1] -join "")
# El archivo ._pth define sys.path: se agregan site-packages y la carpeta de la aplicacion.
Set-Content -Encoding ascii -Path (Join-Path $Py "python$short._pth") -Value @(
    "python$short.zip",
    ".",
    "Lib\site-packages",
    "..\provisioner",
    "import site"
)

Write-Host "== pip y dependencias"
$getpip = Get-Cached "https://bootstrap.pypa.io/get-pip.py" "get-pip.py"
& (Join-Path $Py "python.exe") $getpip --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip fallo" }
& (Join-Path $Py "python.exe") -m pip install --no-warn-script-location --only-binary=:all: -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install fallo" }

Write-Host "== Aplicacion"
Copy-Item -Recurse (Join-Path $Root "app") $App
New-Item -ItemType Directory -Force (Join-Path $Build "docs") | Out-Null
Copy-Item (Join-Path $Root "docs\*") (Join-Path $Build "docs") -Recurse
Copy-Item (Join-Path $Root "README.md") (Join-Path $Build "docs")
Get-ChildItem -Path $Build -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Set-Content -Encoding ascii -Path (Join-Path $App "VERSION") -Value $Version

Write-Host "== Precompilando"
& (Join-Path $Py "python.exe") -m compileall -q $App | Out-Null

Write-Host "== Verificando que la aplicacion importa"
Push-Location $App
& (Join-Path $Py "python.exe") -c "import app.main, app.cli, app.services.sql_suitestensa, uvicorn; print('ok')"
$ok = $LASTEXITCODE
Pop-Location
if ($ok -ne 0) { throw "La aplicacion no importa con el Python embebido" }

Write-Host "== WinSW $WinSWVersion"
$winsw = Get-Cached "https://github.com/winsw/winsw/releases/download/v$WinSWVersion/WinSW-x64.exe" "WinSW-$WinSWVersion-x64.exe"
Copy-Item $winsw (Join-Path $Build "UserProvisioner.exe")
Copy-Item (Join-Path $PSScriptRoot "UserProvisioner.xml") $Build
Copy-Item (Join-Path $PSScriptRoot "config.toml.template") $Build
Copy-Item (Join-Path $PSScriptRoot "provisioner-cli.cmd") $Build

Write-Host "== Inno Setup"
if (-not $Iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $Iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Iscc) { throw "No se encontro ISCC.exe. Instalar Inno Setup 6 (winget install JRSoftware.InnoSetup) o indicar -Iscc <ruta>." }
& $Iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "provisioner.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC fallo" }

Write-Host ""
Write-Host "Instalador generado en $(Join-Path $PSScriptRoot 'dist')" -ForegroundColor Green
