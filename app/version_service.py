"""Thin calls to the database's restricted, transactional TXT capabilities.

The caller commits successful writes, including a processor result of ``failed``.
The separate app.worker process consumes these capabilities; web never publishes.
"""
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Jsonb


def _call(connection, name, arguments=()):
    # Names come only from the fixed wrappers below, never from request input.
    placeholders = ', '.join(['%s'] * len(arguments))
    try:
        row = connection.execute(
            f'SELECT app_core.{name}({placeholders}) AS result', arguments,
        ).fetchone()
    except psycopg.Error as error:
        status = {'P0002': 404, '40001': 409, '22023': 422, '54000': 413}.get(error.sqlstate)
        if status is None:
            raise
        messages = {404: '找不到文件', 409: '版本或工作狀態已變更',
                    422: '請提供有效的 TXT／PDF 與請求欄位', 413: '檔案或文字超過容量限制'}
        raise HTTPException(status, messages[status]) from None
    return row['result'] if isinstance(row, dict) else row[0]


def get_version_management(connection, identity, document_id):
    return _call(connection, 'get_version_management',
                 (identity['tenant_id'], identity['id'], document_id))


def create_version(connection, identity, document_id, expected_version_id, source_bytes, idempotency_key, source_format='txt'):
    arguments = (identity['tenant_id'], identity['id'], document_id, expected_version_id, source_bytes, idempotency_key)
    return _call(connection, 'create_version', arguments if source_format == 'txt' else arguments + (source_format,))


def _uuid(value, status=409):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status, '找不到工作' if status == 404 else '版本或工作狀態已變更') from None


def get_job(connection, identity, job_id):
    result = _call(connection, 'get_job',
                   (identity['tenant_id'], identity['id'], _uuid(job_id, 404)))
    if result is None:
        raise HTTPException(404, '找不到工作')
    return result


def claim_job(connection):
    result = _call(connection, 'claim_version_job')
    if result is not None:
        result['source_bytes'] = bytes.fromhex(result['source_bytes'])
    return result


def complete_job(connection, job_id, lease_token):
    return _call(connection, 'complete_version_job', (_uuid(job_id), _uuid(lease_token)))


def fail_job(connection, job_id, lease_token):
    return _call(connection, 'fail_version_job', (_uuid(job_id), _uuid(lease_token)))


def recover_job(connection):
  return _call(connection, 'recover_version_job')


def complete_pdf_job(connection, job_id, lease_token, source_sha256, pages):
  return _call(connection, 'complete_pdf_version_job',
    (_uuid(job_id), _uuid(lease_token), source_sha256, Jsonb(pages)))


def fail_pdf_job(connection, job_id, lease_token, error):
  return _call(connection, 'fail_pdf_version_job', (_uuid(job_id), _uuid(lease_token), error))
