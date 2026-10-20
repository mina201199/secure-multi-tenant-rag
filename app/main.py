"""FastAPI 入口：M1 的健康檢查、登入 / 登出、目前身分與文件閱讀（API 與 Jinja2 頁面）。"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app import repository, security, version_service
from app.config import Settings
from app.db import create_pool, get_db
from app.errors import install_error_handlers

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
DOCUMENT_NOT_FOUND = "找不到文件"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    pool = create_pool(settings.database_url)
    # 啟動時就確認資料庫連得上，設定錯誤會讓容器直接失敗，而不是等到第一位使用者才發現
    pool.open(wait=True, timeout=30)
    app.state.settings = settings
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Secure Multi-Tenant RAG", lifespan=lifespan)
install_error_handlers(app, TEMPLATES)
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')


@app.get('/demo')
def workspace_demo(request: Request):
    # 純展示頁不讀取 Session、資料庫或付費模型，僅提供固定的虛構人物。
    return TEMPLATES.TemplateResponse(request, 'demo.html', {
        'demo_mode': True,
        'me': {'tenant_name': '甲公司', 'display_name': '林以安', 'username': 'a.owner', 'role': 'employee'},
    })


def optional_identity(request: Request, connection=Depends(get_db)):
    return security.read_identity(
        connection, request.cookies.get(security.SESSION_COOKIE), request.app.state.settings
    )


def require_identity(identity=Depends(optional_identity)):
    if identity is None:
        raise HTTPException(401, "請先登入")
    return identity


def public_identity(identity, request):
    # 只回傳前端需要的欄位；token_hash 等內部欄位不離開後端
    return {
        "id": identity["id"],
        "username": identity["username"],
        "display_name": identity["display_name"],
        "role": identity["role"],
        "tenant_name": identity["tenant_name"],
        "csrf_token": security.session_csrf(
            request.app.state.settings, request.cookies[security.SESSION_COOKIE]
        ),
    }


def render_login(request, error=None, status_code=200):
    token = security.new_login_csrf(request.app.state.settings)
    response = TEMPLATES.TemplateResponse(
        request, "login.html", {"csrf_token": token, "error": error}, status_code=status_code
    )
    security.set_login_csrf_cookie(response, token, request.app.state.settings)
    return response


@app.get("/health")
def health(request: Request):
    # 刻意不經過 Session，也不回傳錯誤細節，避免洩漏連線字串或內部路徑
    try:
        with request.app.state.pool.connection(timeout=3) as connection:
            connection.execute("SELECT 1")
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ok"}


@app.get("/")
def home(identity=Depends(optional_identity)):
    return RedirectResponse("/documents" if identity else "/login", status_code=303)


@app.get("/login")
def login_page(request: Request, identity=Depends(optional_identity)):
    if identity:
        return RedirectResponse("/documents", status_code=303)
    return render_login(request)


@app.post("/login")
def login(
    request: Request,
    username: str = Form(..., max_length=64),
    password: str = Form(..., max_length=256),
    csrf_token: str = Form("", max_length=200),
    connection=Depends(get_db),
):
    try:
        security.check_login_csrf(request, csrf_token)
        raw = security.attempt_login(connection, request, username, password)
    except HTTPException as error:
        # 表單登入失敗時重新顯示登入頁（附新的 CSRF token），狀態碼仍保留 401 / 403 / 429 方便 API 判斷
        response = render_login(request, error.detail, error.status_code)
        if error.headers:
            response.headers.update(error.headers)
        return response
    # 先 commit 再回應，確保瀏覽器拿到 Cookie 時 Session 已確實寫入資料庫
    connection.commit()
    response = RedirectResponse("/documents", status_code=303)
    security.set_session_cookie(response, raw, request.app.state.settings)
    return response


@app.post("/logout")
def logout(
    request: Request,
    csrf_token: str = Form("", max_length=200),
    identity=Depends(require_identity),
    connection=Depends(get_db),
):
    security.check_session_csrf(request, csrf_token)
    security.end_session(connection, request.cookies.get(security.SESSION_COOKIE))
    connection.commit()
    response = RedirectResponse("/login", status_code=303)
    security.clear_session_cookie(response)
    return response


@app.get("/api/me")
def me(request: Request, identity=Depends(require_identity)):
    return public_identity(identity, request)


@app.get("/api/documents")
def api_list_documents(identity=Depends(require_identity), connection=Depends(get_db)):
    return repository.list_documents(connection, identity)


@app.get("/api/documents/{document_id}")
def api_get_document(document_id: int, identity=Depends(require_identity), connection=Depends(get_db)):
    document = repository.get_document(connection, identity, document_id)
    # 不存在與無權閱讀回傳完全相同的 404，猜 ID 也無法得知別人的文件是否存在
    if document is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    return document


@app.get("/documents")
def documents_page(request: Request, identity=Depends(optional_identity), connection=Depends(get_db)):
    if identity is None:
        return RedirectResponse("/login", status_code=303)
    return TEMPLATES.TemplateResponse(request, "documents.html", {
        "me": public_identity(identity, request),
        "documents": repository.list_documents(connection, identity),
    })


@app.get("/documents/{document_id}")
def document_page(
    document_id: int, request: Request, identity=Depends(optional_identity), connection=Depends(get_db)
):
    if identity is None:
        return RedirectResponse("/login", status_code=303)
    document = repository.get_document(connection, identity, document_id)
    if document is None:
        raise HTTPException(404, DOCUMENT_NOT_FOUND)
    # Management belongs to the document owner, independently of reader/share rights.
    try:
        can_manage_versions = version_service.get_version_management(connection, identity, document_id) is not None
    except HTTPException as error:
        if error.status_code != 404:
            raise
        can_manage_versions = False
    return TEMPLATES.TemplateResponse(request, "document.html", {
        "me": public_identity(identity, request),
        "document": document,
        "can_manage_versions": can_manage_versions,
    })


from app.sharing import install_routes as install_sharing_routes
install_sharing_routes(app, require_identity, optional_identity, public_identity, TEMPLATES)

from app.version_routes import install_routes as install_version_routes
install_version_routes(app, require_identity, optional_identity, public_identity, TEMPLATES)
