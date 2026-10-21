"""Real HTTP + worker + PostgreSQL acceptance on the isolated test environment."""
import json
import os
import re
import time
from pathlib import Path
import httpx

BASE = "http://test-web:8000"
ORIGIN = "http://127.0.0.1:18008"
checks = 0


def check(response, expected=200):
    global checks
    assert response.status_code == expected, (response.status_code, response.text[:200])
    checks += 1
    return response


def login(username):
    client = httpx.Client(base_url=BASE, timeout=40, follow_redirects=False)
    page = check(client.get("/login"))
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    check(client.post("/login",data={"username":username,"password":"Demo-Rag-2026!","csrf_token":csrf},
                      headers={"Origin":ORIGIN}),303)
    return client


def mutate(client, method, url, payload=None, **kwargs):
    token = check(client.get("/api/csrf")).json()["csrf_token"]
    return client.request(method,url,json=payload,headers={"X-CSRF-Token":token,"Origin":ORIGIN},**kwargs)


def main():
    if os.getenv("APP_ENV") != "test" or os.getenv("RAG_TEST_PROVIDER") != "true":
        raise RuntimeError("This smoke requires the explicit test-only environment")
    a,b,reader,other = login("a.owner"),login("b.owner"),login("a.reader"),login("b.reader")
    documents=[]
    for owner,code in ((a,"a"),(b,"b")):
        token=check(owner.get("/api/csrf")).json()["csrf_token"]
        result=check(owner.post("/api/documents",data={"title":f"M3 HTTP {code} 公司規定","category":"虛構驗收"},
            files={"file":(f"company-{code}.pdf",Path(f"output/pdf/company-{code}-policy.pdf").read_bytes(),"application/pdf")},
            headers={"X-CSRF-Token":token,"Origin":ORIGIN}),202).json()
        documents.append(result)
        for _ in range(40):
            job=check(owner.get(f"/api/jobs/{result['job_id']}")).json()
            if job["status"]=="done":
                break
            assert job["status"] in ("queued","running"),job
            time.sleep(.5)
        else:
            raise AssertionError("worker did not finish")
        check(mutate(owner,"PUT",f"/api/documents/{result['document_id']}/sharing",
              {"version_id":result["version_id"],"visibility":"company","user_ids":[]}))
    for client,amount,forbidden in ((reader,"1,500","3,000"),(other,"3,000","1,500")):
        answer=check(mutate(client,"POST","/api/ask",{"question":"每日出差補助是多少？"})).json()
        assert not answer["refused"] and amount in answer["answer"] and forbidden not in str(answer)
        assert answer["citations"][0]["location_no"]==1
        check(client.get(answer["citations"][0]["url"]))
        own=documents[0 if client is reader else 1]
        foreign=documents[1 if client is reader else 0]
        check(client.get(f"/api/documents/{own['document_id']}/download"))
        check(client.get(f"/api/documents/{foreign['document_id']}/download"),404)
    # Preserve the active A sample for the marked test-browser walkthrough.
    bdoc=documents[1]
    old=check(mutate(other,"POST","/api/search",{"question":"每日出差補助"})).json()["sources"][0]["id"]
    check(mutate(b,"POST",f"/api/documents/{bdoc['document_id']}/archive"))
    check(other.get(f"/api/citations/{old}"),404)
    refused=check(mutate(other,"POST","/api/ask",{"question":"每日出差補助"})).json()
    assert refused["refused"]
    print(json.dumps({"result":"PASS","model":"TEST DOUBLE; no paid OpenAI calls","http_checks":checks,
                      "document_ids":[d["document_id"] for d in documents],"a_sample_ready":True},ensure_ascii=False))


if __name__=="__main__":
    main()
