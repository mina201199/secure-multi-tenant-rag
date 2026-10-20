import os

import psycopg
import pytest
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from app import bootstrap
from app.main import app
from tests.helpers import BASE_URL


@pytest.fixture(scope="session", autouse=True)
def initialized_database():
    # 測試庫是 tmpfs，每次重建容器都是空的；用與正式流程相同的 bootstrap 建表與 seed
    bootstrap.main()


@pytest.fixture(scope="session", autouse=True)
def running_app(initialized_database):
    # 進入 with 才會執行 lifespan（讀設定、開連線池），整個測試期間只開一次
    with TestClient(app):
        yield app


@pytest.fixture
def client(running_app):
    # 每個測試一個新的 client，Cookie 不會在測試之間互相影響
    return TestClient(running_app, base_url=BASE_URL)


@pytest.fixture
def admin_db():
    # 以 owner 身分直接改資料（模擬過期、停用），只在測試中使用
    with psycopg.connect(os.environ["DATABASE_ADMIN_URL"], autocommit=True) as connection:
        yield connection


@pytest.fixture
def runtime_db():
    # 與 app 的連線池相同設定（dict_row），直接呼叫 app 函式時行為才一致
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as connection:
        yield connection
