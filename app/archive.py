"""文件擁有者的封存入口；交易能力與 worker 共用鎖序。"""
from typing import Annotated

from fastapi import Depends, Form, Header, HTTPException, Path, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app import security, version_service
from app.db import get_db

DocumentId = Annotated[int, Path(ge=1, le=9223372036854775807)]


class ArchiveRequest(BaseModel):
  model_config = ConfigDict(extra='forbid')
  expected_current_version_id: int = Field(gt=0, le=9223372036854775807, strict=True)
  csrf_token: str = Field(default='', max_length=200)


def archiveAndCommit(connection, identity, documentId, expectedVersionId):
  result = version_service.archive_document(connection, identity, documentId, expectedVersionId)
  connection.commit()
  return result


def install_routes(app, require_identity, optional_identity, public_identity, templates):
  @app.post('/api/documents/{document_id}/archive')
  def archive_api(document_id: DocumentId, payload: ArchiveRequest, request: Request,
      identity=Depends(require_identity), connection=Depends(get_db),
      x_csrf_token: str = Header('', max_length=200)):
    security.check_session_csrf(request, x_csrf_token or payload.csrf_token)
    return archiveAndCommit(connection, identity, document_id, payload.expected_current_version_id)

  @app.get('/documents/{document_id}/archive')
  def archive_page(document_id: DocumentId, request: Request, identity=Depends(optional_identity),
      connection=Depends(get_db)):
    if identity is None:
      return RedirectResponse('/login', status_code=303)
    document = version_service.get_version_management(connection, identity, document_id)
    if document is None:
      raise HTTPException(404, '找不到文件')
    return templates.TemplateResponse(request, 'archive.html', {
      'me': public_identity(identity, request), 'document': document})

  @app.post('/documents/{document_id}/archive')
  def archive_form(document_id: DocumentId, request: Request,
      expected_current_version_id: int = Form(..., gt=0, le=9223372036854775807),
      csrf_token: str = Form('', max_length=200), identity=Depends(require_identity),
      connection=Depends(get_db)):
    security.check_session_csrf(request, csrf_token)
    archiveAndCommit(connection, identity, document_id, expected_current_version_id)
    return RedirectResponse('/documents', status_code=303)
