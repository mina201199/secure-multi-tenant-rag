"""HTTP entry point: session authentication, current-version ACLs and M2 routes."""
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import timezone, timedelta

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings
from app.db import get_db
from app.repository import get_document, list_documents
from app.security import (
    LOGIN_CSRF_COOKIE, SESSION_COOKIE, attempt_login, check_login_csrf,
    check_session_csrf, hash_token, new_login_csrf, read_identity,
    session_csrf, set_session_cookie,
)

templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
templates.env.filters["taipei_time"] = lambda value: value.astimezone(
    timezone(timedelta(hours=8))
).strftime("%Y-%m-%d %H:%M（UTC+8）")


def require_identity(request: Request, connection=Depends(get_db, scope="function")):
    identity = read_identity(
        connection, request.cookies.get(SESSION_COOKIE), request.app.state.settings
    )
    if not identity:
        raise HTTPException(401, "請先登入")
    return identity


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(application):
        application.state.settings = settings or Settings.from_env()
        yield

    application = FastAPI(title="公司知識文件平台", lifespan=lifespan)

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Preserve same-origin form POST Origin; no-referrer can turn it into "null".
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path == "/docs":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'unsafe-inline' https://cdn.jsdelivr.net; connect-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
            )
        return response

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        if request.url.path.startswith("/api/") or request.url.path == "/health":
            return JSONResponse({"detail": error.detail}, error.status_code, headers=error.headers)
        if error.status_code == 401 and request.method == "GET":
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request, name="error.html",
            context={"message": error.detail}, status_code=error.status_code, headers=error.headers,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request, error):
        return JSONResponse({"detail": "服務暫時無法使用，請稍後重試"}, status_code=500)

    @application.get("/health")
    def health(connection=Depends(get_db, scope="function")):
        connection.execute("SELECT id FROM app_core.document_versions LIMIT 1")
        return {"status": "ok"}

    @application.get("/")
    def root():
        return RedirectResponse("/documents", status_code=303)

    @application.get("/login")
    def login_page(request: Request):
        csrf = new_login_csrf(request.app.state.settings)
        response = templates.TemplateResponse(
            request=request, name="login.html", context={"csrf": csrf}
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE, csrf, max_age=600, httponly=True,
            secure=request.app.state.settings.cookie_secure, samesite="strict", path="/",
        )
        return response

    @application.post("/login")
    def login(
        request: Request, username: str = Form(max_length=120),
        password: str = Form(max_length=1024), csrf_token: str = Form(max_length=256),
        connection=Depends(get_db, scope="function"),
    ):
        check_login_csrf(request, csrf_token)
        raw = attempt_login(connection, request, username, password)
        response = RedirectResponse("/documents", status_code=303)
        set_session_cookie(response, raw, request.app.state.settings)
        return response

    @application.post("/logout")
    def logout(
        request: Request, csrf_token: str = Form(max_length=256),
        identity=Depends(require_identity), connection=Depends(get_db, scope="function"),
    ):
        check_session_csrf(request, csrf_token)
        connection.execute(
            "SELECT app_core.auth_logout(%s)",
            (hash_token(request.cookies[SESSION_COOKIE]),),
        )
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @application.get("/api/me")
    def me(identity=Depends(require_identity)):
        return {key: value for key, value in identity.items() if key != "token_hash"}

    @application.get("/api/documents")
    def documents_api(identity=Depends(require_identity), connection=Depends(get_db, scope="function")):
        return list_documents(connection, identity)

    @application.get("/api/documents/{document_id}")
    def document_api(document_id: int, identity=Depends(require_identity), connection=Depends(get_db, scope="function")):
        document = get_document(connection, identity, document_id) if 0 < document_id < 2**63 else None
        if document is None:
            raise HTTPException(404, "找不到文件")
        return document

    def page_context(request, identity):
        return {
            "identity": identity,
            "test_mode": request.app.state.settings.environment == "test",
            "csrf": session_csrf(request.app.state.settings, request.cookies[SESSION_COOKIE]),
        }

    @application.get("/documents")
    def documents_page(request: Request, identity=Depends(require_identity), connection=Depends(get_db, scope="function")):
        return templates.TemplateResponse(
            request=request, name="documents.html",
            context={**page_context(request, identity), "documents": list_documents(connection, identity)},
        )

    @application.get("/documents/{document_id}")
    def document_page(
        request: Request, document_id: int,
        identity=Depends(require_identity), connection=Depends(get_db, scope="function"),
    ):
        document = document_api(document_id, identity, connection)
        return templates.TemplateResponse(
            request=request, name="document.html",
            context={**page_context(request, identity), "document": document},
        )

    from app.lifecycle import make_router
    application.include_router(make_router(require_identity, page_context, templates))
    from app.rag_routes import make_router as rag_router
    application.include_router(rag_router(require_identity, page_context, templates))
    return application


app = create_app()
