import re
from pathlib import PurePath
import psycopg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import Field
from starlette.datastructures import UploadFile
from app.db import get_db
from app.ingestion import MAX_FILE
from app.lifecycle import ID, Payload, db_error
from app.models import ModelUnavailable
from app.rag import Retrieval, SELECT_CHUNKS
from app.security import check_session_csrf


class Question(Payload):
    question: str = Field(min_length=2, max_length=1000)


def make_router(require_identity, page_context, templates):
    router = APIRouter()
    auth, db = Depends(require_identity), Depends(get_db, scope="function")

    def render(request, identity, name, **context):
        return templates.TemplateResponse(request=request, name=name,
            context={**page_context(request, identity), **context})

    @router.get("/upload")
    def upload_page(request: Request, identity=auth):
        return render(request, identity, "upload.html")

    async def receive(request, identity, connection, document_id=None, html=False):
        # Dependencies authenticate before consuming any multipart body.
        received = 0
        original_receive = request._receive
        async def limited_receive():
            nonlocal received
            message = await original_receive()
            received += len(message.get("body", b""))
            if received > MAX_FILE + 65536:
                raise HTTPException(413, "上傳上限為 10 MiB")
            return message
        request._receive = limited_receive
        try:
            content_length = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "上傳長度不正確") from None
        if content_length > MAX_FILE + 65536:
            raise HTTPException(413, "上傳上限為 10 MiB")
        if not html:
            check_session_csrf(request, request.headers.get("X-CSRF-Token", ""))
        async with request.form(max_files=1, max_fields=5, max_part_size=4096) as form:
            if html:
                check_session_csrf(request, str(form.get("csrf_token", "")))
            if set(form) - {"file", "title", "category", "csrf_token", "document_id"}:
                raise HTTPException(422, "上傳欄位不正確")
            if html and form.get("document_id"):
                try:
                    document_id = int(form["document_id"])
                except ValueError:
                    raise HTTPException(422, "文件編號不正確") from None
            if document_id is not None and not 0 < document_id < 2**63:
                raise HTTPException(404, "找不到資料")
            # Reject forbidden updates before reading the source bytes.
            if document_id is not None and not connection.execute(
                "SELECT id FROM app_core.documents WHERE id=%s AND tenant_id=%s AND owner_id=%s AND status='ready'",
                (document_id, identity["tenant_id"], identity["id"])).fetchone():
                raise HTTPException(404, "找不到資料")
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise HTTPException(422, "請選擇檔案")
            filename = re.split(r"[/\\]", file.filename or "")[-1]
            if not 1 <= len(filename) <= 180 or any(ord(c) < 32 for c in filename):
                raise HTTPException(422, "檔名不正確")
            suffix = PurePath(filename).suffix.lower()
            media = {".pdf": "application/pdf", ".txt": "text/plain"}.get(suffix)
            if not media or file.content_type not in {media, "application/octet-stream"}:
                raise HTTPException(415, "僅接受文字型 PDF 與 UTF-8 TXT")
            body = await file.read(MAX_FILE + 1)
            if not body or len(body) > MAX_FILE:
                raise HTTPException(413, "檔案為空或超過 10 MiB")
            if media == "application/pdf" and not body.startswith(b"%PDF-"):
                raise HTTPException(422, "檔案不是有效 PDF")
            if media == "text/plain":
                try:
                    text = body.decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise HTTPException(422, "TXT 必須使用 UTF-8 編碼") from None
                if "\x00" in text or len(body) > 1048576:
                    raise HTTPException(422, "TXT 格式或大小不正確")
            title, category = str(form.get("title", filename)).strip(), str(form.get("category", "一般文件")).strip()
            try:
                result = connection.execute("SELECT app_core.upload(%s,%s,%s,%s,%s,%s) AS result",
                    (document_id, title, category, filename, media, body)).fetchone()["result"]
            except psycopg.Error as error:
                db_error(error)
        return RedirectResponse(f"/manage/{result['document_id']}", 303) if html else result

    @router.post("/api/documents", status_code=202)
    async def upload(request: Request, identity=auth, connection=db):
        return await receive(request, identity, connection)

    @router.post("/api/documents/{document_id}/upload", status_code=202)
    async def upload_version(request: Request, document_id: ID, identity=auth, connection=db):
        return await receive(request, identity, connection, document_id)

    @router.post("/upload")
    async def upload_form(request: Request, identity=auth, connection=db):
        return await receive(request, identity, connection, html=True)

    @router.post("/api/documents/{document_id}/index", status_code=202)
    def reindex(request: Request, document_id: ID, identity=auth, connection=db):
        check_session_csrf(request, request.headers.get("X-CSRF-Token", ""))
        try:
            return connection.execute("SELECT app_core.reindex(%s) AS result", (document_id,)).fetchone()["result"]
        except psycopg.Error as error:
            db_error(error)

    def query(request, identity, question, answer=True):
        if not 2 <= len(question.strip()) <= 1000:
            raise HTTPException(422, "請輸入 2 至 1,000 字的問題")
        try:
            retrieval = Retrieval(request.app.state.settings, identity)
            return retrieval.answer(question) if answer else {"sources": retrieval.search(question)}
        except ModelUnavailable as error:
            raise HTTPException(503, str(error)) from None

    @router.post("/api/search")
    def search(request: Request, payload: Question, identity=auth):
        check_session_csrf(request, request.headers.get("X-CSRF-Token", ""))
        return query(request, identity, payload.question, False)

    @router.post("/api/ask")
    def ask(request: Request, payload: Question, identity=auth):
        check_session_csrf(request, request.headers.get("X-CSRF-Token", ""))
        return query(request, identity, payload.question)

    @router.get("/ask")
    def ask_page(request: Request, identity=auth):
        return render(request, identity, "ask.html", result=None, question="")

    @router.post("/ask")
    def ask_form(request: Request, question: str = Form(max_length=1000), csrf_token: str = Form(max_length=256), identity=auth):
        check_session_csrf(request, csrf_token)
        return render(request, identity, "ask.html", result=query(request, identity, question), question=question)

    def citation(chunk_id, identity, connection):
        row = connection.execute(SELECT_CHUNKS + " AND c.id=%(id)s",
                                 {"tenant": identity["tenant_id"], "id": chunk_id}).fetchone()
        if not row:
            raise HTTPException(404, "找不到資料")
        return row

    @router.get("/api/citations/{chunk_id}")
    def citation_api(chunk_id: ID, identity=auth, connection=db):
        return citation(chunk_id, identity, connection)

    @router.get("/citations/{chunk_id}")
    def citation_page(request: Request, chunk_id: ID, identity=auth, connection=db):
        return render(request, identity, "citation.html", source=citation(chunk_id, identity, connection))

    @router.get("/api/usage")
    def usage(identity=auth, connection=db):
        return connection.execute(
            "SELECT id,operation,model,status,input_tokens,output_tokens,charged_microtwd,created_at "
            "FROM app_core.model_usage ORDER BY created_at DESC LIMIT 50").fetchall()

    @router.get("/api/inventory")
    def inventory(identity=auth, connection=db):
        return [row["item"] for row in connection.execute("SELECT app_core.ingestion_inventory() AS item").fetchall()]

    return router
