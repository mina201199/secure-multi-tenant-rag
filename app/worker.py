"""使用專用 DB 登入處理 TXT；無 web、owner 或外部模型憑證。"""
import argparse
import os
import signal
from threading import Event

import psycopg
from psycopg.conninfo import conninfo_to_dict
from fastapi import HTTPException
from psycopg.rows import dict_row

from app import version_service


def connectWorker(dsn):
  if not conninfo_to_dict(dsn).get('password'):
    raise ValueError('worker requires a dedicated password')
  connection = psycopg.connect(dsn, connect_timeout=5, row_factory=dict_row,
    options='-c statement_timeout=30000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=30000')
  try:
    role = connection.execute('''SELECT session_user AS session_name, current_user AS current_name,
      rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolinherit,
      EXISTS (SELECT FROM pg_auth_members WHERE member = pg_roles.oid) AS membership
      FROM pg_roles WHERE rolname = current_user''').fetchone()
    if (role['session_name'] != 'rag_worker' or role['current_name'] != 'rag_worker'
        or any(role[key] for key in ('rolsuper', 'rolbypassrls', 'rolcreaterole',
          'rolcreatedb', 'rolreplication', 'rolinherit', 'membership'))):
      raise ValueError('worker requires its restricted login')
    connection.commit()
    return connection
  except BaseException:
    connection.close()
    raise


def runOnce(connection, stopEvent=None):
  if stopEvent is not None and stopEvent.is_set():
    return False
  recovered = version_service.recover_job(connection)
  connection.commit()
  if stopEvent is not None and stopEvent.is_set():
    return recovered is not None
  job = version_service.claim_job(connection)
  # 領取必須先提交；中途斷線才有持久化租約可供下一個程序恢復。
  connection.commit()
  if job is None:
    return recovered is not None
  version_service.complete_job(connection, job['job_id'], job['lease_token'])
  connection.commit()
  return True


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--once', action='store_true', help='執行一輪恢復及處理後退出')
  args = parser.parse_args()
  dsn = os.getenv('WORKER_DATABASE_URL')
  if not dsn:
    print('worker_configuration_error', flush=True)
    return 2
  stop = Event()
  for signum in (signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, lambda *_: stop.set())
  announced = False
  while not stop.is_set():
    try:
      with connectWorker(dsn) as connection:
        if not announced:
          print('worker_started', flush=True)
          announced = True
        while not stop.is_set():
          worked = runOnce(connection, stop)
          if worked:
            print('worker_cycle_completed', flush=True)
          if args.once:
            return 0
          if not worked:
            stop.wait(2)
    except ValueError:
      print('worker_configuration_error', flush=True)
      return 2
    except (psycopg.Error, HTTPException):
      # 不輸出 driver 例外；它可能包含連線字串、SQL 參數或文件內容。
      # 未提交的交易已回滾，已領取工作則留給租約回收，避免重複發布。
      print('worker_database_retry', flush=True)
      if args.once:
        return 1
      stop.wait(5)
  print('worker_stopped', flush=True)
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
