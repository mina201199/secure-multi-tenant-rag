"""Bounded TXT/PDF uploads; business writes stay in restricted version service."""
import re
import secrets
from uuid import UUID
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app import security, version_service
from app.db import get_db

MAX_SOURCE_BYTES = 1024 * 1024
MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_PDF_BYTES + 64 * 1024
IDEMPOTENCY_KEY = re.compile(r'[A-Za-z0-9_-]{16,128}', re.ASCII)
POSITIVE_BIGINT = re.compile(r'[1-9][0-9]{0,18}', re.ASCII)
DocumentId = Annotated[int, Path(ge=1, le=9223372036854775807)]
UPLOAD_SCHEMA = {
    'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {
        'type': 'object', 'additionalProperties': False,
        'required': ['file', 'expected_current_version_id', 'csrf_token'],
        'properties': {
            'file': {'type': 'string', 'format': 'binary', 'description': 'UTF-8 TXT: 1 MiB; text PDF: 2 MiB / 20 pages; 200,000 characters maximum. PDF validation is asynchronous.'},
            'expected_current_version_id': {'type': 'integer', 'minimum': 1, 'maximum': 9223372036854775807},
            'csrf_token': {'type': 'string', 'maxLength': 200},
        },
    }}}},
}


class UploadTooLarge(MultiPartException):
    """Parser-recognized errors close any files opened before the limit."""


async def bounded_request_stream(request):
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise UploadTooLarge('Upload exceeds request capacity')
        yield chunk


def job_identifier(value):
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, '找不到工作') from None


async def read_upload(request, *, html_form=False):
    if request.headers.get('content-type', '').split(';')[0].lower() != 'multipart/form-data':
        raise HTTPException(422, '請使用 TXT／PDF 檔案上傳表單')
    try:
        form = await MultiPartParser(request.headers, bounded_request_stream(request),
                                     max_files=1, max_fields=4, max_part_size=64 * 1024).parse()
    except UploadTooLarge:
        raise HTTPException(413, '上傳請求超過容量限制') from None
    except MultiPartException:
        raise HTTPException(422, '請檢查上傳表單') from None
    try:
        required = {'file', 'expected_current_version_id', 'csrf_token'}
        if html_form:
            required.add('idempotency_key')
        if set(form) != required or any(len(form.getlist(key)) != 1 for key in form):
            raise HTTPException(422, '請檢查上傳表單欄位')
        file = form['file']
        csrf = form['csrf_token']
        expected = form['expected_current_version_id']
        key = form['idempotency_key'] if html_form else request.headers.get('idempotency-key', '')
        if not isinstance(csrf, str) or len(csrf) > 200:
            raise HTTPException(422, '請檢查上傳表單欄位')
        security.check_session_csrf(request, csrf)
        if (not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key)
                or (not html_form and len(request.headers.getlist('idempotency-key')) != 1)):
            raise HTTPException(422, '請提供有效的請求識別碼')
        if (not isinstance(expected, str) or not POSITIVE_BIGINT.fullmatch(expected)
                or int(expected) > 9223372036854775807):
            raise HTTPException(422, '請提供有效的目前版本')
        if not isinstance(file, UploadFile) or not (file.filename or '').lower().endswith(('.txt', '.pdf')):
            raise HTTPException(422, '只接受 UTF-8 TXT 或文字型 PDF')
        sourceFormat = 'pdf' if file.filename.lower().endswith('.pdf') else 'txt'
        limit = MAX_PDF_BYTES if sourceFormat == 'pdf' else MAX_SOURCE_BYTES
        source = await file.read(limit + 1)
        if len(source) > limit:
            raise HTTPException(413, 'PDF 最多 2 MiB；TXT 最多 1 MiB')
        if (sourceFormat == 'pdf') != source.startswith(b'%PDF-'):
            raise HTTPException(422, '檔案格式與副檔名不符')
        return int(expected), source, key, sourceFormat
    finally:
        await form.close()


def create_and_commit(connection, identity, document_id, expected, source, key, source_format='txt'):
    # Keep database waits off the event loop and commit before exposing success.
    if source_format == 'txt':
        result = version_service.create_version(connection, identity, document_id, expected, source, key)
    else:
        result = version_service.create_version(connection, identity, document_id, expected, source, key, source_format)
    connection.commit()
    return result


def install_routes(app, require_identity, optional_identity, public_identity, templates):
    @app.post('/api/documents/{document_id}/versions', status_code=202, openapi_extra=UPLOAD_SCHEMA)
    async def create(document_id: DocumentId, request: Request,
                     identity=Depends(require_identity), connection=Depends(get_db),
                     idempotency_key: str = Header(..., description='16–128 ASCII letters, digits, - or _')):
        expected, source, key, sourceFormat = await read_upload(request)
        result = await run_in_threadpool(create_and_commit, connection, identity, document_id, expected, source, key, sourceFormat)
        return JSONResponse(result, status_code=202)

    @app.get('/api/jobs/{job_id}')
    def status(job_id: str, identity=Depends(require_identity), connection=Depends(get_db)):
        return version_service.get_job(connection, identity, job_identifier(job_id))

    @app.get('/documents/{document_id}/versions')
    def management(document_id: DocumentId, request: Request, identity=Depends(optional_identity),
                   connection=Depends(get_db)):
        if identity is None:
            return RedirectResponse('/login', status_code=303)
        document = version_service.get_version_management(connection, identity, document_id)
        if document is None:
            raise HTTPException(404, '找不到文件')
        job_id = request.query_params.get('job_id')
        job = version_service.get_job(connection, identity, job_identifier(job_id)) if job_id else None
        return templates.TemplateResponse(request, 'versions.html', {
            'me': public_identity(identity, request), 'document': document,
            'idempotency_key': secrets.token_urlsafe(24), 'job': job,
        })

    @app.post('/documents/{document_id}/versions')
    async def submit(document_id: DocumentId, request: Request,
                     identity=Depends(require_identity), connection=Depends(get_db)):
        expected, source, key, sourceFormat = await read_upload(request, html_form=True)
        result = await run_in_threadpool(create_and_commit, connection, identity, document_id, expected, source, key, sourceFormat)
        return RedirectResponse(f'/documents/{document_id}/versions?job_id={result["job_id"]}', status_code=303)
