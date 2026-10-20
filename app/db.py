from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def create_pool(dsn):
    # 用連線池取代每個 request 都重新連線；open=False 讓啟動流程決定何時真正連上資料庫
    return ConnectionPool(
        dsn,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row, "connect_timeout": 5},
    )


def get_db(request: Request):
    # 一個 request 使用一條連線、一個交易：正常結束時 commit，發生例外時 rollback。
    # 連線會被下一個 request 重用，所以 M2 加 RLS 時只能用 SET LOCAL 設定租戶情境，
    # 不能用 session 層級的 SET，否則下一位使用者可能沿用上一家公司的身分。
    with request.app.state.pool.connection() as connection:
        yield connection
