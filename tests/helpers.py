import os
import re

BASE_URL = os.environ["APP_BASE_URL"]
SEED_PASSWORD = os.environ["SEED_USER_PASSWORD"]
CSRF_FIELD = re.compile(r'name="csrf_token" value="([^"]+)"')


def login_csrf(client):
    page = client.get("/login")
    return CSRF_FIELD.search(page.text).group(1)


def login(client, username, password=SEED_PASSWORD, headers=None):
    token = login_csrf(client)
    # 模擬瀏覽器從本站送出表單：帶上本站 Origin
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        headers={"Origin": BASE_URL} if headers is None else headers,
        follow_redirects=False,
    )


def session_cookie(client):
    return client.cookies.get("rag_session")
