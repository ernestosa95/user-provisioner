"""AD via PowerShell (modulo ActiveDirectory/RSAT en la VM de parametrizacion).

Los parametros viajan por variables de entorno del proceso hijo: la clave no
aparece en la linea de comandos ni en logs.
"""
import json
import os
import subprocess
from pathlib import Path

from .base import AdUser, DirectoryService, ServiceError, UserSpec

SCRIPT = Path(__file__).resolve().parent.parent / "ps" / "ad.ps1"


def _ad_user(r) -> AdUser | None:
    if not r:
        return None
    return AdUser(first_name=r.get("first_name") or "", last_name=r.get("last_name") or "",
                  email=r.get("email") or "", object_guid=r.get("guid") or "",
                  sid=r.get("sid") or "", enabled=bool(r.get("enabled")))


class PowerShellDirectory(DirectoryService):
    def __init__(self, settings):
        self.s = settings

    def _run(self, payload: dict, password: str | None = None, timeout: int = 60):
        a = self.s.get
        payload |= {"server": a("ad", "server"), "ou": a("ad", "ou"),
                    "domain": a("ad", "domain")}
        env = os.environ.copy()
        env["PROV_PAYLOAD"] = json.dumps(payload)
        if password is not None:
            env["PROV_PASSWORD"] = password
        if a("ad", "service_user"):
            env["PROV_SVC_USER"] = a("ad", "service_user")
            env["PROV_SVC_PASSWORD"] = a("ad", "service_password", "")
        cmd = [a("app", "powershell", "powershell.exe"), "-NoProfile",
               "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)]
        try:
            p = subprocess.run(cmd, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ServiceError(f"No se pudo ejecutar PowerShell: {e}") from e
        out = (p.stdout or "").strip().splitlines()
        try:
            data = json.loads(out[-1])
        except (IndexError, ValueError):
            raise ServiceError(f"Respuesta inesperada de PowerShell: {p.stderr.strip() or p.stdout.strip()}")
        if not data.get("ok"):
            raise ServiceError(f"AD: {data.get('error')}")
        return data.get("result")

    def test_connection(self):
        if not (self.s.get("ad", "server") or self.s.get("ad", "domain")):
            raise ServiceError("AD no configurado")
        return str(self._run({"action": "ping"}))

    def authenticate(self, username, password):
        if not password:  # una clave vacia puede dar un bind anonimo "valido"
            return False
        return bool(self._run({"action": "auth", "username": username}, password=password))

    def user_exists(self, username):
        return bool(self._run({"action": "user_exists", "username": username}))

    def get_user(self, username):
        return _ad_user(self._run({"action": "get_user", "username": username}))

    def find_users(self, usernames):
        names = list(dict.fromkeys(u for u in usernames if u))
        if not names:
            return {}
        # Un solo PowerShell para todos (cada proceso tarda segundos en cargar el modulo).
        r = self._run({"action": "find_users", "usernames": names}, timeout=60 + len(names))
        return {u.lower(): _ad_user(v) for u, v in (r or {}).items()}

    def create_user(self, spec: UserSpec, password: str):
        self._run({"action": "create", "username": spec.username,
                   "first_name": spec.first_name, "last_name": spec.last_name,
                   "never_expires": bool(self.s.get("ad", "password_never_expires", True))},
                  password=password)

    def delete_user(self, username):
        self._run({"action": "delete", "username": username})
