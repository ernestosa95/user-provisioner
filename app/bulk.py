"""Carga masiva desde la hoja `User` del Excel de configuracion (ConfigurationRIS.xlsx).

Flujo: leer el Excel -> vista previa (valida todo sin tocar AD ni SQL) -> procesar
las filas validas en segundo plano, cada una como un alta individual (con su rollback).
"""
import io
import secrets
import threading
import time
from dataclasses import dataclass, field, replace

from . import provisioning
from .services.base import ServiceError, UserSpec

MAX_ROWS = 500
SHEET = "User"
# Encabezados tecnicos (fila 2 de la hoja) -> campo. Se buscan sin distinguir mayusculas.
COLUMNS = {
    "idprefix": "prefix", "surname": "last_name", "name": "first_name", "idrole": "role",
    "enabledigitalsign": "digital_sign", "username": "username",
    "signatorycode": "signatory_code", "externalcode": "external_code",
    "businessunit": "bu1", "businessunit2": "bu2", "businessunit3": "bu3",
}
REQUIRED = ("username", "first_name", "last_name", "prefix", "role", "bu1")


@dataclass
class BulkRow:
    line: int                      # fila del Excel (para que L1 la encuentre)
    spec: UserSpec
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ad: str = ""                   # "nuevo" | "existe" (se usa el de AD)

    @property
    def ok(self) -> bool:
        return not self.errors


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # usuarios numericos (DNI) que Excel guarda como numero
    return str(v).strip()


def read_excel(data: bytes) -> list[BulkRow]:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ServiceError(f"No se pudo leer el Excel: {e}") from e
    try:
        if SHEET not in wb.sheetnames:
            raise ServiceError(f"El Excel no tiene la hoja '{SHEET}'")
        rows = list(wb[SHEET].iter_rows(values_only=True))
    finally:
        wb.close()

    # La fila de encabezados tecnicos es la que tiene la columna "UserName".
    head_idx = next((i for i, r in enumerate(rows[:10])
                     if any(_cell(c).lower() == "username" for c in r)), None)
    if head_idx is None:
        raise ServiceError(f"No se encontró la fila de encabezados (UserName, Name, Surname...) en '{SHEET}'")
    cols = {}
    for j, c in enumerate(rows[head_idx]):
        key = COLUMNS.get(_cell(c).lower())
        if key and key not in cols:
            cols[key] = j
    missing = [k for k in REQUIRED if k not in cols]
    if missing:
        raise ServiceError("Faltan columnas en la hoja User: " + ", ".join(missing))

    out = []
    for i, r in enumerate(rows[head_idx + 1:], start=head_idx + 2):
        get = lambda k: _cell(r[cols[k]]) if k in cols and cols[k] < len(r) else ""
        if not any(_cell(c) for c in r):
            continue
        spec = UserSpec(
            username=get("username").lower().rsplit("\\", 1)[-1].split("@", 1)[0],
            first_name=get("first_name"), last_name=get("last_name"),
            prefix=get("prefix"), role=get("role"),
            business_units=[b for b in dict.fromkeys(get(k) for k in ("bu1", "bu2", "bu3")) if b])
        row = BulkRow(i, spec)
        if get("digital_sign") == "1":
            row.warnings.append("Firma digital: no se configura (queda deshabilitada)")
        if get("signatory_code") or get("external_code"):
            row.warnings.append("TaxCode/SignatoryCode e Integration Code no se importan")
        out.append(row)
        if len(out) > MAX_ROWS:
            raise ServiceError(f"Máximo {MAX_ROWS} usuarios por archivo")
    if not out:
        raise ServiceError(f"La hoja '{SHEET}' no tiene usuarios")
    return out


class _CachedCatalogs:
    """Lee roles, prefijos y BU una sola vez para validar todas las filas."""

    def __init__(self, db):
        self._db, self._cache = db, {}

    def __getattr__(self, name):
        attr = getattr(self._db, name)
        if not name.startswith("list_"):
            return attr
        def cached():
            if name not in self._cache:
                self._cache[name] = attr()
            return self._cache[name]
        return cached


def preview(rows: list[BulkRow], ad, db, role_map: dict, *, use_existing: bool,
            ad_external: bool, has_password: bool) -> list[BulkRow]:
    """Valida cada fila sin modificar nada. Puede lanzar ServiceError (AD o SQL caidos)."""
    found = ad.find_users([r.spec.username for r in rows if r.spec.username])
    cat = _CachedCatalogs(db)
    seen: dict[str, int] = {}
    for r in rows:
        u = r.spec.username
        info = found.get(u)
        if info is not None and (ad_external or use_existing):
            r.ad = "existe"
            r.spec = replace(r.spec, existing_ad=True,
                             first_name=info.first_name.strip() or r.spec.first_name,
                             last_name=info.last_name.strip() or r.spec.last_name)
            if not info.enabled:
                r.warnings.append("Deshabilitado en AD")
        elif info is not None:
            r.errors.append("Ya existe en AD (marque 'usar los existentes' para darlo de alta solo en Suitestensa)")
        elif ad_external:
            r.errors.append("No existe en AD (AD ajeno: no se crean usuarios)")
        else:
            r.ad = "nuevo"
            if not has_password:
                r.errors.append("Falta configurar la clave genérica de AD")

        r.errors += provisioning.validate(r.spec, cat, role_map, need_names=r.ad != "existe")
        if u in seen:
            r.errors.append(f"Usuario repetido en el archivo (fila {seen[u]})")
        else:
            seen[u] = r.line
        if u and provisioning.USERNAME_RE.match(u) and db.user_exists(u):
            r.errors.append("Ya existe en Suitestensa")
    return rows


# --- ejecucion en segundo plano -------------------------------------------
@dataclass
class Job:
    id: str
    owner: str
    filename: str
    rows: list[BulkRow]
    results: list = field(default_factory=list)  # (BulkRow, ok, steps)
    finished: bool = False
    started: float = field(default_factory=time.time)

    @property
    def ok_count(self):
        return sum(1 for _, ok, _ in self.results if ok)


class BulkManager:
    """Vistas previas pendientes de confirmar y el trabajo en curso (uno a la vez)."""

    def __init__(self):
        self.previews: dict[str, dict] = {}
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def save_preview(self, owner, filename, rows) -> str:
        now = time.time()
        self.previews = {k: v for k, v in self.previews.items() if now - v["ts"] < 3600}
        token = secrets.token_urlsafe(16)
        self.previews[token] = {"owner": owner, "filename": filename, "rows": rows, "ts": now}
        return token

    def running(self) -> Job | None:
        return next((j for j in self.jobs.values() if not j.finished), None)

    def start(self, token, owner, run_one, on_done) -> Job:
        with self._lock:
            p = self.previews.get(token)
            if not p or p["owner"] != owner:
                raise ServiceError("La vista previa venció o no es suya: vuelva a subir el Excel")
            if self.running():
                raise ServiceError("Ya hay una carga masiva en curso; espere a que termine")
            del self.previews[token]
            job = Job(secrets.token_urlsafe(8), owner, p["filename"], [r for r in p["rows"] if r.ok])
            self.jobs[job.id] = job

        def work():
            try:
                for r in job.rows:
                    try:
                        ok, steps = run_one(job, r.spec)
                    except Exception as e:  # provision no lanza, pero por las dudas
                        ok, steps = False, [provisioning.Step("Error", False, repr(e))]
                    job.results.append((r, ok, steps))
            finally:
                job.finished = True
                on_done(job)

        threading.Thread(target=work, daemon=True, name=f"bulk-{job.id}").start()
        return job
