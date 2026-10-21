"""固定歷史能力呼叫；路由在交付正文前提交稽核交易。"""
import psycopg
from fastapi import HTTPException


def callHistory(connection, name, identity, *arguments):
  values = (identity['tenant_id'], identity['id'], *arguments)
  placeholders = ','.join(['%s'] * len(values))
  try:
    # name 僅由下方固定 wrapper 提供，不接受 HTTP 輸入。
    row = connection.execute(f'SELECT app_core.{name}({placeholders}) AS result', values).fetchone()
  except psycopg.Error as error:
    status = {'P0002': 404, '40001': 409, '22023': 422}.get(error.sqlstate)
    if status is None:
      raise
    raise HTTPException(status, {404: '找不到歷史版本', 409: '版本狀態已變更，請重新載入',
      422: '請指定本公司有效帳號與正確欄位'}[status]) from None
  return row['result']


def listVersions(connection, identity, afterId=0):
  return callHistory(connection, 'list_history_versions', identity, afterId)


def getAccess(connection, identity, versionId):
  return callHistory(connection, 'get_history_access', identity, versionId)


def setAccess(connection, identity, versionId, readerId, granted):
  return callHistory(connection, 'set_history_access', identity, versionId, readerId, granted)


def readVersion(connection, identity, versionId):
  return callHistory(connection, 'read_history_version', identity, versionId)
