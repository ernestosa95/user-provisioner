"""Base local SQLite: admins de la app, mapeo de roles y auditoria.

Los admins se autentican contra AD; aca solo se guarda quien tiene acceso.
El superusuario (ebitadmin) es fijo y no se guarda en esta tabla.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

# Valores iniciales tomados de configure_users.ps1 (editables desde la app).
DEFAULT_ROLE_MAP = {
    "Requesting": "DepartmentPhysician",
    "Anaesthesiologist": "DepartmentPhysician",
    "Surgeon": "DepartmentPhysician",
    "Sonographer": "DepartmentPhysician",
    "Technician": "T_S_R_M",
    "Nurse": "I_P",
    "Performing": "ReferringPhysician",
    "Reporter": "ReferringPhysician",
    "Administrator": "Administrator",
    "Administrative": "Administrative",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS admins(
  username TEXT PRIMARY KEY, added_by TEXT, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS role_map(
  role TEXT PRIMARY KEY, profile TEXT NOT NULL, ad_group TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config(
  section TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(section, key));
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, admin TEXT,
  action TEXT NOT NULL, target TEXT, ok INTEGER NOT NULL, detail TEXT);
"""


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self._c() as c:
            cols = [r["name"] for r in c.execute("PRAGMA table_info(admins)")]
            if "pw_hash" in cols:  # esquema viejo con claves locales: ya no se usa
                c.execute("DROP TABLE admins")
            c.executescript(SCHEMA)
            if c.execute("SELECT COUNT(*) FROM role_map").fetchone()[0] == 0:
                c.executemany(
                    "INSERT INTO role_map VALUES (?,?,?)",
                    [(r, p, r) for r, p in DEFAULT_ROLE_MAP.items()],
                )

    @contextmanager
    def _c(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # --- admins -----------------------------------------------------
    def is_admin(self, username: str) -> bool:
        with self._c() as c:
            return c.execute("SELECT 1 FROM admins WHERE username=?",
                             (username.lower(),)).fetchone() is not None

    def add_admin(self, username: str, added_by: str):
        with self._c() as c:
            c.execute("INSERT INTO admins VALUES (?,?,?)",
                      (username.lower(), added_by,
                       datetime.now(timezone.utc).isoformat(timespec="seconds")))

    def remove_admin(self, username: str):
        with self._c() as c:
            c.execute("DELETE FROM admins WHERE username=?", (username.lower(),))

    def list_admins(self):
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT * FROM admins ORDER BY username")]

    # --- mapeo de roles ----------------------------------------------
    def role_map(self) -> dict:
        with self._c() as c:
            return {r["role"]: {"profile": r["profile"], "ad_group": r["ad_group"]}
                    for r in c.execute("SELECT * FROM role_map ORDER BY role")}

    def set_role_map(self, role: str, profile: str, ad_group: str):
        with self._c() as c:
            c.execute("INSERT INTO role_map VALUES (?,?,?) ON CONFLICT(role) "
                      "DO UPDATE SET profile=excluded.profile, ad_group=excluded.ad_group",
                      (role, profile, ad_group))

    def delete_role_map(self, role: str):
        with self._c() as c:
            c.execute("DELETE FROM role_map WHERE role=?", (role,))

    # --- configuracion del sitio (ver site_config.py; secretos ya cifrados) ---
    def config_rows(self):
        with self._c() as c:
            return [tuple(r) for r in c.execute("SELECT section,key,value FROM config")]

    def config_set(self, rows):
        with self._c() as c:
            c.executemany("INSERT INTO config VALUES (?,?,?) ON CONFLICT(section,key) "
                          "DO UPDATE SET value=excluded.value", rows)

    def config_delete_section(self, section: str):
        with self._c() as c:
            c.execute("DELETE FROM config WHERE section=?", (section,))

    # --- auditoria ---------------------------------------------------
    def log(self, admin, action, target, ok: bool, detail=None):
        """Nunca registrar contrasenas en `detail`."""
        with self._c() as c:
            c.execute("INSERT INTO audit(ts,admin,action,target,ok,detail) VALUES (?,?,?,?,?,?)",
                      (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       admin, action, target, int(ok),
                       json.dumps(detail, ensure_ascii=False) if detail is not None else None))

    def audit(self, limit=200):
        with self._c() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]
