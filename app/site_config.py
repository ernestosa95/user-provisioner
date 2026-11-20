"""Configuracion de conexion a AD y SQL, editable desde la interfaz (solo ebitadmin).

Se guarda en la base local (tabla `config`); las claves van SIEMPRE cifradas
(ver crypto.py) y nunca se devuelven a la interfaz.
"""
import json
import re
import secrets
from dataclasses import dataclass

from .crypto import Vault


@dataclass(frozen=True)
class Field:
    section: str
    key: str
    label: str
    kind: str = "text"  # text | secret | bool
    default: object = ""
    help: str = ""


FIELDS = [
    Field("ad", "server", "Controlador de dominio", help="ej. DCV01.suitestensa.local"),
    Field("ad", "domain", "Dominio", help="ej. suitestensa.local"),
    Field("ad", "ou", "OU donde se crean los usuarios", help="ej. OU=Usuarios,DC=suitestensa,DC=local"),
    Field("ad", "default_password", "Clave genérica de los usuarios nuevos", "secret"),
    Field("ad", "password_never_expires", "La clave de los usuarios nuevos no expira", "bool", True),
    Field("ad", "service_user", "Cuenta de servicio (opcional)",
          help="ej. SUITESTENSA\\svc_altas. Vacío = la cuenta que ejecuta la app."),
    Field("ad", "service_password", "Clave de la cuenta de servicio", "secret"),
    Field("sql", "driver", "Driver ODBC",
          help="Vacío = el ODBC Driver for SQL Server más nuevo instalado."),
    Field("sql", "server", "Servidor SQL", help="ej. SQLSRV01\\SUITE o 10.0.0.5,1433"),
    Field("sql", "username", "Usuario SQL", help="Vacío = autenticación de Windows."),
    Field("sql", "password", "Clave SQL", "secret"),
    Field("sql", "db_radio", "Base RIS (esquema ExtRadio)", default="ExtensaRadio"),
    Field("sql", "db_config", "Base de configuración (esquema ExtConfig)", default="SL_UserAndConfig"),
]
SECTIONS = {"ad", "sql"}
_BY_ID = {(f.section, f.key): f for f in FIELDS}
_DB_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


class SiteConfig:
    SECTIONS = SECTIONS

    def __init__(self, store, vault: Vault):
        self.store, self.vault = store, vault
        self._cache = self._load()

    def _load(self) -> dict:
        out = {}
        for sec, key, raw in self.store.config_rows():
            f = _BY_ID.get((sec, key))
            is_secret = (f and f.kind == "secret") or sec == "internal"
            out[(sec, key)] = self.vault.decrypt(raw) if is_secret else json.loads(raw)
        return out

    def get(self, section: str, key: str):
        """Valor guardado o el default del campo; None si el campo no existe."""
        if (section, key) in self._cache:
            return self._cache[(section, key)]
        f = _BY_ID.get((section, key))
        return f.default if f else None

    def has_secret(self, section: str, key: str) -> bool:
        return bool(self._cache.get((section, key)))

    def ad_configured(self) -> bool:
        return bool(self.get("ad", "server") or self.get("ad", "domain"))

    def session_key(self) -> str:
        """Clave de firma de las cookies: se genera una vez y se guarda cifrada."""
        k = self._cache.get(("internal", "session_key"))
        if not k:
            k = secrets.token_urlsafe(48)
            self.store.config_set([("internal", "session_key", self.vault.encrypt(k))])
            self._cache[("internal", "session_key")] = k
        return k

    def update(self, form: dict) -> tuple[list[str], list[str]]:
        """Aplica los valores del formulario. Devuelve (errores, claves modificadas).

        Para los campos `secret`: vacio = conservar el actual; `clear_<sec>_<key>` = borrarlo.
        """
        new, errors = {}, []
        for f in FIELDS:
            name = f"{f.section}_{f.key}"
            if f.kind == "bool":
                new[(f.section, f.key)] = form.get(name) == "1"
            elif f.kind == "secret":
                v = str(form.get(name, ""))
                if form.get(f"clear_{name}") == "1":
                    new[(f.section, f.key)] = ""
                elif v:
                    new[(f.section, f.key)] = v
            else:
                new[(f.section, f.key)] = str(form.get(name, "")).strip()

        for k in ("db_radio", "db_config"):
            if not _DB_NAME.match(new[("sql", k)]):
                errors.append(f"Nombre de base inválido: '{new[('sql', k)]}'.")
        for k in ("driver", "server", "username"):
            if any(c in new[("sql", k)] for c in ";{}"):
                errors.append(f"SQL {k}: no puede contener ';', '{{' ni '}}'.")
        if (new[("ad", "server")] or new[("ad", "domain")]) and not new[("ad", "domain")]:
            errors.append("Falta el dominio de AD.")
        if errors:
            return errors, []

        changed = [f"{s}.{k}" for (s, k), v in new.items() if self.get(s, k) != v]
        rows = []
        for (s, k), v in new.items():
            if _BY_ID[(s, k)].kind == "secret":
                rows.append((s, k, self.vault.encrypt(v)))
            else:
                rows.append((s, k, json.dumps(v)))
        self.store.config_set(rows)
        self._cache.update(new)
        return [], changed

    def reset(self, section: str):
        self.store.config_delete_section(section)
        self._cache = {k: v for k, v in self._cache.items() if k[0] != section}
