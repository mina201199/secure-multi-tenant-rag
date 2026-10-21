from typing import Annotated, Literal

import psycopg
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.db import get_db
from app.repository import get_document
from app.security import check_session_csrf

ID = Annotated[int, Field(gt=0, le=2**63-1)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sharing(Payload):
    version_id: ID
    visibility: Literal["private", "company", "selected"]
    user_ids: list[ID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.visibility != "selected" and self.user_ids:
            raise ValueError("Only selected sharing accepts readers")
        return self


class NewVersion(Payload):
    content: str = Field(min_length=1, max_length=1048576)


class HistoryGrant(Payload):
    user_ids: list[ID] = Field(default_factory=list, max_length=100)


class Scope(Payload):
    document_id: ID
    version_ids: list[ID] = Field(min_length=1, max_length=100)


class Transfer(Payload):
    recipient_id: ID
    reason: str = Field(min_length=1, max_length=500)
    scope: list[Scope] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_documents(self):
        if len({s.document_id for s in self.scope}) != len(self.scope):
            raise ValueError("Duplicate document scope")
        return self


class AccountState(Payload):
    is_active: bool


def db_error(error):
    status = {"P0002": 404, "P0001": 409, "42501": 403, "22023": 422,
              "23503": 422, "23505": 409, "23514": 422}.get(error.sqlstate)
    if not status:
        raise error
    raise HTTPException(status, {
        404: "找不到資料", 409: "狀態已變更或操作不適用，請重新載入",
        403: "沒有操作權限", 422: "請確認接收人、版本範圍與輸入內容",
    }[status]) from None


def change(connection, action, payload):
    try:
        return connection.execute("SELECT app_core.change(%s,%s) AS result", (action, Jsonb(payload))).fetchone()["result"]
    except psycopg.Error as error:
        db_error(error)


def history_read(connection, version_id):
    try:
        return connection.execute("SELECT * FROM app_core.history_read(%s)", (version_id,)).fetchone()
    except psycopg.Error as error:
        db_error(error)


def make_router(require_identity, page_context, templates):
    router = APIRouter()
    db = Depends(get_db, scope="function")
    auth = Depends(require_identity)

    def mutate(request, connection, action, payload):
        check_session_csrf(request, request.headers.get("X-CSRF-Token", ""))
        return change(connection, action, payload)

    def people(connection, identity, all_accounts=False):
        if all_accounts and identity["role"] != "admin":
            raise HTTPException(403, "沒有操作權限")
        return connection.execute(
            "SELECT id,username,display_name,role,is_active FROM app_core.users WHERE tenant_id=%s "
            + ("" if all_accounts else "AND is_active ") + "ORDER BY id",
            (identity["tenant_id"],),
        ).fetchall()

    def managed(connection, identity, document_id=None):
        rows = connection.execute(
            "SELECT id,title,category,status,current_version_id,revision FROM app_core.documents "
            "WHERE tenant_id=%s AND owner_id=%s ORDER BY id",
            (identity["tenant_id"], identity["id"]),
        ).fetchall()
        if document_id is None:
            return rows
        document = next((d for d in rows if d["id"] == document_id), None)
        if not document:
            raise HTTPException(404, "找不到資料")
        return document

    def inventory(connection):
        return connection.execute("SELECT * FROM app_core.version_inventory()").fetchall()

    def history_list(connection, identity):
        connection.execute("SELECT set_config('app.mode','history',true)")
        return connection.execute(
            """SELECT v.id,v.document_id,d.title,v.created_at
               FROM app_core.document_versions v JOIN app_core.documents d ON d.id=v.document_id
               WHERE v.tenant_id=%s AND app_core.readable_history(v.id) ORDER BY v.id""",
            (identity["tenant_id"],),
        ).fetchall()

    def transfers(connection):
        # Supervisor never receives document/version IDs, titles, contents or scope.
        return connection.execute(
            "SELECT id,applicant_id,recipient_id,reason,status FROM app_core.transfer_requests ORDER BY id DESC"
        ).fetchall()

    @router.get("/api/csrf")
    def csrf(request: Request, identity=auth):
        return {"csrf_token": page_context(request, identity)["csrf"]}

    @router.get("/api/people")
    def people_api(identity=auth, connection=db):
        return people(connection, identity)

    @router.get("/api/manage/documents")
    def managed_api(identity=auth, connection=db):
        return managed(connection, identity)

    @router.get("/api/documents/{document_id}/versions")
    def versions_api(document_id: ID, identity=auth, connection=db):
        managed(connection, identity, document_id)
        return [v for v in inventory(connection) if v["document_id"] == document_id]

    @router.put("/api/documents/{document_id}/sharing")
    def sharing(request: Request, document_id: ID, payload: Sharing, identity=auth, connection=db):
        return mutate(request, connection, "sharing", {"document_id": document_id, **payload.model_dump()})

    @router.post("/api/documents/{document_id}/versions", status_code=202)
    def version(request: Request, document_id: ID, payload: NewVersion, identity=auth, connection=db):
        return mutate(request, connection, "create_version", {"document_id": document_id, **payload.model_dump()})

    @router.post("/api/documents/{document_id}/archive")
    def archive(request: Request, document_id: ID, identity=auth, connection=db):
        return mutate(request, connection, "archive", {"document_id": document_id})

    @router.get("/api/documents/{document_id}/download")
    def download(document_id: ID, identity=auth, connection=db):
        document = get_document(connection, identity, document_id)
        if not document:
            raise HTTPException(404, "找不到文件")
        original = connection.execute(
            "SELECT body,media_type FROM app_core.source_files WHERE version_id=%s", (document["version_id"],)
        ).fetchone()
        if original:
            extension = "pdf" if original["media_type"] == "application/pdf" else "txt"
            return Response(bytes(original["body"]), media_type=original["media_type"],
                            headers={"Content-Disposition": f'attachment; filename="document-{document_id}.{extension}"'})
        return Response(document["content"], media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="document-{document_id}.txt"'})

    @router.get("/api/jobs/{job_id}")
    def job(job_id: ID, identity=auth, connection=db):
        result = connection.execute(
            "SELECT id,document_id,version_id,status,stage,attempts,error FROM app_core.jobs WHERE id=%s", (job_id,)
        ).fetchone()
        if not result:
            raise HTTPException(404, "找不到資料")
        return result

    @router.post("/api/jobs/{job_id}/retry")
    def retry(request: Request, job_id: ID, identity=auth, connection=db):
        current = job(job_id, identity, connection)
        return mutate(request, connection, "retry", {"document_id": current["document_id"], "job_id": job_id})

    @router.put("/api/history/versions/{version_id}/permissions")
    def history_permissions(request: Request, version_id: ID, payload: HistoryGrant, identity=auth, connection=db):
        return mutate(request, connection, "history_permissions", {"version_id": version_id, **payload.model_dump()})

    @router.get("/api/history")
    def histories(identity=auth, connection=db):
        return history_list(connection, identity)

    @router.get("/api/history/versions/{version_id}")
    def history(version_id: ID, identity=auth, connection=db):
        return history_read(connection, version_id)

    @router.post("/api/transfers", status_code=201)
    def propose(request: Request, payload: Transfer, identity=auth, connection=db):
        return mutate(request, connection, "transfer", payload.model_dump())

    @router.get("/api/transfers")
    def transfer_list(identity=auth, connection=db):
        return transfers(connection)

    @router.post("/api/transfers/{transfer_id}/approve")
    def approve(request: Request, transfer_id: ID, identity=auth, connection=db):
        return mutate(request, connection, "approve", {"transfer_id": transfer_id})

    @router.post("/api/transfers/{transfer_id}/reject")
    def reject(request: Request, transfer_id: ID, identity=auth, connection=db):
        return mutate(request, connection, "reject", {"transfer_id": transfer_id})

    @router.get("/api/admin/users")
    def accounts(identity=auth, connection=db):
        return people(connection, identity, all_accounts=True)

    @router.put("/api/admin/users/{user_id}/active")
    def account(request: Request, user_id: ID, payload: AccountState, identity=auth, connection=db):
        return mutate(request, connection, "account", {"user_id": user_id, **payload.model_dump()})

    def render(request, identity, name, **context):
        return templates.TemplateResponse(
            request=request, name=name, context={**page_context(request, identity), **context}
        )

    @router.get("/manage")
    def manage_page(request: Request, identity=auth, connection=db):
        return render(request, identity, "manage.html", documents=managed(connection, identity))

    @router.get("/manage/{document_id}")
    def manage_document(request: Request, document_id: ID, identity=auth, connection=db):
        document = managed(connection, identity, document_id)
        versions = [v for v in inventory(connection) if v["document_id"] == document_id]
        readers = connection.execute(
            "SELECT user_id FROM app_core.version_readers WHERE version_id=%s", (document["current_version_id"],)
        ).fetchall()
        jobs = connection.execute(
            "SELECT id,version_id,status,stage,attempts,error FROM app_core.jobs WHERE document_id=%s ORDER BY id DESC",
            (document_id,),
        ).fetchall()
        current = next(v for v in versions if v["id"] == document["current_version_id"])
        manager_id = connection.execute("SELECT manager_id FROM app_core.users WHERE id=%s",
                                        (identity["id"],)).fetchone()["manager_id"]
        return render(request, identity, "manage_document.html", document=document, versions=versions,
                      people=people(connection, identity), readers=[r["user_id"] for r in readers],
                      current=current, jobs=jobs, manager_id=manager_id,
                      sources=[row["item"] for row in connection.execute(
                          "SELECT app_core.ingestion_inventory() AS item").fetchall()
                          if row["item"]["document_id"] == document_id])

    @router.get("/history")
    def history_page(request: Request, identity=auth, connection=db):
        return render(request, identity, "history.html", histories=history_list(connection, identity),
                      owned=[v for v in inventory(connection) if v["owner_id"] == identity["id"] and v["state"] == "ready"],
                      people=people(connection, identity),
                      grants=connection.execute("SELECT version_id,user_id FROM app_core.history_permissions").fetchall())

    @router.get("/history/{version_id}")
    def history_document(request: Request, version_id: ID, identity=auth, connection=db):
        return render(request, identity, "history_document.html", version=history_read(connection, version_id))

    @router.get("/transfers")
    def transfer_page(request: Request, identity=auth, connection=db):
        return render(request, identity, "transfers.html", transfers=transfers(connection),
                      people={p["id"]: p["display_name"] for p in people(connection, identity)})

    @router.get("/accounts")
    def account_page(request: Request, identity=auth, connection=db):
        return render(request, identity, "accounts.html", accounts=people(connection, identity, all_accounts=True))

    @router.post("/actions/{action}")
    def action_form(
        request: Request, action: str, csrf_token: str = Form(max_length=256),
        document_id: int | None = Form(None), version_id: int | None = Form(None),
        visibility: str = Form("private"), user_ids: list[int] = Form([]),
        content: str = Form("", max_length=1048576), job_id: int | None = Form(None),
        recipient_id: int | None = Form(None), version_ids: list[int] = Form([]),
        reason: str = Form("", max_length=500), transfer_id: int | None = Form(None),
        user_id: int | None = Form(None), is_active: bool = Form(False),
        identity=auth, connection=db,
    ):
        check_session_csrf(request, csrf_token)
        try:
            if action == "sharing":
                payload = {"document_id": document_id, **Sharing(version_id=version_id, visibility=visibility,
                           user_ids=user_ids if visibility == "selected" else []).model_dump()}
            elif action == "create_version":
                payload = {"document_id": document_id, **NewVersion(content=content).model_dump()}
            elif action == "history_permissions":
                payload = {"version_id": version_id, **HistoryGrant(user_ids=user_ids).model_dump()}
            elif action == "transfer":
                payload = Transfer(recipient_id=recipient_id, reason=reason,
                                   scope=[Scope(document_id=document_id, version_ids=version_ids)]).model_dump()
            elif action in ("approve", "reject"):
                payload = {"transfer_id": transfer_id}
            elif action == "account":
                payload = {"user_id": user_id, "is_active": is_active}
            elif action in ("archive", "retry"):
                payload = {"document_id": document_id, "job_id": job_id}
            else:
                raise HTTPException(404, "找不到操作")
            # Form IDs receive the same bounds as JSON/path IDs, including optional fields.
            for key in ("document_id", "version_id", "transfer_id", "user_id", "job_id"):
                if key in payload and payload[key] is not None and not 0 < payload[key] < 2**63:
                    raise HTTPException(422, "請確認輸入內容")
            change(connection, action, payload)
        except ValidationError:
            raise HTTPException(422, "請確認接收人、版本範圍與輸入內容") from None
        destination = ("/history" if action == "history_permissions" else
                       "/transfers" if action in ("transfer", "approve", "reject") else
                       "/accounts" if action == "account" else f"/manage/{document_id}")
        return RedirectResponse(destination, status_code=303)

    return router
