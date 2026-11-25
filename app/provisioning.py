"""Orquestacion del alta: validar -> AD -> SQL (con rollback de AD).

Si el usuario ya existe en AD (alta marcada asi, o un AD ajeno de solo lectura)
no se crea ni se borra nada en AD: se toman sus datos y solo se inserta en SQL.
"""
import re
from dataclasses import dataclass, replace

from .services.base import (DirectoryService, ServiceError, SuitestensaService,
                            UserSpec)

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,18}[a-z0-9]$")  # SamAccountName <= 20


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""


def validate(spec: UserSpec, db: SuitestensaService, role_map: dict,
             need_names: bool = True) -> list[str]:
    errs = []
    if not USERNAME_RE.match(spec.username):
        errs.append("Usuario invalido: 3-20 caracteres, minusculas, numeros, '.', '_' o '-'.")
    if need_names and (not spec.first_name.strip() or not spec.last_name.strip()):
        errs.append("Nombre y apellido son obligatorios.")
    if spec.prefix not in db.list_prefixes():
        errs.append("Prefijo invalido.")
    if spec.role not in db.list_roles():
        errs.append("Rol invalido.")
    elif spec.role not in role_map:
        errs.append(f"El rol '{spec.role}' no tiene perfil configurado (ver Roles).")
    valid_bu = set(db.list_business_units())
    if not spec.business_units:
        errs.append("Seleccione al menos una Business Unit.")
    if len(spec.business_units) > 3:
        errs.append("Maximo 3 Business Units.")
    if any(b not in valid_bu for b in spec.business_units):
        errs.append("Business Unit invalida.")
    return errs


def provision(spec: UserSpec, ad: DirectoryService, db: SuitestensaService,
              role_map: dict, password: str, rollback_ad: bool = True,
              ad_external: bool = False):
    """Devuelve (ok, steps). Nunca lanza: cualquier error queda en los pasos.

    `ad_external`: el AD no es nuestro (solo lectura); todos los usuarios tienen
    que existir en AD de antemano."""
    if ad_external:
        spec = replace(spec, existing_ad=True)
    if spec.existing_ad:
        return _provision_existing(spec, ad, db, role_map, ad_external)
    steps: list[Step] = []
    ad_attempted = ad_created = False
    try:
        errs = validate(spec, db, role_map)
        if not password:
            errs.append("Falta configurar la clave genérica de AD (ver Configuración).")
        if errs:
            return False, [Step("Validacion", False, " | ".join(errs))]
        steps.append(Step("Validacion", True))
        m = role_map[spec.role]

        if ad.user_exists(spec.username):
            raise ServiceError(f"'{spec.username}' ya existe en AD. Si es la misma persona, marque "
                               "'Ya existe en AD' para darlo de alta solo en Suitestensa")
        if db.user_exists(spec.username):
            raise ServiceError(f"'{spec.username}' ya existe en Suitestensa")
        steps.append(Step("Chequeo de duplicados", True))

        ad_attempted = True
        ad.create_user(spec, password)
        ad_created = True
        steps.append(Step("Usuario creado en AD", True))

        guid = db.create_user(spec, m["profile"])
        steps.append(Step(f"Insertado en Suitestensa (perfil {m['profile']})", True, guid))
        return True, steps
    except Exception as e:
        steps.append(_error_step(e))
        if ad_attempted and not ad_created:
            # New-ADUser puede crear el objeto y fallar despues (p. ej. la clave no
            # cumple la politica, o timeout): verificar si quedo creado.
            try:
                ad_created = ad.user_exists(spec.username)
            except Exception as e2:
                steps.append(Step("No se pudo verificar si el usuario quedo en AD: revisar a mano",
                                  False, str(e2)))
        if ad_created:
            if rollback_ad:
                try:
                    ad.delete_user(spec.username)
                    steps.append(Step("Rollback: usuario eliminado de AD", True))
                except Exception as e2:
                    steps.append(Step("Rollback AD FALLO: eliminar el usuario a mano", False, str(e2)))
            else:
                steps.append(Step("ATENCION: el usuario quedo creado en AD", False))
        return False, steps


def _provision_existing(spec, ad, db, role_map, ad_external):
    """Usuario que ya esta en AD: solo se lee de AD (nunca se crea ni se borra)."""
    steps: list[Step] = []
    try:
        errs = validate(spec, db, role_map, need_names=False)
        if errs:
            return False, [Step("Validacion", False, " | ".join(errs))]
        steps.append(Step("Validacion", True))
        m = role_map[spec.role]

        info = ad.get_user(spec.username)
        if info is None:
            raise ServiceError(f"'{spec.username}' no existe en AD" + (
                "" if ad_external else ". Desmarque 'Ya existe en AD' para crearlo"))
        # El nombre de AD manda; el del formulario solo completa lo que falte en AD.
        spec = replace(spec, first_name=info.first_name.strip() or spec.first_name,
                       last_name=info.last_name.strip() or spec.last_name)
        if not spec.first_name or not spec.last_name:
            raise ServiceError("El usuario no tiene nombre o apellido en AD: complételos en el formulario")
        detail = f"{spec.first_name} {spec.last_name}" + ("" if info.enabled else " (DESHABILITADO en AD)")
        steps.append(Step("Usuario encontrado en AD (no se modifica)", True, detail))

        if db.user_exists(spec.username):
            raise ServiceError(f"'{spec.username}' ya existe en Suitestensa")
        steps.append(Step("Chequeo de duplicados", True))

        guid = db.create_user(spec, m["profile"], ad_user=info)
        steps.append(Step(f"Insertado en Suitestensa (perfil {m['profile']})", True, guid))
        return True, steps
    except Exception as e:
        steps.append(_error_step(e))
        return False, steps


def _error_step(e: Exception) -> Step:
    msg = str(e) if isinstance(e, ServiceError) else f"Error inesperado: {e!r}"
    return Step("Error", False, msg)
