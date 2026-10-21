"""獨立歷史後台；POST 調閱必須先完成稽核交易才交付內容。"""
from typing import Annotated, Literal

from fastapi import Depends, Form, Header, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app import history_service as service, security
from app.db import get_db

VersionId = Annotated[int, Path(ge=1, le=9223372036854775807)]
AfterId = Annotated[int, Query(ge=0, le=9223372036854775807)]


class ReadRequest(BaseModel):
  model_config = ConfigDict(extra='forbid')
  csrf_token: str = Field(default='', max_length=200)


class AccessRequest(ReadRequest):
  reader_id: int = Field(gt=0, le=9223372036854775807, strict=True)
  granted: bool = Field(strict=True)


def install_routes(app, requireIdentity, optionalIdentity, publicIdentity, templates):
  @app.middleware('http')
  async def historyNoStore(request, callNext):
    response = await callNext(request)
    if request.url.path == '/history' or request.url.path.startswith(('/history/', '/api/history/')):
      response.headers['Cache-Control'] = 'no-store'
    return response

  @app.get('/api/history/versions')
  def historyList(after_id: AfterId = 0, identity=Depends(requireIdentity), connection=Depends(get_db)):
    return service.listVersions(connection, identity, after_id)

  @app.get('/api/history/versions/{version_id}/access')
  def historyAccess(version_id: VersionId, identity=Depends(requireIdentity), connection=Depends(get_db)):
    return service.getAccess(connection, identity, version_id)

  @app.put('/api/history/versions/{version_id}/access')
  def changeAccess(version_id: VersionId, payload: AccessRequest, request: Request,
      identity=Depends(requireIdentity), connection=Depends(get_db), x_csrf_token: str = Header('', max_length=200)):
    security.check_session_csrf(request, x_csrf_token or payload.csrf_token)
    result = service.setAccess(connection, identity, version_id, payload.reader_id, payload.granted)
    connection.commit()
    return result

  @app.post('/api/history/versions/{version_id}/read')
  def readHistory(version_id: VersionId, payload: ReadRequest, request: Request,
      identity=Depends(requireIdentity), connection=Depends(get_db), x_csrf_token: str = Header('', max_length=200)):
    security.check_session_csrf(request, x_csrf_token or payload.csrf_token)
    result = service.readVersion(connection, identity, version_id)
    connection.commit()
    return result

  @app.get('/history')
  def historyPage(request: Request, after_id: AfterId = 0, identity=Depends(optionalIdentity), connection=Depends(get_db)):
    if identity is None:
      return RedirectResponse('/login', status_code=303)
    versions = service.listVersions(connection, identity, after_id)
    return templates.TemplateResponse(request, 'history.html', {
      'me': publicIdentity(identity, request), 'versions': versions})

  @app.get('/history/versions/{version_id}/access')
  def accessPage(version_id: VersionId, request: Request, identity=Depends(optionalIdentity), connection=Depends(get_db)):
    if identity is None:
      return RedirectResponse('/login', status_code=303)
    target = service.getAccess(connection, identity, version_id)
    return templates.TemplateResponse(request, 'history_access.html', {
      'me': publicIdentity(identity, request), 'version': target})

  @app.post('/history/versions/{version_id}/access')
  def accessForm(version_id: VersionId, request: Request,
      reader_id: int = Form(..., gt=0, le=9223372036854775807), action: Literal['grant', 'revoke'] = Form(...),
      csrf_token: str = Form('', max_length=200), identity=Depends(requireIdentity), connection=Depends(get_db)):
    security.check_session_csrf(request, csrf_token)
    service.setAccess(connection, identity, version_id, reader_id, action == 'grant')
    connection.commit()
    return RedirectResponse(f'/history/versions/{version_id}/access', status_code=303)

  @app.post('/history/versions/{version_id}/read')
  def readPage(version_id: VersionId, request: Request, csrf_token: str = Form('', max_length=200),
      identity=Depends(requireIdentity), connection=Depends(get_db)):
    security.check_session_csrf(request, csrf_token)
    target = service.readVersion(connection, identity, version_id)
    # 先完成 render；模板失敗就回滾，避免把未產生的頁面記成成功調閱。
    response = templates.TemplateResponse(request, 'history_read.html', {
      'me': publicIdentity(identity, request), 'version': target})
    connection.commit()
    return response
