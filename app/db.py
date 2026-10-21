import psycopg
from psycopg.rows import dict_row
from fastapi import Request
import hashlib
import hmac


def connect(dsn):
    return psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)


def get_db(request: Request):
    # A single request uses one connection/transaction. Exceptions roll it back.
    with connect(request.app.state.settings.database_url) as connection:
        yield connection


def bind_identity(connection, identity, token_hash, secret):
    """Signed, transaction-local context. Arbitrary tenant/user GUCs are not trusted."""
    material = f"{identity['id']}:{identity['tenant_id']}:{token_hash}"
    mac = hmac.new(secret.encode(), ("db-context:" + material).encode(), hashlib.sha256).hexdigest()
    for key, value in (
        ("app.actor_id", str(identity["id"])), ("app.tenant_id", str(identity["tenant_id"])),
        ("app.session_hash", token_hash), ("app.context_signature", mac), ("app.mode", "current"),
    ):
        connection.execute("SELECT set_config(%s,%s,true)", (key, value))
