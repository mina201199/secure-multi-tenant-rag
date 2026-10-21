"""Real HTTP acceptance. Creates one fictional document and leaves it archived."""
import hashlib
import json
import os
import re
import secrets
import time

import httpx

from app.bootstrap import DEMO_PASSWORD
from app.db import connect


def main():
    base = os.getenv("SMOKE_BASE_URL", "http://web:8000")
    origin = os.getenv("APP_BASE_URL", "http://127.0.0.1:8008")
    clients = []
    checks = 0

    def check(response, status):
        nonlocal checks
        assert response.status_code == status, (response.request.url.path, response.status_code, response.text[:100])
        checks += 1
        return response

    def sign_in(name):
        client = httpx.Client(base_url=base, timeout=15, follow_redirects=False)
        clients.append(client)
        page = check(client.get("/login"), 200)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        check(client.post("/login", data={"username": name, "password": DEMO_PASSWORD, "csrf_token": csrf},
                          headers={"Origin": origin}), 303)
        return client

    def mutate(client, method, path, payload=None, status=200):
        csrf = check(client.get("/api/csrf"), 200).json()["csrf_token"]
        return check(client.request(method, path, json=payload,
                                    headers={"Origin": origin, "X-CSRF-Token": csrf}), status)

    doc_id = secrets.randbelow(10**9) + 10**9
    content = "第二階段驗收原文：本範例用於檢查版本、授權與交接。"
    with connect(os.environ["DATABASE_ADMIN_URL"]) as admin:
        admin.execute(
            "INSERT INTO app_core.documents(id,tenant_id,owner_id,title,category,status,current_version_id) "
            "VALUES(%s,1,103,'第二階段驗收範例','驗收資料','ready',%s)", (doc_id, doc_id),
        )
        admin.execute(
            "INSERT INTO app_core.document_versions(id,tenant_id,document_id,owner_id,content,source_hash,visibility,state) "
            "VALUES(%s,1,%s,103,%s,%s,'private','ready')",
            (doc_id, doc_id, content, hashlib.sha256(content.encode()).hexdigest()),
        )
    print(f"Created fictional acceptance document {doc_id}", flush=True)
    owner, reader, supervisor, recipient, foreign = [
        sign_in(name) for name in ("a.owner", "a.reader", "a.supervisor", "a.outsider", "b.supervisor")
    ]
    try:
        check(reader.get(f"/api/documents/{doc_id}"), 404)
        check(foreign.get(f"/api/documents/{doc_id}"), 404)
        mutate(owner, "PUT", f"/api/documents/{doc_id}/sharing",
               {"version_id": doc_id, "visibility": "selected", "user_ids": [104]})
        check(reader.get(f"/api/documents/{doc_id}"), 200)
        created = mutate(owner, "POST", f"/api/documents/{doc_id}/versions",
                         {"content": "第二階段驗收新版：新版應預設私人。"}, status=202).json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            job = check(owner.get(f"/api/jobs/{created['job_id']}"), 200).json()
            if job["status"] == "done":
                break
            assert job["status"] in ("queued", "running"), job
            time.sleep(0.2)
        else:
            raise AssertionError("Worker did not complete the version within 30 seconds")
        check(reader.get(f"/api/documents/{doc_id}"), 404)
        check(reader.get(f"/api/history/versions/{doc_id}"), 404)
        mutate(owner, "PUT", f"/api/history/versions/{doc_id}/permissions", {"user_ids": [104]})
        assert check(reader.get(f"/api/history/versions/{doc_id}"), 200).json()["content"] == content
        mutate(owner, "PUT", f"/api/documents/{doc_id}/sharing",
               {"version_id": created["version_id"], "visibility": "selected", "user_ids": [103, 104]})
        proposed = mutate(owner, "POST", "/api/transfers", {
            "recipient_id": 105, "reason": "第二階段驗收交接",
            "scope": [{"document_id": doc_id, "version_ids": [created["version_id"]]}],
        }, status=201).json()
        cases = check(supervisor.get("/api/transfers"), 200).json()
        case = next(row for row in cases if row["id"] == proposed["id"])
        assert set(case) == {"id", "applicant_id", "recipient_id", "reason", "status"}
        mutate(foreign, "POST", f"/api/transfers/{proposed['id']}/approve", status=404)
        mutate(supervisor, "POST", f"/api/transfers/{proposed['id']}/approve")
        mutate(supervisor, "POST", f"/api/transfers/{proposed['id']}/approve", status=409)
        check(owner.get(f"/manage/{doc_id}"), 404)
        check(recipient.get(f"/manage/{doc_id}"), 200)
        check(owner.get(f"/api/documents/{doc_id}"), 200)
        mutate(recipient, "PUT", f"/api/history/versions/{doc_id}/permissions", {"user_ids": [105]}, status=404)
        mutate(recipient, "PUT", f"/api/history/versions/{created['version_id']}/permissions", {"user_ids": [104]})
        mutate(recipient, "POST", f"/api/documents/{doc_id}/archive")
        for person in (owner, reader, recipient):
            for path in (f"/api/documents/{doc_id}", f"/documents/{doc_id}", f"/api/documents/{doc_id}/download"):
                check(person.get(path), 404)
        check(reader.get(f"/api/history/versions/{doc_id}"), 200)
        check(reader.get(f"/api/history/versions/{created['version_id']}"), 200)
        with connect(os.environ["DATABASE_ADMIN_URL"]) as admin:
            events = admin.execute(
                "SELECT count(*) AS n FROM app_core.audit_events WHERE document_id=%s", (doc_id,)
            ).fetchone()["n"]
            assert events >= 10
        print(json.dumps({"result": "PASS", "http_checks": checks, "document_id": doc_id,
                          "new_version_id": created["version_id"], "transfer_id": proposed["id"],
                          "audit_events": events, "final_state": "archived"}, ensure_ascii=False), flush=True)
    finally:
        for client in clients:
            try:
                csrf = client.get("/api/csrf").json().get("csrf_token")
                if csrf:
                    client.post("/logout", data={"csrf_token": csrf}, headers={"Origin": origin})
            finally:
                client.close()


if __name__ == "__main__":
    main()
