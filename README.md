# Alta de usuarios (AD + Suitestensa)

Aplicación web para que los administradores de un hospital creen usuarios en
Active Directory y los den de alta en Suitestensa con su rol, perfil y Business Units.
Reemplaza el flujo manual de `Create_AD_Groups`, `Import_Users_to_AD`,
`Import_users` y `configure_users`.

## Flujo del alta
1. Valida los datos (usuario, prefijo, rol, BU) contra la base del sitio.
2. Chequea que el usuario no exista en AD ni en Suitestensa y que el grupo AD exista.
3. Crea el usuario en AD (clave genérica del sitio, sin expiración, sin cambio obligatorio).
4. Lo agrega al grupo AD del rol.
5. Inserta en Suitestensa **en una sola transacción**: `ExtConfig.User`, `ExtRadio.tbUser`,
   `UserProfile_User` y `lsBusinessUnitUser`.
6. Si falla el paso 5, hace rollback de SQL y elimina el usuario de AD que acaba de crear.
7. Todo queda en la auditoría (sin contraseñas).

Roles, prefijos y BU se leen de la BD del sitio. El mapeo **rol → perfil → grupo AD**
se edita en la pantalla *Roles* (valores iniciales tomados de `configure_users.ps1`).

## Probar en modo demo (sin AD ni SQL)
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp config.example.toml config.toml                     # Windows: copy
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8080
```
Abrir http://127.0.0.1:8080 → ingresar como `ebitadmin` con la clave `demo_password` (por defecto `demo`).
Tests: `python -m pytest`.

## Acceso
- Todos los administradores se loguean con su **usuario y clave de AD** (la app no guarda claves).
- `ebitadmin` es el administrador principal, fijo: gestiona los roles y agrega/quita administradores.
- Los demás administradores tienen que existir en AD; `ebitadmin` los habilita desde *Administradores*.
- Tras 5 intentos fallidos en 5 minutos se bloquea el login (también evita que se bloquee la cuenta en AD).

## Instalar en un sitio (VM de parametrización)
Se instala como **servicio de Windows** con un instalador. Para compilarlo: `build.bat [version]`.
El resultado queda en `installer\dist\`. Ver la guía completa en [docs/instalacion-windows.md](docs/instalacion-windows.md).

En resumen:
1. Instalar el ODBC Driver 17/18 y RSAT ActiveDirectory en la VM, y ejecutar el instalador.
2. Al terminar se abre `https://localhost:<puerto>/` en la VM con la **configuración inicial** de AD y SQL.
   Esa pantalla solo existe mientras AD no esté configurado y solo responde a accesos locales.
3. Ingresar como `ebitadmin` (clave de dominio). Desde *Configuración* se editan AD y SQL
   y se prueba la conexión; los cambios aplican sin reiniciar.

### Claves y cifrado
- Las claves (clave genérica, cuenta de servicio, SQL) y la clave de sesión se guardan
  **cifradas** (Fernet) en `data/app.db`, y la interfaz nunca las vuelve a mostrar.
- La clave maestra está en `data/master.key`; en Windows queda protegida con DPAPI
  (alcance de máquina), así que una copia de `data/` no se puede descifrar en otro equipo.
- Si se pierde `master.key`, o se mueve `data/` a otra VM, hay que volver a cargar las claves.

### Recuperación
Si la configuración de AD queda mal y `ebitadmin` no puede ingresar, ejecutar en la VM
`provisioner-cli reset-ad` y volver a entrar a `https://localhost:<puerto>/` desde la propia VM.

## Estructura
- `app/provisioning.py` — orquestación y rollback.
- `app/services/` — `ad_powershell.py` (AD), `sql_suitestensa.py` (SQL), `demo.py` (simulados).
- `app/ps/ad.ps1` — operaciones AD; los parámetros viajan por variables de entorno.
- `app/store.py` — SQLite local: admins habilitados, mapeo de roles, configuración, auditoría.
- `app/site_config.py` / `app/crypto.py` — configuración de AD/SQL editable y cifrado de claves.
- `app/cli.py` — `serve` / `init` / `check` / `reset-ad`.
- `installer/` — `build.ps1` + Inno Setup + WinSW (instalador de Windows); se compila con `build.bat`.
- `docs/extraer_esquemas.sql` — consulta para validar el esquema real.

## Pendiente / siguiente fase
- Validar `sql_suitestensa.py` y `ad.ps1` contra un sitio de test (no probados con servidores reales).
- Carga masiva desde la hoja `User` del Excel.
- Firma digital (fuera de alcance: se inserta `EnableDigitalSign = 0`, sin código de firmante).
- Campos que el script original dejaba vacíos (`ActiveDirectoryObjectGUID`, `ObjectSID`, Email).
