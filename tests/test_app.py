import re

import pytest
from fastapi.testclient import TestClient


from app import main, provisioning
from app.config import Settings
from app.main import create_app
from app.services.base import ServiceError, UserSpec
from app.services.demo import DemoDirectory, DemoSuitestensa


def settings(tmp_path, **app):
    return Settings({"app": {"mode": "demo", "data_dir": str(tmp_path),
                             "rollback_ad_on_sql_failure": True, "demo_password": "demo"} | app})


@pytest.fixture
def client(tmp_path):
    app = create_app(settings(tmp_path))
    c = TestClient(app, follow_redirects=True)
    c.app_ = app
    return c


def csrf(c, path):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


def login(c, user="ebitadmin", pw="demo"):
    return c.post("/login", data={"csrf": csrf(c, "/login"), "username": user, "password": pw})


def logout(c):
    c.post("/logout", data={"csrf": csrf(c, "/")})


FORM = {"username": "ana.lopez", "first_name": "Ana", "last_name": "Lopez", "prefix": "DR",
        "role": "Technician", "bu": ["RADIO 1"]}


def test_requires_login(client):
    assert client.get("/").url.path == "/login"
    assert client.get("/setup").status_code == 404


def test_session_key_persisted_encrypted(tmp_path):
    a = create_app(settings(tmp_path))
    key = a.state.site.session_key()
    assert create_app(settings(tmp_path)).state.site.session_key() == key  # sobrevive reinicios
    assert key.encode() not in (tmp_path / "app.db").read_bytes()


def test_superuser_login_ad(client):
    assert "incorrectos" in login(client, pw="mala").text
    r = login(client, user="DEMO\\EbitAdmin")
    assert r.url.path == "/" and "Administradores" in r.text


def test_ad_user_not_admin_cannot_login(client):
    # existe en AD (demo) pero ebitadmin no lo habilito
    assert "incorrectos" in login(client, user="admin.demo").text


def test_admin_add_and_remove(client):
    login(client)
    r = client.post("/admins", data={"csrf": csrf(client, "/admins"), "username": "no.existe"})
    assert "no existe en AD" in r.text
    client.post("/admins", data={"csrf": csrf(client, "/admins"), "username": "admin.demo"})
    logout(client)

    r = login(client, user="admin.demo")
    assert r.url.path == "/" and "Administradores" not in r.text
    assert client.get("/admins").status_code == 403
    logout(client)

    login(client)
    client.post("/admins/remove", data={"csrf": csrf(client, "/admins"), "username": "admin.demo"})
    logout(client)
    assert "incorrectos" in login(client, user="admin.demo").text


def test_removed_admin_loses_session(client):
    login(client)
    client.post("/admins", data={"csrf": csrf(client, "/admins"), "username": "admin.demo"})
    other = TestClient(client.app_, follow_redirects=True)
    login(other, user="admin.demo")
    assert other.get("/").url.path == "/"
    client.post("/admins/remove", data={"csrf": csrf(client, "/admins"), "username": "admin.demo"})
    assert other.get("/").url.path == "/login"


def test_create_ok(client):
    login(client)
    r = client.post("/users", data={**FORM, "csrf": csrf(client, "/")})
    assert "Usuario creado: ana.lopez" in r.text
    assert "Technician" in client.app_.state.ad.users["ana.lopez"]
    assert client.app_.state.db.users["ana.lopez"]["profile"] == "T_S_R_M"


def test_duplicate_rejected(client):
    login(client)
    t = csrf(client, "/")
    client.post("/users", data={**FORM, "csrf": t})
    r = client.post("/users", data={**FORM, "csrf": t})
    assert "ya existe" in r.text


def test_sql_failure_rolls_back_ad(client):
    login(client)
    client.app_.state.db.fail_next = True
    r = client.post("/users", data={**FORM, "csrf": csrf(client, "/")})
    assert "Rollback" in r.text
    assert "ana.lopez" not in client.app_.state.ad.users


def test_validation(client):
    login(client)
    r = client.post("/users", data={**FORM, "username": "A B", "csrf": csrf(client, "/")})
    assert "Usuario invalido" in r.text
    assert "ana.lopez" not in client.app_.state.ad.users


def test_csrf_enforced(client):
    login(client)
    assert client.post("/users", data=FORM).status_code == 400


def test_audit_has_no_password(client):
    login(client)
    client.post("/users", data={**FORM, "csrf": csrf(client, "/")})
    text = client.get("/audit").text
    assert "Demo.Pass.01" not in text and ">demo<" not in text


# --- orquestacion: errores parciales -------------------------------------
SPEC = UserSpec(username="ana.lopez", first_name="Ana", last_name="Lopez", prefix="DR",
                role="Technician", business_units=["RADIO 1"])
ROLES = {"Technician": {"profile": "T_S_R_M", "ad_group": "Technician"}}


def test_partial_ad_create_is_rolled_back():
    """New-ADUser crea el objeto y falla al poner la clave: no debe quedar huerfano."""
    class HalfCreate(DemoDirectory):
        def create_user(self, spec, password):
            super().create_user(spec, password)
            raise ServiceError("La clave no cumple la politica")

    ad = HalfCreate()
    ok, steps = provisioning.provision(SPEC, ad, DemoSuitestensa(), ROLES, "x")
    assert not ok and "ana.lopez" not in ad.users
    assert any("Rollback" in s.name for s in steps)


