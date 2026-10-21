"""交接僅使用固定資料庫能力；路由負責提交交易。"""
import psycopg
from fastapi import HTTPException


def callTransfer(connection, name, identity, *arguments):
  values = (identity['tenant_id'], identity['id'], *arguments)
  placeholders = ','.join(['%s'] * len(values))
  try:
    row = connection.execute(f'SELECT app_core.{name}({placeholders}) AS result', values).fetchone()
  except psycopg.Error as error:
    status = {'P0002': 404, '40001': 409, '22023': 422}.get(error.sqlstate)
    if status is None:
      raise
    raise HTTPException(status, {404: '找不到交接申請或文件', 409: '交接狀態已變更，請重新提出',
      422: '請指定本公司有效帳號與正確欄位'}[status]) from None
  return row['result']


def getOptions(connection, identity, documentId):
  return callTransfer(connection, 'get_transfer_options', identity, documentId)


def createTransfer(connection, identity, documentId, expectedVersionId, versionIds, recipientId, reason):
  return callTransfer(connection, 'create_transfer', identity, documentId, expectedVersionId, versionIds, recipientId, reason)


def listTransfers(connection, identity, afterId=0):
  return callTransfer(connection, 'list_transfers', identity, afterId)


def decideTransfer(connection, identity, requestId, approve):
  return callTransfer(connection, 'decide_transfer', identity, requestId, approve)
