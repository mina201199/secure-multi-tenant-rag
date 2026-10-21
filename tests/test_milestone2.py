import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.config import Settings
from app.db import connect, bind_identity
from app.security import hash_token, read_identity, SESSION_COOKIE
from app.worker import run_once
from tests.test_milestone1 import login


@pytest.fixture
def account(client):
    clients = []
    def make(username):
        other = TestClient(client.app, base_url="http://127.0.0.1:8008")
        other.__enter__()
        clients.append(other)
        assert login(other, username).status_code == 303
        return other
    yield make
    for other in clients:
        other.__exit__(None, None, None)


def call(client, method, path, payload=None):
    token = client.get("/api/csrf").json()["csrf_token"]
    return client.request(method, path, json=payload,
                          headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:8008"})


def sharing(client, doc=1003, version=1003, visibility="selected", users=None):
    return call(client, "PUT", f"/api/documents/{doc}/sharing",
                {"version_id": version, "visibility": visibility, "user_ids": users or []})


def new_version(client, content="第二版內容", doc=1001):
    response = call(client, "POST", f"/api/documents/{doc}/versions", {"content": content})
    assert response.status_code == 202, response.text
    return response.json()


def grant(client, version, users):
    return call(client, "PUT", f"/api/history/versions/{version}/permissions", {"user_ids": users})


def transfer(client, docs=None, recipient=105, reason="工作交接"):
    response = call(client, "POST", "/api/transfers", {
        "recipient_id": recipient, "reason": reason,
        "scope": docs or [{"document_id": 1003, "version_ids": [1003]}],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def process():
    return run_once(os.environ["WORKER_DATABASE_URL"])


def bound(client):
    connection = connect(os.environ["DATABASE_URL"])
    assert read_identity(connection, client.cookies[SESSION_COOKIE], Settings.from_env())
    return connection


def test_share_and_revoke_effective_next_request(account, admin):
    owner, reader, outsider = (account("a."+name) for name in ("owner", "reader", "outsider"))
    assert reader.get("/api/documents/1003").status_code == 200
    assert sharing(owner, users=[105]).status_code == 200
    assert reader.get("/api/documents/1003").status_code == 404
    assert outsider.get("/api/documents/1003").status_code == 200
    assert sharing(owner, visibility="private").status_code == 200
    assert outsider.get("/api/documents/1003").status_code == 404
    assert sharing(owner, visibility="company").status_code == 200
    assert reader.get("/api/documents/1003").status_code == 200
    assert account("b.owner").get("/api/documents/1003").status_code == 404
    assert admin.execute("SELECT count(*) AS n FROM app_core.audit_events WHERE action='sharing'").fetchone()["n"] == 3


@pytest.mark.parametrize("username", ["a.reader", "a.admin", "a.supervisor", "a.outsider", "b.owner"])
def test_nonowners_cannot_mutate_or_inspect_management(account, username):
    other = account(username)
    assert sharing(other).status_code == 404
    assert call(other, "POST", "/api/documents/1003/versions", {"content": "forged"}).status_code == 404
    assert call(other, "POST", "/api/documents/1003/archive").status_code == 404
    assert other.get("/api/documents/1003/versions").status_code == 404
    assert other.get("/manage/1003").status_code == 404
    assert grant(other, 1003, [104]).status_code == 404


@pytest.mark.parametrize("ids", [[204], [999999], [104, 204]])
def test_share_rejects_invalid_targets_atomically(account, ids):
    owner, reader = account("a.owner"), account("a.reader")
    assert sharing(owner, users=ids).status_code == 422
    assert reader.get("/api/documents/1003").status_code == 200


def test_disabled_target_and_stale_version_rejected(account, admin):
    owner = account("a.owner")
    admin.execute("UPDATE app_core.users SET is_active=false WHERE id=104")
    admin.commit()
    assert sharing(owner, users=[104]).status_code == 422
    version = new_version(owner)
    assert process()
    assert sharing(owner, doc=1001, version=1001, visibility="company").status_code == 409
    assert sharing(owner, doc=1001, version=version["version_id"], visibility="company").status_code == 200


def test_version_activation_is_private_and_no_old_fallback(account, admin):
    owner, reader = account("a.owner"), account("a.reader")
    old = reader.get("/api/documents/1001").json()["content"]
    version = new_version(owner, "新版補助 NT$9,999")
    assert reader.get("/api/documents/1001").json()["content"] == old
    assert owner.get(f"/api/jobs/{version['job_id']}").json()["status"] == "queued"
    assert reader.get(f"/api/jobs/{version['job_id']}").status_code == 404
    assert process()
    assert owner.get("/api/documents/1001").json()["content"] == "新版補助 NT$9,999"
    assert reader.get("/api/documents/1001").status_code == 404
    assert reader.get("/api/documents/1001/download").status_code == 404
    assert reader.get("/documents/1001?version_id=1001").status_code == 404
    assert reader.get("/api/history/versions/1001").status_code == 404
    with bound(reader) as runtime:
        assert runtime.execute("SELECT * FROM app_core.document_versions WHERE document_id=1001").fetchall() == []
    assert admin.execute("SELECT content FROM app_core.document_versions WHERE id=1001").fetchone()["content"] == old
    assert admin.execute("SELECT count(*) AS n FROM app_core.version_readers WHERE version_id=%s",
                         (version["version_id"],)).fetchone()["n"] == 0
    assert sharing(owner, doc=1001, version=version["version_id"], visibility="company").status_code == 200
    assert reader.get("/api/documents/1001").json()["content"] == "新版補助 NT$9,999"


def test_failed_version_keeps_old_and_retries_are_bounded(account):
    owner = account("a.owner")
    version = new_version(owner, "   ")
    for attempt in range(1, 4):
        assert process()
        assert "1,500" in owner.get("/api/documents/1001").json()["content"]
        job = owner.get(f"/api/jobs/{version['job_id']}").json()
        assert (job["status"], job["attempts"]) == ("failed", attempt)
        result = call(owner, "POST", f"/api/jobs/{version['job_id']}/retry")
        assert result.status_code == (200 if attempt < 3 else 409)
    assert not process()


def test_history_requires_explicit_permission_and_audits_each_read(account, admin):
    owner, reader, supervisor = account("a.owner"), account("a.reader"), account("a.supervisor")
    new_version(owner)
    process()
    for person in (owner, reader, supervisor, account("a.admin"), account("b.owner")):
        assert person.get("/api/history/versions/1001").status_code == 404
    assert grant(owner, 1001, [103,104]).status_code == 200
    assert {v["id"] for v in reader.get("/api/history").json()} == {1001}
    assert reader.get("/api/history/versions/1001").status_code == 200
    assert reader.get("/history/1001").status_code == 200
    assert owner.get("/api/history/versions/1001").status_code == 200
    events = admin.execute("SELECT actor_id,version_id FROM app_core.audit_events WHERE action='history_read'").fetchall()
    assert len(events) == 3 and {e["version_id"] for e in events} == {1001}
    assert grant(owner, 1001, [103]).status_code == 200
    assert reader.get("/api/history/versions/1001").status_code == 404
    assert reader.get("/api/history").json() == []


def test_history_grants_cross_company_and_disabled_rejected(account, admin):
    owner = account("a.owner")
    assert grant(owner, 1001, [204]).status_code == 422
    admin.execute("UPDATE app_core.users SET is_active=false WHERE id=104")
    admin.commit()
    assert grant(owner, 1001, [104]).status_code == 422
    assert grant(owner, 2001, [103]).status_code == 404


def test_archive_blocks_frontend_download_and_running_worker(account, admin):
    owner, reader = account("a.owner"), account("a.reader")
    assert grant(owner, 1001, [104]).status_code == 200
    version = new_version(owner)
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        lease = worker.execute("SELECT * FROM app_core.claim_job()").fetchone()
    assert call(owner, "POST", "/api/documents/1001/archive").status_code == 200
    for person in (owner, reader):
        for path in ("/documents/1001", "/api/documents/1001", "/api/documents/1001/download"):
            assert person.get(path).status_code == 404
    with connect(os.environ["WORKER_DATABASE_URL"]) as worker:
        assert not worker.execute("SELECT app_core.finish_job(%s,%s,true) AS ok",
                                  (lease["job_id"], lease["lease_token"])).fetchone()["ok"]
    assert reader.get("/api/history/versions/1001").status_code == 200
    assert owner.get("/api/history/versions/1001").status_code == 404
    assert call(owner, "POST", f"/api/jobs/{version['job_id']}/retry").status_code == 409
    assert admin.execute("SELECT count(*) AS n FROM app_core.document_versions WHERE document_id=1001").fetchone()["n"] == 2
    assert admin.execute("SELECT status FROM app_core.documents WHERE id=1001").fetchone()["status"] == "archived"


def test_transfer_minimal_supervisor_view_and_existing_acl_preserved(account, admin):
    owner, supervisor, recipient = account("a.owner"), account("a.supervisor"), account("a.outsider")
    assert sharing(owner, users=[103,104]).status_code == 200
    tid = transfer(owner)
    cases = supervisor.get("/api/transfers").json()
    assert cases == [{"id": tid, "applicant_id": 103, "recipient_id": 105, "reason": "工作交接", "status": "pending"}]
    page = supervisor.get("/transfers")
    assert page.status_code == 200 and "指定分享作業規定" not in page.text
    assert "領料" not in page.text
    with bound(supervisor) as runtime:
        assert runtime.execute("SELECT * FROM app_core.transfer_documents").fetchall() == []
        assert runtime.execute("SELECT * FROM app_core.transfer_versions").fetchall() == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime.execute("SELECT * FROM app_core.audit_events")
        runtime.rollback()
    for person in (owner, recipient, account("a.admin"), account("b.supervisor")):
        assert call(person, "POST", f"/api/transfers/{tid}/approve").status_code == 404
    assert call(supervisor, "POST", f"/api/transfers/{tid}/approve").status_code == 200
    assert call(supervisor, "POST", f"/api/transfers/{tid}/approve").status_code == 409
    assert supervisor.get("/api/transfers").json() == []
    assert owner.get("/api/documents/1003").status_code == 200  # Explicit ACL survives.
    assert owner.get("/manage/1003").status_code == 404
    assert sharing(owner).status_code == 404
    assert recipient.get("/manage/1003").status_code == 200
    assert account("a.reader").get("/api/documents/1003").status_code == 200
    assert admin.execute("SELECT owner_id FROM app_core.document_versions WHERE id=1003").fetchone()["owner_id"] == 105


@pytest.mark.parametrize("recipient", [102,103,204,999999])
def test_invalid_transfer_recipient(account, recipient):
    owner = account("a.owner")
    result = call(owner, "POST", "/api/transfers", {
        "recipient_id": recipient, "reason": "交接", "scope": [{"document_id":1003,"version_ids":[1003]}]})
    assert result.status_code == 422


@pytest.mark.parametrize("scope", [
    [{"document_id":2001,"version_ids":[2001]}],
    [{"document_id":1003,"version_ids":[2003]}],
    [{"document_id":1001,"version_ids":[1003]}],
    [{"document_id":1003,"version_ids":[]}],
])
def test_transfer_rejects_cross_scope(account, scope):
    response = call(account("a.owner"), "POST", "/api/transfers",
                    {"recipient_id":105,"reason":"交接","scope":scope})
    assert response.status_code in (404,422)


def test_transfer_preserves_unselected_historical_ownership(account, admin):
    owner, supervisor, recipient = account("a.owner"), account("a.supervisor"), account("a.outsider")
    version = new_version(owner)
    process()
    tid = transfer(owner, [{"document_id":1001,"version_ids":[version["version_id"]]}])
    assert call(supervisor, "POST", f"/api/transfers/{tid}/approve").status_code == 200
    assert grant(owner, 1001, [103]).status_code == 200
    assert grant(recipient, 1001, [105]).status_code == 404
    assert owner.get("/api/history/versions/1001").status_code == 200
    assert owner.get("/api/documents/1001").status_code == 404  # New private version moved.
    assert admin.execute("SELECT owner_id FROM app_core.document_versions WHERE id=1001").fetchone()["owner_id"] == 103
    assert recipient.get("/api/documents/1001").status_code == 200


def test_transfer_explicitly_included_history_moves_with_permissions(account):
    owner, supervisor, recipient, reader = (account("a."+n) for n in ("owner","supervisor","outsider","reader"))
    version = new_version(owner)
    process()
    assert grant(owner,1001,[104]).status_code == 200
    tid = transfer(owner,[{"document_id":1001,"version_ids":[1001,version["version_id"]]}])
    assert call(supervisor,"POST",f"/api/transfers/{tid}/approve").status_code == 200
    assert grant(owner,1001,[103]).status_code == 404
    assert reader.get("/api/history/versions/1001").status_code == 200
    assert grant(recipient,1001,[105]).status_code == 200
    assert reader.get("/api/history/versions/1001").status_code == 404


@pytest.mark.parametrize("mutation", [
    "UPDATE app_core.users SET manager_id=101 WHERE id=103",
    "UPDATE app_core.users SET is_active=false WHERE id=103",
    "UPDATE app_core.users SET is_active=false WHERE id=105",
    "UPDATE app_core.documents SET owner_id=104 WHERE id=1003",
    "UPDATE app_core.documents SET status='archived' WHERE id=1003",
    "UPDATE app_core.documents SET revision=revision+1 WHERE id=1003",
    "UPDATE app_core.transfer_requests SET expires_at=now()-interval '1 second'",
])
def test_stale_transfer_never_partially_applies(account, admin, mutation):
    owner, supervisor = account("a.owner"), account("a.supervisor")
    tid = transfer(owner, [{"document_id":1001,"version_ids":[1001]},{"document_id":1003,"version_ids":[1003]}])
    admin.execute(mutation)
    admin.commit()
    assert call(supervisor,"POST",f"/api/transfers/{tid}/approve").status_code == 409
    assert admin.execute("SELECT owner_id FROM app_core.documents WHERE id=1001").fetchone()["owner_id"] == 103
    assert admin.execute("SELECT owner_id FROM app_core.document_versions WHERE id=1001").fetchone()["owner_id"] == 103
    assert admin.execute("SELECT status FROM app_core.transfer_requests WHERE id=%s",(tid,)).fetchone()["status"] == "pending"


def test_reject_and_account_disable(account, admin):
    owner, supervisor, administrator = account("a.owner"), account("a.supervisor"), account("a.admin")
    tid = transfer(owner)
    assert call(supervisor,"POST",f"/api/transfers/{tid}/reject").status_code == 200
    assert call(supervisor,"POST",f"/api/transfers/{tid}/approve").status_code == 409
    assert call(owner,"PUT","/api/admin/users/105/active",{"is_active":False}).status_code == 403
    assert call(administrator,"PUT","/api/admin/users/205/active",{"is_active":False}).status_code == 404
    assert call(administrator,"PUT","/api/admin/users/103/active",{"is_active":False,"role":"admin"}).status_code == 422
    assert call(administrator,"PUT","/api/admin/users/103/active",{"is_active":False}).status_code == 200
    assert owner.get("/api/documents").status_code == 401
    assert administrator.get("/api/documents/1002").status_code == 404
    assert administrator.get("/api/manage/documents").json() == []
    assert call(administrator,"PUT","/api/admin/users/103/active",{"is_active":True}).status_code == 200
    assert owner.get("/api/documents").status_code == 401  # Old session stays revoked.
    assert admin.execute("SELECT owner_id FROM app_core.documents WHERE id=1002").fetchone()["owner_id"] == 103


@pytest.mark.parametrize("separate_requests", [False,True])
def test_concurrent_approvals_apply_once(account, admin, separate_requests):
    owner, supervisor = account("a.owner"), account("a.supervisor")
    scope=[{"document_id":1001,"version_ids":[1001]},{"document_id":1003,"version_ids":[1003]}]
    first=transfer(owner,scope)
    second=transfer(owner,list(reversed(scope)),recipient=104) if separate_requests else first
    gate=Barrier(2)
    raw=supervisor.cookies[SESSION_COOKIE]
    def approve(tid):
        try:
            with connect(os.environ["DATABASE_URL"]) as connection:
                read_identity(connection,raw,Settings.from_env())
                # Resolve before the gate, then release the session row lock;
                # rebind signed context in the next transaction.
                connection.commit()
                bind_identity(connection,{"id":102,"tenant_id":1},hash_token(raw),Settings.from_env().csrf_secret)
                gate.wait(timeout=10)
                connection.execute("SET LOCAL statement_timeout='10s'")
                connection.execute("SELECT app_core.change('approve',%s)",(Jsonb({"transfer_id":tid}),))
            return "ok"
        except psycopg.Error as error:
            return error.sqlstate
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(approve,[first,second]))
    assert sorted(outcomes)==["P0001","ok"]
    owners={r["owner_id"] for r in admin.execute("SELECT owner_id FROM app_core.documents WHERE id IN (1001,1003)").fetchall()}
    assert len(owners)==1 and owners <= {104,105}


@pytest.mark.parametrize("method,path,payload", [
    ("PUT","/api/documents/1003/sharing",{"version_id":1003,"visibility":"private","user_ids":[]}),
    ("POST","/api/documents/1003/versions",{"content":"test"}),
    ("POST","/api/documents/1003/archive",None),
    ("PUT","/api/history/versions/1003/permissions",{"user_ids":[104]}),
    ("POST","/api/transfers",{"recipient_id":105,"reason":"test","scope":[{"document_id":1003,"version_ids":[1003]}]}),
    ("POST","/api/transfers/999/approve",None),
    ("POST","/api/transfers/999/reject",None),
    ("PUT","/api/admin/users/105/active",{"is_active":False}),
])
def test_all_mutations_reject_missing_csrf(account, method, path, payload):
    assert account("a.owner").request(method,path,json=payload).status_code==403


def test_active_version_is_not_a_history_backdoor(account):
    owner,reader=account("a.owner"),account("a.reader")
    assert grant(owner,1002,[104]).status_code==200
    assert reader.get("/api/history/versions/1002").status_code==404
    assert reader.get("/api/documents/1002").status_code==404
    assert reader.get("/api/history").json()==[]


def test_new_pending_version_invalidates_transfer_and_cannot_duplicate_work(account):
    owner,supervisor=account("a.owner"),account("a.supervisor")
    tid=transfer(owner)
    version=new_version(owner,doc=1003)
    assert call(owner,"POST","/api/documents/1003/versions",{"content":"duplicate"}).status_code==409
    assert owner.post(f"/api/jobs/{version['job_id']}/retry").status_code==403
    assert call(supervisor,"POST",f"/api/transfers/{tid}/approve").status_code==409


def test_invalid_second_scope_rolls_back_entire_proposal(account, admin):
    result=call(account("a.owner"),"POST","/api/transfers",{
        "recipient_id":105,"reason":"atomic",
        "scope":[{"document_id":1001,"version_ids":[1001]},{"document_id":1003,"version_ids":[2003]}]})
    assert result.status_code==422
    assert admin.execute("SELECT count(*) AS n FROM app_core.transfer_requests").fetchone()["n"]==0
    assert admin.execute("SELECT count(*) AS n FROM app_core.audit_events WHERE action='transfer_requested'").fetchone()["n"]==0


def test_pages_and_form_workflow(account):
    owner,reader,supervisor,recipient=(account("a."+n) for n in ("owner","reader","supervisor","outsider"))
    def form(person,action,data):
        token=person.get("/api/csrf").json()["csrf_token"]
        return person.post("/actions/"+action,data={"csrf_token":token,**data},follow_redirects=False)
    for path in ("/manage","/manage/1001","/history","/transfers"):
        assert owner.get(path).status_code==200
    assert reader.get("/accounts").status_code==403
    assert account("a.admin").get("/accounts").status_code==200
    assert form(owner,"sharing",{"document_id":1003,"version_id":1003,"visibility":"private"}).status_code==303
    assert reader.get("/api/documents/1003").status_code==404
    assert form(owner,"create_version",{"document_id":1001,"content":"表單第二版"}).status_code==303
    process()
    assert "表單第二版" in owner.get("/documents/1001").text
    assert form(owner,"history_permissions",{"version_id":1001,"user_ids":["103","104"]}).status_code==303
    assert reader.get("/history").status_code==200
    assert "1,500" in reader.get("/history/1001").text
    assert form(owner,"transfer",{"document_id":1003,"recipient_id":105,"version_ids":["1003"],"reason":"表單交接"}).status_code==303
    case=supervisor.get("/api/transfers").json()[0]["id"]
    assert form(supervisor,"approve",{"transfer_id":case}).status_code==303
    assert recipient.get("/manage/1003").status_code==200
    assert form(recipient,"archive",{"document_id":1003}).status_code==303
    assert recipient.get("/documents/1003").status_code==404
    assert "已封存" in recipient.get("/manage/1003").text
