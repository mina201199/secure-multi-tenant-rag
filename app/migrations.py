import hashlib
from pathlib import Path


def migrate(connection):
    connection.execute(
        "CREATE TABLE IF NOT EXISTS app_core.schema_migrations "
        "(name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    for path in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
        source = path.read_bytes()
        checksum = hashlib.sha256(source).hexdigest()
        applied = connection.execute(
            "SELECT checksum FROM app_core.schema_migrations WHERE name=%s", (path.name,)
        ).fetchone()
        if applied:
            if applied["checksum"] != checksum:
                raise RuntimeError(f"Applied migration changed: {path.name}")
            continue
        connection.execute(source.decode("utf-8"))
        connection.execute(
            "INSERT INTO app_core.schema_migrations(name,checksum) VALUES (%s,%s)",
            (path.name, checksum),
        )
