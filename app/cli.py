"""Comandos de operacion (en Windows: provisioner-cli.cmd <comando>).

    python -m app.cli serve      levanta la app (lo usa el servicio de Windows)
    python -m app.cli init       crea la base local, la clave maestra y un certificado
                                 autofirmado si faltan (lo usa el instalador)
    python -m app.cli check      prueba la conexion a AD y SQL con la configuracion guardada
    python -m app.cli reset-ad   borra la configuracion de AD; la pantalla inicial vuelve a
                                 estar disponible en https://localhost:<puerto>/ desde la VM
"""
import argparse
import ipaddress
import logging
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ROOT, load_settings


def _path(s, key, default):
    p = Path(s.get("server", key) or default)
    return p if p.is_absolute() else ROOT / p


def _tls(s):
    cert, key = _path(s, "tls_cert", "certs/provisioner.crt"), _path(s, "tls_key", "certs/provisioner.key")
    return cert, key


def _open_app(s):
    from .crypto import Vault
    from .site_config import SiteConfig
    from .store import Store

    store = Store(s.data_dir / "app.db")
    site = SiteConfig(store, Vault(s.data_dir))
    s.attach(site)
    return store, site


def serve() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = load_settings()
    if s.mode == "demo":
        logging.warning("mode = demo: AD y SQL simulados. NO usar en produccion.")
    cert, key = _tls(s)
    kwargs = {}
    if cert.exists() and key.exists():
        kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    else:
        logging.warning("Sin certificado TLS (%s): la app se sirve por HTTP.", cert)
    # proxy_headers=False: la pantalla inicial solo local depende de la IP real del cliente.
    uvicorn.run("app.main:create_app", factory=True, host=s.get("server", "host", "0.0.0.0"),
                port=int(s.get("server", "port", 8443)), proxy_headers=False, **kwargs)


def init() -> None:
    s = load_settings()
    _open_app(s)  # crea app.db, master.key y la clave de sesion
    print(f"Base local y clave maestra en {s.data_dir}")
    cert, key = _tls(s)
    if not cert.exists():
        _self_signed(cert, key)
        print(f"Certificado autofirmado creado en {cert} (reemplazar por uno de la CA interna)")


def check() -> int:
    s = load_settings()
    _, site = _open_app(s)
    print(f"Modo: {s.mode}")
    if s.mode == "demo":
        print("En modo demo no hay conexiones reales para probar.")
        return 0
    from .services.ad_powershell import PowerShellDirectory
    from .services.base import ServiceError
    from .services.sql_suitestensa import SqlSuitestensa

    rc = 0
    for name, svc in (("AD", PowerShellDirectory(s)), ("SQL", SqlSuitestensa(s))):
        try:
            print(f"[OK]    {name}: {svc.test_connection()}")
        except ServiceError as e:
            print(f"[ERROR] {name}: {e}")
            rc = 1
    return rc


def reset_ad() -> None:
    s = load_settings()
    store, site = _open_app(s)
    site.reset("ad")
    store.log("(consola)", "config_reset_ad", None, True)
    print("Configuracion de AD borrada. Entrar desde la propia VM a https://localhost:<puerto>/")


def _self_signed(cert_path: Path, key_path: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    host = socket.getfqdn()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(timezone.utc)
    alt = [x509.DNSName(h) for h in dict.fromkeys([host, socket.gethostname(), "localhost"])]
    alt.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="Alta de usuarios (AD + Suitestensa)")
    parser.add_argument("--config", type=Path, help="archivo config.toml (equivale a PROV_CONFIG)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("serve", "init", "check", "reset-ad"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    if args.config:
        os.environ["PROV_CONFIG"] = str(args.config)
    commands = {"serve": serve, "init": init, "check": check, "reset-ad": reset_ad}
    return commands[args.cmd]() or 0


if __name__ == "__main__":
    sys.exit(main())
