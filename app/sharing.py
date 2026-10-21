"""Effective-version sharing API and server-rendered owner controls."""
from typing import Annotated, Literal

from fastapi import Depends, Form, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app import repository, security
from app.db import get_db


class SharingUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version_id: int = Field(gt=0, strict=True)
    mode: Literal['private', 'company', 'specified']
    reader_ids: list[Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]] = Field(default_factory=list, max_length=500)
    csrf_token: str = Field(default='', max_length=200)


def install_routes(app, require_identity, optional_identity, public_identity, templates):
    @app.put('/api/documents/{document_id}/sharing')
    def update_sharing(document_id: int, payload: SharingUpdate, request: Request,
                       identity=Depends(require_identity), connection=Depends(get_db),
                       x_csrf_token: str = Header('', max_length=200)):
        security.check_session_csrf(request, x_csrf_token or payload.csrf_token)
        result = repository.replace_sharing(connection, identity, document_id, payload.version_id,
                                             payload.mode, payload.reader_ids)
        connection.commit()
        return result

    @app.get('/documents/{document_id}/sharing')
    def sharing_page(document_id: int, request: Request, identity=Depends(optional_identity),
                     connection=Depends(get_db)):
        if identity is None:
            return RedirectResponse('/login', status_code=303)
        document = repository.owned_current_version(connection, identity, document_id)
        if document is None:
            raise HTTPException(404, '找不到文件')
        readers = repository.sharing_readers(connection, identity, document['version_id'])
        mode = 'company' if document['visibility'] == 'company' else ('specified' if readers else 'private')
        return templates.TemplateResponse(request, 'sharing.html', {
            'me': public_identity(identity, request), 'document': document, 'mode': mode,
            'reader_ids': readers, 'candidates': repository.active_sharing_candidates(connection, identity),
            'saved': request.query_params.get('saved') == '1',
        })

    @app.post('/documents/{document_id}/sharing')
    def sharing_form(document_id: int, request: Request, version_id: int = Form(..., gt=0),
                     mode: Literal['private', 'company', 'specified'] = Form(...),
                     reader_ids: list[int] = Form([]), csrf_token: str = Form('', max_length=200),
                     identity=Depends(require_identity), connection=Depends(get_db)):
        security.check_session_csrf(request, csrf_token)
        if len(reader_ids) > 500:
            raise HTTPException(422, '一次最多選取 500 位閱讀者')
        repository.replace_sharing(connection, identity, document_id, version_id, mode, reader_ids)
        connection.commit()
        return RedirectResponse(f'/documents/{document_id}/sharing?saved=1', status_code=303)
