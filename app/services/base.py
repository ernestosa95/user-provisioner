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
    existing_ad: bool = False   # el usuario ya existe en AD: no se crea ni se borra


@dataclass
class AdUser:
    """Datos de un usuario que ya existe en AD (se copian a ExtConfig.User)."""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    object_guid: str = ""
    sid: str = ""
    enabled: bool = True


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
    def get_user(self, username: str) -> AdUser | None:
        """Datos del usuario en AD, o None si no existe."""

    def find_users(self, usernames: list[str]) -> dict[str, AdUser]:
        """Los que existen en AD, por usuario (carga masiva)."""
        return {u: a for u in dict.fromkeys(usernames) if (a := self.get_user(u)) is not None}

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
    def create_user(self, spec: UserSpec, profile: str, ad_user: AdUser | None = None) -> str:
        """Inserta todo en UNA transaccion. Devuelve el GUID del usuario.
        `ad_user`: datos de AD de un usuario existente (GUID, SID, mail)."""
