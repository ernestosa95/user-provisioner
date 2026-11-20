from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ServiceError(Exception):
    """Error controlado de AD o SQL con mensaje legible para L1."""


@dataclass
class UserSpec:
    username: str
    first_name: str
    last_name: str
    prefix: str                 # lsPrefix.Description (DR, PROF...)
    role: str                   # lsRole.Description
    business_units: list[str] = field(default_factory=list)  # hasta 3, por Description


class DirectoryService(ABC):
    """Active Directory."""

    @abstractmethod
    def test_connection(self) -> str:
        """Verifica servidor, credenciales y OU. Devuelve un resumen legible."""

    @abstractmethod
    def authenticate(self, username: str, password: str) -> bool:
        """Valida usuario/clave contra el dominio (login de la app)."""

    @abstractmethod
    def user_exists(self, username: str) -> bool: ...

    @abstractmethod
    def create_user(self, spec: UserSpec, password: str) -> None: ...

    @abstractmethod
    def delete_user(self, username: str) -> None: ...


class SuitestensaService(ABC):
    """Base de datos de Suitestensa / Extensa RIS."""

    @abstractmethod
    def test_connection(self) -> str:
        """Verifica conexion y bases. Devuelve un resumen legible."""

    @abstractmethod
    def list_roles(self) -> list[str]: ...

    @abstractmethod
    def list_prefixes(self) -> list[str]: ...

    @abstractmethod
    def list_business_units(self) -> list[str]: ...

    @abstractmethod
    def list_profiles(self) -> list[str]: ...

    @abstractmethod
    def user_exists(self, username: str) -> bool: ...

    @abstractmethod
    def create_user(self, spec: UserSpec, profile: str) -> str:
        """Inserta todo en UNA transaccion. Devuelve el GUID del usuario."""
