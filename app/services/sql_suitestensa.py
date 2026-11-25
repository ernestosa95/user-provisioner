"""Acceso real a Suitestensa (SQL Server). Todo parametrizado, sin NOLOCK en
las lecturas que deciden inserciones, y las escrituras en UNA transaccion.

Replica la logica de configure_users.ps1 para un unico usuario:
  1. ExtConfig.[User]            (config)
  2. ExtRadio.tbUser             (RIS, mismo GUID que config)
  3. ExtConfig.UserProfile_User  (perfil segun rol)
  4. ExtRadio.lsBusinessUnitUser (hasta 3 BU)
"""
import re
import uuid

import pyodbc

from .base import AdUser, ServiceError, SuitestensaService, UserSpec

ZERO_GUID = "00000000-0000-0000-0000-000000000000"


class SqlSuitestensa(SuitestensaService):
    """La configuracion se lee en cada uso: los cambios hechos desde la interfaz
    aplican sin reiniciar la app."""

    def __init__(self, settings):
        self.s = settings

    @property
    def radio(self):
        return f"[{self.s.get('sql', 'db_radio')}].[ExtRadio]"

    @property
    def cfg(self):
        return f"[{self.s.get('sql', 'db_config')}].[ExtConfig]"

    def _driver(self):
        configured = self.s.get("sql", "driver")
        installed = pyodbc.drivers()
        if configured in installed:
            return configured
        # Vacío, o un driver que ya no está (p. ej. el 17, default de versiones viejas):
        # se usa el más nuevo instalado.
        found = sorted((d for d in installed if re.fullmatch(r"ODBC Driver \d+ for SQL Server", d)),
                       key=lambda d: int(d.split()[2]))
        if not found:
            raise ServiceError("No hay ningún ODBC Driver for SQL Server instalado (17 o 18)")
        return found[-1]

    def _conn_str(self):
        g = self.s.get
        if not g("sql", "server"):
            raise ServiceError("SQL no configurado (ver Configuración)")
        parts = [f"DRIVER={{{self._driver()}}}", f"SERVER={g('sql', 'server')}",
                 "TrustServerCertificate=yes"]
        if g("sql", "username"):
            pwd = (g("sql", "password") or "").replace("}", "}}")
            parts += [f"UID={g('sql', 'username')}", f"PWD={{{pwd}}}"]
        else:
            parts.append("Trusted_Connection=yes")
        return ";".join(parts)

    def _connect(self, autocommit=True):
        conn_str = self._conn_str()
        try:
            return pyodbc.connect(conn_str, autocommit=autocommit, timeout=10)
        except pyodbc.Error as e:
            raise ServiceError(f"No se pudo conectar a SQL Server: {e}") from e

    def _col(self, sql, *args):
        try:
            with self._connect() as c:
                return [r[0] for r in c.cursor().execute(sql, *args).fetchall()]
        except pyodbc.Error as e:
            raise ServiceError(f"SQL: {e}") from e

    def test_connection(self):
        roles = self.list_roles()
        dom = self._col(f"SELECT name FROM {self.cfg}.Domain WHERE name=?", self.s.domain_short)
        d = f"dominio '{self.s.domain_short}' " + ("encontrado" if dom else "NO encontrado en ExtConfig.Domain")
        return f"Conectado: {len(roles)} roles en lsRole; {d}"

    # --- catalogos (se leen de la BD de cada sitio) ---------------------
    def list_roles(self):
        return self._col(f"SELECT Description FROM {self.radio}.lsRole ORDER BY Description")

    def list_prefixes(self):
        return self._col(f"SELECT Description FROM {self.radio}.lsPrefix ORDER BY Description")

    def list_business_units(self):
        return self._col(f"SELECT Description FROM {self.radio}.lsBusinessUnit ORDER BY Description")

    def list_profiles(self):
        return self._col(f"SELECT name FROM {self.cfg}.UserProfile ORDER BY name")

    def user_exists(self, username):
        try:
            with self._connect() as c:
                cur = c.cursor()
                a = cur.execute(f"SELECT 1 FROM {self.cfg}.[User] WHERE UserName=?", username).fetchone()
                b = cur.execute(f"SELECT 1 FROM {self.radio}.tbUser WHERE UserName=?", username).fetchone()
        except pyodbc.Error as e:
            raise ServiceError(f"SQL: {e}") from e
        return bool(a or b)

    # --- alta --------------------------------------------------------------
    def create_user(self, spec: UserSpec, profile: str, ad_user: AdUser | None = None) -> str:
        con = self._connect(autocommit=False)
        try:
            cur = con.cursor()

            def one(sql, *a):
                r = cur.execute(sql, *a).fetchone()
                return str(r[0]) if r else None

            prefix = one(f"SELECT TOP 1 guid FROM {self.radio}.lsPrefix WHERE Description=?", spec.prefix)
            role = one(f"SELECT TOP 1 guid FROM {self.radio}.lsRole WHERE Description=?", spec.role)
            domain = one(f"SELECT TOP 1 guid FROM {self.cfg}.Domain WHERE name=?", self.s.domain_short)
            prof = one(f"SELECT TOP 1 guid FROM {self.cfg}.UserProfile WHERE name=?", profile)
            if not prefix: raise ServiceError(f"Prefijo '{spec.prefix}' no existe en lsPrefix")
            if not role: raise ServiceError(f"Rol '{spec.role}' no existe en lsRole")
            if not domain: raise ServiceError(f"Dominio '{self.s.domain_short}' no existe en ExtConfig.Domain")
            if not prof: raise ServiceError(f"Perfil '{profile}' no existe en UserProfile")
            bus = []
            for bu in spec.business_units:
                g = one(f"SELECT TOP 1 guid FROM {self.radio}.lsBusinessUnit WHERE Description=?", bu)
                if not g: raise ServiceError(f"Business Unit '{bu}' no existe")
                bus.append(g)

            # UPDLOCK+HOLDLOCK: bloquea el rango hasta el commit (evita altas duplicadas en paralelo).
            if cur.execute(f"SELECT 1 FROM {self.cfg}.[User] WITH (UPDLOCK, HOLDLOCK) WHERE UserName=?",
                           spec.username).fetchone() \
               or cur.execute(f"SELECT 1 FROM {self.radio}.tbUser WITH (UPDLOCK, HOLDLOCK) WHERE UserName=?",
                              spec.username).fetchone():
                raise ServiceError(f"El usuario '{spec.username}' ya existe en Suitestensa")

            guid = str(uuid.uuid4())
            full = f"{spec.first_name} {spec.last_name}"
            a = ad_user or AdUser()  # usuario creado por la app: como el script original, vacios
            cur.execute(
                f"""INSERT INTO {self.cfg}.[User]
                (guid,UserName,Domain_GUID,name,surname,enabled,Description,descname,taxcode,
                 PrincipalName,ActiveDirectoryObjectGUID,BirthDate,Email,ObjectSID,Sex,Status,TelephoneNumbers)
                VALUES (?,?,?,?,?,1,?,?,'',?,?,'',?,?,'',0,'')""",
                guid, spec.username, domain, spec.first_name, spec.last_name,
                "Existing AD user, added from web app" if ad_user else "Created from web app",
                spec.username, full, a.object_guid, a.email, a.sid)
            cur.execute(
                f"""INSERT INTO {self.radio}.tbUser
                (guid,idprefix,idrole,EnableDigitalSign,isactive,IdDefaultFunctionality,UserName,SignatoryCode)
                VALUES (?,?,?,0,1,?,?,NULL)""",  # sin firma digital
                guid, prefix, role, ZERO_GUID, spec.username)
            cur.execute(f"INSERT INTO {self.cfg}.UserProfile_User (UserProfile_GUID,User_GUID) VALUES (?,?)",
                        prof, guid)
            for bu in dict.fromkeys(bus):  # sin duplicados
                cur.execute(f"INSERT INTO {self.radio}.lsBusinessUnitUser (guid,IdBusinessUnit,iduser) "
                            "VALUES (NEWID(),?,?)", bu, guid)
            con.commit()
            return guid
        except ServiceError:
            con.rollback()
            raise
        except pyodbc.Error as e:
            con.rollback()
            raise ServiceError(f"SQL: {e}") from e
        finally:
            con.close()
