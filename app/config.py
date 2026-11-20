"""Configuracion de arranque (config.toml).

Solo datos de la instalacion, sin secretos. La conexion a AD y SQL (y sus
claves, cifradas) se configura desde la interfaz: ver site_config.py.
"""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Settings:
    def __init__(self, data: dict):
        self._d = data
        self._site = None  # SiteConfig: secciones ad y sql

    def attach(self, site):
        self._site = site

    def get(self, section: str, key: str, default=None):
        if self._site is not None and section in self._site.SECTIONS:
            v = self._site.get(section, key)
            return default if v is None else v
        return self._d.get(section, {}).get(key, default)

    @property
    def mode(self) -> str:
        return self.get("app", "mode", "demo")

    @property
    def data_dir(self) -> Path:
        p = Path(self.get("app", "data_dir", "data"))
        p = p if p.is_absolute() else ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def domain_short(self) -> str:
        """Primer label del dominio (como hace configure_users.ps1)."""
        return (self.get("ad", "domain") or "").split(".")[0]


def load_settings(path: str | None = None) -> Settings:
    path = path or os.environ.get("PROV_CONFIG") or str(ROOT / "config.toml")
    with open(path, "rb") as f:
        return Settings(tomllib.load(f))
