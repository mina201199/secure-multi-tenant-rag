"""交接操作共用資料庫能力，提交交易後才交付成功結果。"""
from typing import Annotated, Literal

from fastapi import Depends, Form, Header, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import security, transfer_service as service
from app.db import get_db

ObjectId = Annotated[int, Path(ge=1, le=9223372036854775807)]
AfterId = Annotated[int, Query(ge=0, le=9223372036854775807)]
StrictId = Annotated[int, Field(gt=0, le=9223372036854775807, strict=True)]
FormId = Annotated[int, Form(gt=0, le=9223372036854775807)]


class CreateRequest(BaseModel):
  model_config = ConfigDict(extra='forbid')
  document_id: StrictId
  expected_version_id: StrictId
  version_ids: list[StrictId] = Field(min_length=1, max_length=100)
  recipient_id: StrictId
  reason: str = Field(min_length=1, max_length=500)
  csrf_token: str = Field(default='', max_length=200)

  @field_validator('reason')
  @classmethod
  def cleanReason(cls, value):
    if not value.strip():
      raise ValueError('reason is required')
    return value.strip()


class DecisionRequest(BaseModel):
  model_config = ConfigDict(extra='forbid')
  approve: bool = Field(strict=True)
  csrf_token: str = Field(default='', max_length=200)


def install_routes(app, requireIdentity, optionalIdentity, publicIdentity, templates):
  @app.middleware('http')
  async def transferNoStore(request, callNext):
    response = await callNext(request)
    if request.url.path.startswith(('/api/transfers', '/transfers')) or request.url.path.endswith(('/transfer', '/transfer-options')):
      response.headers['Cache-Control'] = 'no-store'
    return response

  @app.get('/api/transfers')
  def transferList(after_id: AfterId = 0, identity=Depends(requireIdentity), connection=Depends(get_db)):
    return service.listTransfers(connection, identity, after_id)

  @app.get('/api/documents/{document_id}/transfer-options')
  def transferOptions(document_id: ObjectId, identity=Depends(requireIdentity), connection=Depends(get_db)):
    return service.getOptions(connection, identity, document_id)

  @app.post('/api/transfers')
  def createTransfer(payload: CreateRequest, request: Request, identity=Depends(requireIdentity),
      connection=Depends(get_db), x_csrf_token: str = Header('', max_length=200)):
    security.check_session_csrf(request, x_csrf_token or payload.csrf_token)
    result = service.createTransfer(connection, identity, payload.document_id, payload.expected_version_id,
      payload.version_ids, payload.recipient_id, payload.reason)
    connection.commit()
    return result

  @app.post('/api/transfers/{request_id}/decision')
  def decideTransfer(request_id: ObjectId, payload: DecisionRequest, request: Request,
      identity=Depends(requireIdentity), connection=Depends(get_db), x_csrf_token: str = Header('', max_length=200)):
    security.check_session_csrf(request, x_csrf_token or payload.csrf_token)
    result = service.decideTransfer(connection, identity, request_id, payload.approve)
    connection.commit()
    return result

  @app.get('/transfers')
  def transfersPage(request: Request, after_id: AfterId = 0, identity=Depends(optionalIdentity), connection=Depends(get_db)):
    if identity is None:
      return RedirectResponse('/login', status_code=303)
    rows = service.listTransfers(connection, identity, after_id)
    return templates.TemplateResponse(request, 'transfers.html', {
      'me': publicIdentity(identity, request), 'transfers': rows})

  @app.get('/documents/{document_id}/transfer')
  def transferPage(document_id: ObjectId, request: Request, identity=Depends(optionalIdentity), connection=Depends(get_db)):
    if identity is None:
      return RedirectResponse('/login', status_code=303)
    options = service.getOptions(connection, identity, document_id)
    return templates.TemplateResponse(request, 'transfer_new.html', {
      'me': publicIdentity(identity, request), 'options': options})

  @app.post('/documents/{document_id}/transfer')
  def submitForm(document_id: ObjectId, request: Request, expected_version_id: FormId, recipient_id: FormId,
      version_ids: Annotated[list[int], Form(min_length=1, max_length=100)],
      reason: str = Form(..., min_length=1, max_length=500), csrf_token: str = Form('', max_length=200),
      identity=Depends(requireIdentity), connection=Depends(get_db)):
    security.check_session_csrf(request, csrf_token)
    if not reason.strip() or any(v <= 0 or v > 9223372036854775807 for v in version_ids):
      raise HTTPException(422, '請填寫交接原因並選擇有效版本')
    service.createTransfer(connection, identity, document_id, expected_version_id, version_ids, recipient_id, reason.strip())
    connection.commit()
    return RedirectResponse('/transfers', status_code=303)

  @app.post('/transfers/{request_id}/decision')
  def decisionForm(request_id: ObjectId, request: Request, action: Literal['approve', 'reject'] = Form(...),
      csrf_token: str = Form('', max_length=200), identity=Depends(requireIdentity), connection=Depends(get_db)):
    security.check_session_csrf(request, csrf_token)
    service.decideTransfer(connection, identity, request_id, action == 'approve')
    connection.commit()
    return RedirectResponse('/transfers', status_code=303)
