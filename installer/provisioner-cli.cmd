@echo off
rem Comandos de operacion de Alta de Usuarios. Ejecutar como Administrador.
rem   provisioner-cli check      prueba la conexion a AD y SQL con la configuracion guardada
rem   provisioner-cli reset-ad   borra la configuracion de AD (vuelve la pantalla inicial, solo desde la VM)
rem   provisioner-cli init       crea base local, clave maestra y certificado si faltan
setlocal
set "PROV_CONFIG=%ProgramData%\UserProvisioner\config.toml"
set PYTHONUTF8=1
pushd "%~dp0provisioner"
"%~dp0python\python.exe" -m app.cli %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