def test_unexpected_error_is_rolled_back():
    class Broken(DemoSuitestensa):
        def create_user(self, spec, profile):
            raise KeyError("boom")

    ad = DemoDirectory()
    ok, steps = provisioning.provision(SPEC, ad, Broken(), ROLES, "x")
    assert not ok and "ana.lopez" not in ad.users


def test_db_down_during_validation_does_not_crash():
    class Down(DemoSuitestensa):
        def list_prefixes(self):
            raise ServiceError("SQL caido")

    ok, steps = provisioning.provision(SPEC, DemoDirectory(), Down(), ROLES, "x")
    assert not ok and "SQL caido" in steps[-1].detail


# --- configuracion de AD/SQL desde la interfaz ---------------------------
CFG = {"ad_server": "dc01.hosp.local", "ad_domain": "hosp.local", "ad_ou": "OU=U,DC=hosp,DC=local",
       "ad_default_password": "Generica.2026!", "ad_password_never_expires": "1",
       "ad_service_user": "", "ad_service_password": "",
       "sql_driver": "ODBC Driver 18 for SQL Server", "sql_server": "SQL01\\SUITE",
       "sql_username": "prov", "sql_password": "Sql.Secreta.99",
       "sql_db_radio": "ExtensaRadio", "sql_db_config": "SL_UserAndConfig"}


def save_cfg(c, **over):
    return c.post("/config", data={**CFG, **over, "csrf": csrf(c, "/config")})


def test_config_only_superuser(client):
    login(client)
    client.post("/admins", data={"csrf": csrf(client, "/admins"), "username": "admin.demo"})
    logout(client)
    login(client, user="admin.demo")
    assert client.get("/config").status_code == 403
    assert client.post("/config", data={**CFG, "csrf": csrf(client, "/")}).status_code == 403


def test_config_secrets_encrypted_and_never_shown(client, tmp_path):
    login(client)
    r = save_cfg(client)
    assert "Configuración guardada" in r.text
    assert "Generica.2026!" not in r.text and "Sql.Secreta.99" not in r.text
    raw = (tmp_path / "app.db").read_bytes()
    assert b"Generica.2026!" not in raw and b"Sql.Secreta.99" not in raw
    site = client.app_.state.site
    assert site.get("ad", "default_password") == "Generica.2026!"
    assert client.app_.state.s.get("sql", "server") == "SQL01\\SUITE"
    # vacio = conservar; clear = borrar
    save_cfg(client, ad_default_password="", sql_password="", clear_sql_password="1")
    assert site.get("ad", "default_password") == "Generica.2026!"
    assert site.get("sql", "password") == ""
    # sobrevive reinicio (descifra con master.key)
    again = create_app(settings(tmp_path))
    assert again.state.site.get("ad", "default_password") == "Generica.2026!"
    # la auditoria registra que cambio, no los valores
    assert "Generica" not in client.get("/audit").text


def test_config_rejects_bad_db_name(client):
    login(client)
    r = save_cfg(client, sql_db_radio="x]; DROP TABLE y--")
    assert "Nombre de base inválido" in r.text
    assert client.app_.state.site.get("sql", "db_radio") == "ExtensaRadio"


def test_config_test_connection(client):
    login(client)
    r = client.post("/config/test", data={"csrf": csrf(client, "/config"), "target": "ad"})
    assert "AD simulado" in r.text


# --- arranque en modo real sin AD configurado ----------------------------
@pytest.fixture
def real_app(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "build_services",
                        lambda s: (DemoDirectory("demo"), DemoSuitestensa()))
    return create_app(settings(tmp_path, mode="real"))


def test_bootstrap_not_available_remotely(real_app):
    c = TestClient(real_app, follow_redirects=True, client=("10.0.0.9", 5000))
    assert "AD no está configurado" in c.get("/login").text
    assert c.get("/bootstrap").status_code == 404
    local_behind_proxy = TestClient(real_app, client=("127.0.0.1", 5000),
                                    headers={"X-Forwarded-For": "10.0.0.9"})
    assert local_behind_proxy.get("/bootstrap").status_code == 404


def test_bootstrap_local_then_closed(real_app):
    c = TestClient(real_app, follow_redirects=True, client=("127.0.0.1", 5000))
    assert c.get("/login").url.path == "/bootstrap"
    r = c.post("/bootstrap", data={**CFG, "csrf": csrf(c, "/bootstrap")})
    assert r.url.path == "/login"
    assert c.get("/bootstrap").status_code == 404  # ya configurado: se cierra
    assert login(c).url.path == "/"


def test_reset_ad_reopens_bootstrap(real_app):
    real_app.state.site.update(CFG)
    real_app.state.site.reset("ad")
    assert not real_app.state.site.ad_configured()
    assert real_app.state.site.get("sql", "password") == "Sql.Secreta.99"


# --- CLI del instalador ------------------------------------------------------
def test_cli_init_creates_key_db_and_cert(tmp_path, monkeypatch):
    from app import cli
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"""[app]
mode = "real"
data_dir = '{tmp_path / "data"}'
[server]
port = 8444
tls_cert = '{tmp_path / "certs" / "c.crt"}'
tls_key = '{tmp_path / "certs" / "c.key"}'
""")
    monkeypatch.delenv("PROV_CONFIG", raising=False)
    assert cli.main(["--config", str(cfg), "init"]) == 0
    assert (tmp_path / "data" / "app.db").exists() and (tmp_path / "data" / "master.key").exists()
    assert (tmp_path / "certs" / "c.crt").exists()
    assert cli.main(["--config", str(cfg), "check"]) == 1  # real sin configurar
