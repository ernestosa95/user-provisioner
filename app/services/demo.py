"""Servicios simulados (datos ficticios) para desarrollar sin AD ni SQL."""
import uuid

from .base import DirectoryService, ServiceError, SuitestensaService, UserSpec


class DemoDirectory(DirectoryService):
    def __init__(self, password: str = "demo"):
        # Todos los usuarios del AD simulado usan la misma clave (config [app] demo_password).
        self.password = password
        self.users: dict[str, set[str]] = {"ebitadmin": {"Administrator"},
                                           "admin.demo": {"Administrator"}}
        self.groups = {"Requesting", "Technician", "Nurse", "Anaesthesiologist",
                       "Performing", "Surgeon", "Sonographer", "Reporter",
                       "Administrator", "Administrative"}

    def test_connection(self):
        return "AD simulado (modo demo)"

    def authenticate(self, username, password):
        return username.lower() in self.users and password == self.password

    def user_exists(self, username):
        return username.lower() in self.users

    def group_exists(self, group):
        return group in self.groups

    def create_user(self, spec, password):
        if self.user_exists(spec.username):
            raise ServiceError(f"El usuario {spec.username} ya existe en AD (demo)")
        self.users[spec.username.lower()] = set()

    def add_to_group(self, username, group):
        if not self.group_exists(group):
            raise ServiceError(f"El grupo AD '{group}' no existe (demo)")
        self.users[username.lower()].add(group)

    def delete_user(self, username):
        self.users.pop(username.lower(), None)


class DemoSuitestensa(SuitestensaService):
    fail_next = False  # para probar el rollback en tests

    def __init__(self):
        self.users: dict[str, dict] = {"admin.demo": {"guid": str(uuid.uuid4())}}

    def test_connection(self):
        return "Base simulada (modo demo)"

    def list_roles(self):
        return ["Administrative", "Administrator", "Anaesthesiologist", "Nurse",
                "Performing", "Reporter", "Requesting", "Sonographer",
                "Surgeon", "Technician"]

    def list_prefixes(self):
        return ["DR", "DRA", "PROF", "LIC", "ENF", "TEC"]

    def list_business_units(self):
        return ["RADIOLOGIA XYZ", "RADIO 1", "ECOGRAFIA", "TOMOGRAFIA"]

    def list_profiles(self):
        return ["DepartmentPhysician", "T_S_R_M", "I_P", "ReferringPhysician",
                "Administrator", "Administrative"]

    def user_exists(self, username):
        return username.lower() in self.users

    def create_user(self, spec, profile):
        if self.fail_next:
            self.fail_next = False
            raise ServiceError("Fallo simulado en SQL (demo)")
        guid = str(uuid.uuid4())
        self.users[spec.username.lower()] = {"guid": guid, "profile": profile,
                                             "spec": spec}
        return guid
