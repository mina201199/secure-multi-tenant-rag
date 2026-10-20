import psycopg
from psycopg.rows import dict_row
from fastapi import Request


def connect(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)


def get_db(request: Request):
    # A single request uses one connection/transaction. Exceptions roll it back.
    with connect(request.app.state.settings.database_url) as connection:
        yield connection
