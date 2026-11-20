"""Servicios simulados (datos ficticios) para desarrollar sin AD ni SQL."""
import uuid

from .base import DirectoryService, ServiceError, SuitestensaService, UserSpec


class DemoDirectory(DirectoryService):
    def __init__(self, password: str = "demo"):
        # Todos los usuarios del AD simulado usan la misma clave (config [app] demo_password).
        self.password = password
        self.users: set[str] = {"ebitadmin", "admin.demo"}

    def test_connection(self):
        return "AD simulado (modo demo)"

    def authenticate(self, username, password):
        return username.lower() in self.users and password == self.password

    def user_exists(self, username):
        return username.lower() in self.users

    def create_user(self, spec, password):
        if self.user_exists(spec.username):
            raise ServiceError(f"El usuario {spec.username} ya existe en AD (demo)")
        self.users.add(spec.username.lower())

    def delete_user(self, username):
        self.users.discard(username.lower())


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
