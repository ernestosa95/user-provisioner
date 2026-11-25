import secrets
import time
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware.sessions import SessionMiddleware

from . import bulk, provisioning
from .provisioning import USERNAME_RE
from .config import ROOT, Settings, load_settings
from .crypto import Vault
from .services.base import ServiceError, UserSpec
from .site_config import FIELDS, SiteConfig
from .store import Store

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
_fails: dict[str, list[float]] = {}  # anti fuerza bruta basico, en memoria
SUPERUSER = "ebitadmin"  # fijo: unico que gestiona admins, roles y configuracion
_LOCAL = {"127.0.0.1", "::1"}


def _is_local(request: Request) -> bool:
    """Acceso directo desde la propia VM (no a traves de un reverse proxy)."""
    h = request.headers
    return (request.client is not None and request.client.host in _LOCAL
            and "x-forwarded-for" not in h and "forwarded" not in h)


def _normalize(username: str) -> str:
    """Acepta 'DOMINIO\\usuario' o 'usuario@dominio' y devuelve 'usuario'."""
    u = username.strip().lower()
    return u.rsplit("\\", 1)[-1].split("@", 1)[0]


def build_services(s: Settings):
    if s.mode == "demo":
        from .services.demo import DemoDirectory, DemoSuitestensa
        return DemoDirectory(s.get("app", "demo_password", "demo")), DemoSuitestensa()
    from .services.ad_powershell import PowerShellDirectory
    from .services.sql_suitestensa import SqlSuitestensa
    return PowerShellDirectory(s), SqlSuitestensa(s)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()
    app = FastAPI(title="Alta de usuarios", docs_url=None, redoc_url=None)
    app.state.s = s
    app.state.store = Store(s.data_dir / "app.db")
    site = app.state.site = SiteConfig(app.state.store, Vault(s.data_dir))
    s.attach(site)
    app.state.ad, app.state.db = build_services(s)
    app.state.bulk = bulk.BulkManager()
    app.add_middleware(SessionMiddleware, secret_key=site.session_key(),
                       same_site="strict", https_only=bool(s.get("app", "https_only", False)),
                       max_age=8 * 3600)
    app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")

    def render(request, name, **ctx):
        ctx.update(user=request.session.get("user"), site=s.get("app", "site_name", ""),
                   mode=s.mode, csrf=csrf_token(request))
        return templates.TemplateResponse(request, name, ctx)

    def csrf_token(request):
        if "csrf" not in request.session:
            request.session["csrf"] = secrets.token_urlsafe(24)
        return request.session["csrf"]

    class NeedLogin(Exception): ...

    @app.exception_handler(NeedLogin)
    async def _need_login(request, exc):
        return RedirectResponse("/login", 303)

    def needs_bootstrap() -> bool:
        return s.mode == "real" and not site.ad_configured()

    def allowed(username: str) -> bool:
        return username == SUPERUSER or app.state.store.is_admin(username)

    def current(request: Request):
        u = request.session.get("user")
        if not u or not allowed(u["username"]):  # se re-chequea en cada request
            raise NeedLogin()
        return u

    def superuser(u=Depends(current)):
        if not u["is_super"]:
            raise HTTPException(403, "Requiere administrador principal")
        return u

    async def check_csrf(request: Request):
        form = await request.form()
        if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "x")):
            raise HTTPException(400, "Token CSRF invalido")

    def flash(request, msg, kind="ok"):
        request.session["flash"] = [kind, msg]

    @app.middleware("http")
    async def headers(request, call_next):
        r = await call_next(request)
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["Cache-Control"] = "no-store"
        r.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'"
        return r

    # --- login (contra AD) ----------------------------------------------
    NO_AD = ("AD no está configurado. ebitadmin debe configurarlo desde la propia VM "
             "entrando a https://localhost:<puerto>/")

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if needs_bootstrap():
            if _is_local(request):
                return RedirectResponse("/bootstrap", 303)
            return render(request, "login.html", error=NO_AD)
        return render(request, "login.html", error=None)

    @app.post("/login", dependencies=[Depends(check_csrf)])
    def login(request: Request, username: str = Form(...), password: str = Form(...)):
        if needs_bootstrap():
            return render(request, "login.html", error=NO_AD)
        username = _normalize(username)
        key = f"{request.client.host if request.client else '?'}|{username}"
        recent = [t for t in _fails.get(key, []) if time.time() - t < 300]
        if len(recent) >= 5:  # tambien protege la cuenta de AD de un bloqueo por intentos
            return render(request, "login.html", error="Demasiados intentos. Espere 5 minutos.")
        try:
            # Solo se consulta AD para usuarios habilitados en la app.
            ok = allowed(username) and app.state.ad.authenticate(username, password)
        except ServiceError as e:
            return render(request, "login.html", error=f"No se pudo validar contra AD: {e}")
        if not ok:
            _fails[key] = recent + [time.time()]
            app.state.store.log(username, "login", None, False)
            return render(request, "login.html", error="Usuario o clave incorrectos.")
        _fails.pop(key, None)
        request.session.clear()
        request.session["user"] = {"username": username, "is_super": username == SUPERUSER}
        app.state.store.log(username, "login", None, True)
        return RedirectResponse("/", 303)

    @app.post("/logout", dependencies=[Depends(check_csrf)])
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", 303)

    # --- alta de usuario ---------------------------------------------
    def form_ctx(request, values=None, errors=None):
        rm = app.state.store.role_map()
        base = dict(values=values or {}, errors=errors or [], ad_external=ad_external())
        try:
            roles = [(r, r in rm) for r in app.state.db.list_roles()]
            return base | dict(roles=roles, prefixes=app.state.db.list_prefixes(),
                               bus=app.state.db.list_business_units(), db_error=None)
        except ServiceError as e:
            return base | dict(roles=[], prefixes=[], bus=[], db_error=str(e))

    def ad_external() -> bool:
        return bool(s.get("ad", "external", False))

    @app.get("/", response_class=HTMLResponse)
    def new_user(request: Request, u=Depends(current)):
        return render(request, "new_user.html", **form_ctx(request))

    @app.post("/users", dependencies=[Depends(check_csrf)], response_class=HTMLResponse)
    async def create_user(request: Request, u=Depends(current)):
        f = await request.form()
        g = lambda k: str(f.get(k, "")).strip()
        spec = UserSpec(
            username=g("username").lower(), first_name=g("first_name"), last_name=g("last_name"),
            prefix=g("prefix"), role=g("role"),
            business_units=[b for b in dict.fromkeys(f.getlist("bu")) if b],
            existing_ad=ad_external() or g("existing_ad") == "1")
        store = app.state.store
        # AD (PowerShell) y SQL son bloqueantes: fuera del event loop.
        password = s.get("ad", "default_password") or ("Demo.Pass.01" if s.mode == "demo" else "")
        ok, steps = await run_in_threadpool(
            provisioning.provision, spec, app.state.ad, app.state.db, store.role_map(),
            password,
            rollback_ad=bool(s.get("app", "rollback_ad_on_sql_failure", True)),
            ad_external=ad_external())
        store.log(u["username"], "create_user", spec.username, ok,
                  {"role": spec.role, "bu": spec.business_units, "existing_ad": spec.existing_ad,
                   "steps": [(x.name, x.ok, x.detail) for x in steps]})
        if not ok:
            keep = ["username", "first_name", "last_name", "prefix", "role"]
            values = {k: g(k) for k in keep} | {"bu": spec.business_units,
                                                "existing_ad": spec.existing_ad}
            ctx = await run_in_threadpool(form_ctx, request, values, steps)
            return render(request, "new_user.html", **ctx)
        return render(request, "result.html", spec=spec, steps=steps)

    # --- carga masiva desde Excel ------------------------------------
    @app.get("/bulk", response_class=HTMLResponse)
    def bulk_form(request: Request, u=Depends(current)):
        return render(request, "bulk.html", error=None, ad_external=ad_external(),
                      running=app.state.bulk.running())

    @app.post("/bulk/preview", dependencies=[Depends(check_csrf)], response_class=HTMLResponse)
    async def bulk_preview(request: Request, u=Depends(current)):
        f = await request.form()
        up = f.get("file")
        use_existing = f.get("use_existing") == "1"
        try:
            if not isinstance(up, UploadFile) or not up.filename:
                raise ServiceError("Seleccione el Excel (.xlsx)")
            data = await up.read(10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise ServiceError("El archivo supera los 10 MB")
            password = s.get("ad", "default_password") or ("Demo.Pass.01" if s.mode == "demo" else "")
            rows = await run_in_threadpool(lambda: bulk.preview(
                bulk.read_excel(data), app.state.ad, app.state.db, app.state.store.role_map(),
                use_existing=use_existing, ad_external=ad_external(), has_password=bool(password)))
        except ServiceError as e:
            return render(request, "bulk.html", error=str(e), ad_external=ad_external(),
                          running=app.state.bulk.running())
        token = app.state.bulk.save_preview(u["username"], up.filename, rows)
        return render(request, "bulk_preview.html", rows=rows, token=token, filename=up.filename,
                      valid=sum(r.ok for r in rows))

    @app.post("/bulk/run", dependencies=[Depends(check_csrf)])
    def bulk_run(request: Request, token: str = Form(...), u=Depends(current)):
        store = app.state.store
        password = s.get("ad", "default_password") or ("Demo.Pass.01" if s.mode == "demo" else "")
        rollback = bool(s.get("app", "rollback_ad_on_sql_failure", True))

        def run_one(job, spec):
            ok, steps = provisioning.provision(spec, app.state.ad, app.state.db, store.role_map(),
                                               password, rollback_ad=rollback,
                                               ad_external=ad_external())
            store.log(u["username"], "create_user", spec.username, ok,
                      {"role": spec.role, "bu": spec.business_units, "existing_ad": spec.existing_ad,
                       "bulk": job.id, "steps": [(x.name, x.ok, x.detail) for x in steps]})
            return ok, steps

        def on_done(job):
            store.log(u["username"], "bulk_upload", job.filename, job.ok_count == len(job.rows),
                      {"job": job.id, "total": len(job.rows), "ok": job.ok_count})

        try:
            job = app.state.bulk.start(token, u["username"], run_one, on_done)
        except ServiceError as e:
            return render(request, "bulk.html", error=str(e), ad_external=ad_external(),
                          running=app.state.bulk.running())
        return RedirectResponse(f"/bulk/job/{job.id}", 303)

    @app.get("/bulk/job/{job_id}", response_class=HTMLResponse)
    def bulk_job(request: Request, job_id: str, u=Depends(current)):
        job = app.state.bulk.jobs.get(job_id)
        if job is None:
            raise HTTPException(404)
        return render(request, "bulk_job.html", job=job)

    # --- admin: roles, admins, auditoria --------------------------------
    @app.get("/roles", response_class=HTMLResponse)
    def roles(request: Request, u=Depends(current)):
        try:
            db_roles, profiles, err = app.state.db.list_roles(), app.state.db.list_profiles(), None
        except ServiceError as e:
            db_roles, profiles, err = [], [], str(e)
        return render(request, "roles.html", rm=app.state.store.role_map(),
                      db_roles=db_roles, profiles=profiles, err=err)

    @app.post("/roles", dependencies=[Depends(check_csrf)])
    def roles_save(request: Request, role: str = Form(...), profile: str = Form(...),
                   u=Depends(superuser)):
        app.state.store.set_role_map(role.strip(), profile.strip())
        app.state.store.log(u["username"], "role_map", role, True, {"profile": profile})
        return RedirectResponse("/roles", 303)

    @app.post("/roles/delete", dependencies=[Depends(check_csrf)])
    def roles_del(role: str = Form(...), u=Depends(superuser)):
        app.state.store.delete_role_map(role)
        app.state.store.log(u["username"], "role_map_delete", role, True)
        return RedirectResponse("/roles", 303)

    @app.get("/admins", response_class=HTMLResponse)
    def admins(request: Request, u=Depends(superuser)):
        msg = request.session.pop("flash", None)
        return render(request, "admins.html", admins=app.state.store.list_admins(),
                      superuser_name=SUPERUSER, msg=msg)

    @app.post("/admins", dependencies=[Depends(check_csrf)])
    def admins_add(request: Request, username: str = Form(...), u=Depends(superuser)):
        username = _normalize(username)
        if not USERNAME_RE.match(username) or username == SUPERUSER:
            flash(request, "Usuario invalido.", "err")
        elif app.state.store.is_admin(username):
            flash(request, f"{username} ya es administrador.", "err")
        else:
            try:
                exists = app.state.ad.user_exists(username)
            except ServiceError as e:
                flash(request, f"No se pudo consultar AD: {e}", "err")
                return RedirectResponse("/admins", 303)
            if not exists:
                flash(request, f"{username} no existe en AD.", "err")
            else:
                app.state.store.add_admin(username, u["username"])
                app.state.store.log(u["username"], "admin_add", username, True)
                flash(request, f"{username} ahora es administrador.")
        return RedirectResponse("/admins", 303)

    @app.post("/admins/remove", dependencies=[Depends(check_csrf)])
    def admins_remove(request: Request, username: str = Form(...), u=Depends(superuser)):
        app.state.store.remove_admin(username)
        app.state.store.log(u["username"], "admin_remove", username, True)
        flash(request, f"{username} ya no es administrador.")
        return RedirectResponse("/admins", 303)

    # --- configuracion de AD y SQL (solo ebitadmin) ---------------------
    def config_page(request, bootstrap=False, errors=None):
        values = {(f.section, f.key): site.get(f.section, f.key) for f in FIELDS if f.kind != "secret"}
        saved = {(f.section, f.key): site.has_secret(f.section, f.key) for f in FIELDS if f.kind == "secret"}
        return render(request, "config.html", fields=FIELDS, values=values, saved=saved,
                      bootstrap=bootstrap, errors=errors or [], msg=request.session.pop("flash", None))

    def save_config(request, form, actor) -> list[str]:
        errors, changed = site.update(dict(form))
        if not errors:
            app.state.store.log(actor, "config", None, True, {"changed": changed})  # sin valores
        return errors

    @app.get("/bootstrap", response_class=HTMLResponse)
    def bootstrap_form(request: Request):
        if not (needs_bootstrap() and _is_local(request)):
            raise HTTPException(404)
        return config_page(request, bootstrap=True)

    @app.post("/bootstrap", dependencies=[Depends(check_csrf)], response_class=HTMLResponse)
    async def bootstrap_save(request: Request):
        if not (needs_bootstrap() and _is_local(request)):
            raise HTTPException(404)
        errors = save_config(request, await request.form(), "(bootstrap local)")
        if errors or needs_bootstrap():
            return config_page(request, True, errors or ["Complete al menos el dominio de AD."])
        return RedirectResponse("/login", 303)

    @app.get("/config", response_class=HTMLResponse)
    def config_form(request: Request, u=Depends(superuser)):
        return config_page(request)

    @app.post("/config", dependencies=[Depends(check_csrf)], response_class=HTMLResponse)
    async def config_save(request: Request, u=Depends(superuser)):
        errors = save_config(request, await request.form(), u["username"])
        if errors:
            return config_page(request, errors=errors)
        flash(request, "Configuración guardada.")
        return RedirectResponse("/config", 303)

    @app.post("/config/test", dependencies=[Depends(check_csrf)])
    def config_test(request: Request, target: str = Form(...), u=Depends(superuser)):
        svc = {"ad": app.state.ad, "sql": app.state.db}.get(target)
        if svc is None:
            raise HTTPException(400)
        try:
            flash(request, f"{target.upper()}: {svc.test_connection()}")
        except ServiceError as e:
            flash(request, f"{target.upper()}: {e}", "err")
        return RedirectResponse("/config", 303)

    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request, u=Depends(current)):
        return render(request, "audit.html", rows=app.state.store.audit())

    return app
