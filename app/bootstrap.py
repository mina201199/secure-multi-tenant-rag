"""資料庫初始化：建立 runtime 角色、套用 migration、寫入兩家虛構公司的 seed。

只由 Compose 的 setup 服務（及測試）以資料庫 owner 執行；web 永遠拿不到 owner 憑證。
可以重複執行：已套用的 migration 會跳過，已存在的公司不會重寫資料。
"""
import os
from pathlib import Path

import psycopg
from psycopg import sql

from app.security import PASSWORD_HASHER, normalize_username

RUNTIME_ROLE = "rag_app"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# 任意固定數字；讓同時啟動的多個 setup 依序執行，不會重複建角色或重複套用 migration
BOOTSTRAP_LOCK_ID = 7319001


def ensure_runtime_role(connection, password):
    exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (RUNTIME_ROLE,)
    ).fetchone()
    if not exists:
        connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(RUNTIME_ROLE)))
    # 每次都重設屬性與密碼：既能套用 .env 的新密碼，也確保角色不會被手動改成可繞過權限
    connection.execute(
        sql.SQL(
            "ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS PASSWORD {}"
        ).format(sql.Identifier(RUNTIME_ROLE), sql.Literal(password))
    )


def apply_migrations(connection):
    connection.execute("CREATE SCHEMA IF NOT EXISTS app_core")
    # 版本紀錄表不授權給 runtime，web 看不到也改不了 schema 版本
    connection.execute(
        """CREATE TABLE IF NOT EXISTS app_core.schema_migrations (
               version text PRIMARY KEY,
               applied_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    applied = {
        row[0] for row in connection.execute("SELECT version FROM app_core.schema_migrations")
    }
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.stem in applied:
            continue
        connection.execute(path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO app_core.schema_migrations (version) VALUES (%s)", (path.stem,)
        )
        print(f"已套用 migration：{path.stem}")


def ensure_worker_role(connection, password):
  # 沒設定專用密碼就保持 NOLOGIN，避免默默沿用 web 或 owner 憑證。
  if not password:
    connection.execute('ALTER ROLE rag_worker NOLOGIN PASSWORD NULL')
    return
  connection.execute(sql.SQL(
    'ALTER ROLE rag_worker WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB '
    'NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}'
  ).format(sql.Literal(password)))


def demo_tenants():
    """兩家公司使用相同標題、不同內容，才能測出「別家文件更相近也不能看到」。"""
    tenants = []
    for name, prefix, allowance, part_spec in (
        ("甲公司", "a", "1,500", "M8x30"),
        ("乙公司", "b", "3,000", "M10x40"),
    ):
        tenants.append({
            "name": name,
            # (帳號後綴, 顯示名稱, 角色, 主管帳號後綴, 是否啟用)
            "users": [
                ("admin", f"{name}管理員", "admin", None, True),
                ("manager", f"{name}主管", "employee", None, True),
                ("owner", f"{name}文件擁有者", "employee", "manager", True),
                ("reader", f"{name}獲授權員工", "employee", "manager", True),
                ("staff", f"{name}一般員工", "employee", "manager", True),
                ("inactive", f"{name}已停用員工", "employee", "manager", False),
            ],
            "prefix": prefix,
            # (標題, 分類, 可見範圍, 狀態, 內容, 額外授權的帳號後綴)
            "documents": [
                (
                    "出差補助規定", "作業規定", "company", "ready",
                    f"{name}員工國內出差，每日交通補助上限為 NT${allowance}，須於返回後 7 日內檢附單據申請。",
                    [],
                ),
                (
                    "料件清冊（試行）", "料件清冊", "private", "ready",
                    f"料號 {prefix.upper()}-1001｜品名：六角螺栓｜規格：{part_spec}｜單位：支",
                    ["reader"],
                ),
                (
                    "主管會議紀錄", "會議紀錄", "private", "ready",
                    f"{name}內部會議紀錄：下季採購預算待確認，僅限文件擁有者閱讀。",
                    [],
                ),
                (
                    "舊版出差補助規定", "作業規定", "company", "archived",
                    f"{name}已封存的舊規定：每日交通補助上限為 NT$800。",
                    [],
                ),
            ],
        })
    return tenants


def seed_demo_data(connection, password):
    password_hash = PASSWORD_HASHER.hash(password)
    for tenant in demo_tenants():
        row = connection.execute(
            "INSERT INTO app_core.tenants (name) VALUES (%s) "
            "ON CONFLICT (name) DO NOTHING RETURNING id",
            (tenant["name"],),
        ).fetchone()
        # 公司已存在代表先前已 seed 過；不覆寫，以免蓋掉組員手動調整的帳號狀態
        if row is None:
            continue
        tenant_id = row[0]
        user_ids = {}
        # users 清單已把主管排在部屬前面，插入時主管 ID 一定已存在
        for suffix, display_name, role, manager_suffix, is_active in tenant["users"]:
            username = normalize_username(f"{tenant['prefix']}.{suffix}")
            user_ids[suffix] = connection.execute(
                """INSERT INTO app_core.users
                   (tenant_id, username, display_name, password_hash, role, is_active, manager_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (tenant_id, username, display_name, password_hash, role, is_active,
                 user_ids.get(manager_suffix)),
            ).fetchone()[0]
        for title, category, visibility, status, content, readers in tenant["documents"]:
            document_id = connection.execute(
                """INSERT INTO app_core.documents
                   (tenant_id, title, category, owner_id, status)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (tenant_id, title, category, user_ids["owner"], status),
            ).fetchone()[0]
            version_id = connection.execute(
                """INSERT INTO app_core.document_versions
                   (tenant_id, document_id, version_number, owner_id, visibility, status, content)
                   VALUES (%s, %s, 1, %s, %s, %s, %s) RETURNING id""",
                (tenant_id, document_id, user_ids["owner"], visibility, status, content),
            ).fetchone()[0]
            connection.execute(
                "UPDATE app_core.documents SET current_version_id = %s WHERE id = %s",
                (version_id, document_id),
            )
            for reader in readers:
                connection.execute(
                    "INSERT INTO app_core.version_readers (tenant_id, version_id, user_id) "
                    "VALUES (%s, %s, %s)",
                    (tenant_id, version_id, user_ids[reader]),
                )
        print(f"已建立 seed：{tenant['name']}")


def main():
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    runtime_password = os.environ["RUNTIME_DB_PASSWORD"]
    seed_password = os.getenv("SEED_USER_PASSWORD")
    # 整個初始化在同一個交易內，任何一步失敗都全部回滾，不會留下半套 schema
    with psycopg.connect(admin_url, connect_timeout=5) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (BOOTSTRAP_LOCK_ID,))
        ensure_runtime_role(connection, runtime_password)
        apply_migrations(connection)
        ensure_worker_role(connection, os.getenv('WORKER_DB_PASSWORD'))
        # 沒設定 seed 密碼就不建立示範帳號，避免正式環境意外出現共用密碼帳號
        if seed_password:
            seed_demo_data(connection, seed_password)
    print("bootstrap 完成")


if __name__ == "__main__":
    main()
