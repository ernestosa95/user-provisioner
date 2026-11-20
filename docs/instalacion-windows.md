# Instalación en Windows

La aplicación se instala como **servicio de Windows** ("Alta de Usuarios (AD + Suitestensa)"), con Python embebido.
No necesita Python instalado en el servidor.

## 1. Compilar el instalador (VM de compilación)

Requisitos: Windows 10/11 o Server 2016+, PowerShell 5+, acceso a internet e [Inno Setup 6](https://jrsoftware.org/isdl.php)
(`winget install JRSoftware.InnoSetup`).

Hacer doble clic en **`build.bat`** (en la raíz del proyecto), o desde una consola:

```bat
build.bat 1.0.0
```

`build.bat` llama a `installer\build.ps1`. Ese script:
1. Descarga Python 3.12 embebido.
2. Instala las dependencias de `requirements.txt`.
3. Verifica que la app importe.
4. Descarga WinSW y compila con Inno Setup.

Resultado: `installer\dist\UserProvisioner-Setup-1.0.0.exe`.

## 2. Instalar (VM de parametrización del sitio)

**Requisitos del servidor** (el instalador avisa si faltan, pero no los instala):
- ODBC Driver 17 o 18 for SQL Server.
- El módulo ActiveDirectory de PowerShell (RSAT):
  - Windows 10/11: `Add-WindowsCapability -Online -Name Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0`
  - Windows Server: `Install-WindowsFeature RSAT-AD-PowerShell`

Ejecutar el instalador como administrador. El asistente pide:

| Página | Datos |
|---|---|
| Tareas | Abrir el puerto en el firewall. **Modo demostración**: AD y SQL simulados, solo para pruebas |
| Sitio | Nombre del sitio y puerto HTTPS (8444) |

Los datos de AD y SQL **no se cargan en el instalador**: se cargan en la propia aplicación (ver paso 3).

Qué hace el instalador:
- Instala el programa en `C:\Program Files\UserProvisioner`: Python, la aplicación, el servicio y `provisioner-cli.cmd`.
- Crea `C:\ProgramData\UserProvisioner` con:
  - `config.toml`: la configuración de la instalación (modo, puerto, certificado). No lleva secretos.
  - `data\`: la base local con admins, roles, auditoría y la configuración de AD/SQL con las claves cifradas. También guarda `master.key`, protegida con DPAPI.
  - `logs\`: los logs del servicio.
  - `certs\`: el certificado HTTPS. Si no existe, se genera uno autofirmado.
- Restringe esa carpeta a Administradores y SYSTEM.
- Abre el puerto en el firewall (si se eligió) y registra e inicia el servicio, que arranca automáticamente.

**Actualizar:** ejecutar el instalador de la versión nueva. Conserva `C:\ProgramData\UserProvisioner`.
**Desinstalar:** desde *Aplicaciones*. También conserva `C:\ProgramData\UserProvisioner`.

## 3. Configuración inicial (en la propia VM)

1. Al terminar, el instalador abre `https://localhost:8444/`. Como AD todavía no está configurado, se muestra la
   **configuración inicial**. Esta pantalla solo responde a accesos desde la propia VM.
2. Cargar los datos de AD (dominio, servidor, OU, clave genérica y, si hace falta, la cuenta de servicio) y de SQL.
3. Ingresar como `ebitadmin` con su clave de dominio. En *Configuración*, usar **Probar AD** y **Probar SQL**.
4. En *Roles*, revisar el perfil y el grupo AD de cada rol. En *Administradores*, habilitar a los demás admins.

### Con qué cuenta se conecta a AD y SQL
El servicio corre como **SYSTEM**. Con esa cuenta:
- **AD:** validar el login y leer funcionan con la cuenta de equipo. Para **crear usuarios** hay que cargar una
  **cuenta de servicio** en *Configuración* con delegación sobre la OU y los grupos de rol.
  Otra opción es cambiar la cuenta con la que corre el servicio en `services.msc`, y darle control total sobre `C:\ProgramData\UserProvisioner`.
- **SQL:** con autenticación de Windows se conecta como la cuenta de equipo (`DOMINIO\VM$`). Hay dos opciones:
  - Usar un usuario SQL, que se carga en *Configuración*.
  - Crear el login de la cuenta de equipo en SQL Server, con lectura e INSERT en las tablas del alta.

### Certificado
Reemplazar `certs\provisioner.crt` y `provisioner.key` (PEM) por un certificado de la CA interna y reiniciar el servicio.

## Operación

Desde una consola de administrador, con `"C:\Program Files\UserProvisioner\provisioner-cli.cmd" <comando>`:

| Comando | Para qué |
|---|---|
| `check` | Prueba la conexión a AD y SQL con la configuración guardada |
| `reset-ad` | Borra la configuración de AD (si quedó mal y nadie puede ingresar); vuelve la configuración inicial, solo desde la VM |
| `init` | Crea la base local, la clave maestra y el certificado si faltan |

Para cambiar `config.toml` (puerto, modo, certificado): editarlo (hay un acceso en el menú Inicio) y reiniciar el servicio
(`Restart-Service UserProvisioner`). Los cambios de AD y SQL hechos en *Configuración* aplican sin reiniciar.

**Copias de seguridad:** respaldar `C:\ProgramData\UserProvisioner\data`. Las claves solo se pueden descifrar
en la misma VM, porque `master.key` está protegida con DPAPI. Si se restaura en otra VM, hay que volver a cargar las claves en *Configuración*.

## Diagnóstico

| Síntoma | Revisar |
|---|---|
| El servicio no arranca | `C:\ProgramData\UserProvisioner\logs\UserProvisioner.err.log` |
| "AD no está configurado" al entrar desde otra PC | Hacer la configuración inicial desde la propia VM (`https://localhost:<puerto>/`) |
| Nadie puede ingresar | `provisioner-cli check`; si AD quedó mal, `provisioner-cli reset-ad` |
| Falla "Crear usuario en AD" por permisos | Cuenta de servicio de AD en *Configuración* (delegación sobre la OU y los grupos) |
| "No se pudo conectar a SQL Server" | Driver ODBC instalado, servidor\instancia, firewall al 1433, login de la cuenta |
