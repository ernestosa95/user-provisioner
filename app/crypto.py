"""Cifrado de secretos guardados en la base local (Fernet: AES-128-CBC + HMAC).

La clave maestra vive en <data_dir>/master.key:
- En Windows el archivo esta protegido con DPAPI (alcance de maquina): copiar la
  carpeta data/ a otro equipo no permite descifrar los secretos.
- En otros sistemas (solo desarrollo) se guarda con permisos 600.
"""
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_DPAPI_HEADER = b"DPAPI:"


class CryptoError(Exception):
    pass


class Vault:
    def __init__(self, data_dir: Path):
        self._f = Fernet(_load_or_create_key(Path(data_dir) / "master.key"))

    def encrypt(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._f.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise CryptoError("No se pudo descifrar (master.key distinta o dato alterado)") from e


def _load_or_create_key(path: Path) -> bytes:
    if path.exists():
        raw = path.read_bytes()
        if raw.startswith(_DPAPI_HEADER):
            return _dpapi(raw[len(_DPAPI_HEADER):], protect=False)
        return raw.strip()
    key = Fernet.generate_key()
    if sys.platform == "win32":
        data = _DPAPI_HEADER + _dpapi(key, protect=True)
    else:
        data = key
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return key


def _dpapi(data: bytes, protect: bool) -> bytes:
    """CryptProtectData / CryptUnprotectData via ctypes (sin dependencias extra)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    CRYPTPROTECT_UI_FORBIDDEN, CRYPTPROTECT_LOCAL_MACHINE = 0x1, 0x4
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), None, None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE,
                                      ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise CryptoError(f"DPAPI: {ctypes.WinError()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
