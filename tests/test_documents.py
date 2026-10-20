import pytest

from tests.helpers import login

TRAVEL = "出差補助規定"
PARTS = "料件清冊（試行）"
MEETING = "主管會議紀錄"

# 權限矩陣：管理員與主管沒有額外閱讀權，只看得到本公司公開文件
EXPECTED_TITLES = {
    "owner": {TRAVEL, PARTS, MEETING},
    "reader": {TRAVEL, PARTS},
    "staff": {TRAVEL},
    "admin": {TRAVEL},
    "manager": {TRAVEL},
}


def tenant_document_ids(admin_db, tenant_name):
    return [
        row[0] for row in admin_db.execute(
            """SELECT d.id FROM app_core.documents d JOIN app_core.tenants t ON t.id = d.tenant_id
               WHERE t.name = %s ORDER BY d.id""",
            (tenant_name,),
        )
    ]


@pytest.mark.parametrize("prefix", ["a", "b"])
@pytest.mark.parametrize("suffix", sorted(EXPECTED_TITLES))
def test_document_list_matches_permission_matrix(client, prefix, suffix):
    login(client, f"{prefix}.{suffix}")
    titles = {document["title"] for document in client.get("/api/documents").json()}
    assert titles == EXPECTED_TITLES[suffix]


def test_same_title_shows_own_company_content(client):
    login(client, "a.staff")
    travel = next(d for d in client.get("/api/documents").json() if d["title"] == TRAVEL)
    content = client.get(f"/api/documents/{travel['id']}").json()["content"]
    assert "NT$1,500" in content and "NT$3,000" not in content


def test_guessing_other_company_ids_looks_like_missing_document(client, admin_db):
    login(client, "a.owner")
    missing = client.get("/api/documents/999999999")
    assert missing.status_code == 404
    for document_id in tenant_document_ids(admin_db, "乙公司"):
        response = client.get(f"/api/documents/{document_id}")
        # 狀態碼與內容都要和「不存在」一模一樣，才不會透露別家公司有這份文件
        assert response.status_code == 404
        assert response.json() == missing.json()


def test_unshared_private_and_archived_documents_are_hidden(client, admin_db):
    meeting_id, archived_id = admin_db.execute(
        """SELECT max(d.id) FILTER (WHERE d.title = %s), max(d.id) FILTER (WHERE d.status = 'archived')
           FROM app_core.documents d JOIN app_core.tenants t ON t.id = d.tenant_id
           WHERE t.name = '甲公司'""",
        (MEETING,),
    ).fetchone()
    login(client, "a.staff")
    assert client.get(f"/api/documents/{meeting_id}").status_code == 404
    client.cookies.clear()
    # 連擁有者也打不開已封存文件
    login(client, "a.owner")
    assert client.get(f"/api/documents/{archived_id}").status_code == 404


def test_forged_tenant_parameter_is_ignored(client, admin_db):
    other_tenant_id = admin_db.execute("SELECT id FROM app_core.tenants WHERE name = '乙公司'").fetchone()[0]
    login(client, "a.staff")
    normal = client.get("/api/documents").json()
    forged = client.get("/api/documents", params={"tenant_id": other_tenant_id}).json()
    assert forged == normal


def test_non_numeric_id_is_validation_error(client):
    login(client, "a.staff")
    assert client.get("/api/documents/abc").status_code == 422


def test_html_pages_follow_same_rules(client, admin_db):
    login(client, "b.reader")
    page = client.get("/documents")
    assert page.status_code == 200
    assert "乙公司" in page.text and PARTS in page.text and MEETING not in page.text
    other_id = tenant_document_ids(admin_db, "甲公司")[0]
    assert client.get(f"/documents/{other_id}").status_code == 404
