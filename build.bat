@echo off
rem ============================================================
rem  Compila el instalador de Alta de Usuarios (AD + Suitestensa).
rem
rem  Uso:  build.bat [version]          ej.  build.bat 1.0.1
rem        (doble clic = version 1.0.0)
rem
rem  Requisitos: Inno Setup 6 instalado y acceso a internet.
rem  Resultado:  installer\dist\UserProvisioner-Setup-<version>.exe
rem ============================================================
setlocal
set "VERSION=%~1"
if "%VERSION%"=="" set "VERSION=1.0.0"

cd /d "%~dp0"
echo Compilando Alta de Usuarios version %VERSION% ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\build.ps1" -Version "%VERSION%"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo *** ERROR: la compilacion fallo con codigo %RC%. Revisar los mensajes de arriba.
) else (
    echo Listo: installer\dist\UserProvisioner-Setup-%VERSION%.exe
    explorer "%~dp0installer\dist"
)

rem Si se ejecuto con doble clic, deja la ventana abierta.
echo %CMDCMDLINE% | find /i "/c" >nul && pause
exit /b %RC%
