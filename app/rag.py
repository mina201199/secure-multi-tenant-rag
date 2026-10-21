"""Exact vector search and extractive answers; every boundary uses fresh RLS snapshots."""
from fastapi import HTTPException
from app.db import bind_identity, connect
from app.models import ModelUnavailable, answer_sources, embed_texts, provider

REFUSAL = {"answer": "目前有權限的文件沒有足夠證據回答此問題。", "citations": [], "refused": True}
SELECT_CHUNKS = """
 SELECT c.id,c.document_id,c.version_id,c.content,c.location_kind,c.location_no,
 c.start_offset,c.end_offset,c.profile,d.title,d.revision
 FROM app_core.chunks c JOIN app_core.documents d ON d.id=c.document_id AND d.tenant_id=c.tenant_id
 JOIN app_core.document_versions v ON v.id=c.version_id AND v.tenant_id=c.tenant_id
 JOIN app_core.index_manifests m ON m.version_id=c.version_id AND m.profile=c.profile
 WHERE c.tenant_id=%(tenant)s AND d.status='ready' AND d.current_version_id=c.version_id
 AND v.state='ready' AND app_core.readable_current(c.version_id)
"""


class Retrieval:
    def __init__(self, settings, identity, model=None):
        self.settings, self.identity, self.model = settings, identity, model
        self.dsn = settings.database_url

    def bind(self, connection):
        bind_identity(connection, self.identity, self.identity["token_hash"], self.settings.csrf_secret)

    def active(self):
        with connect(self.dsn) as connection:
            self.bind(connection)
            if connection.execute("SELECT app_core.actor() AS id").fetchone()["id"] != self.identity["id"]:
                raise HTTPException(409, "身分或文件權限已變更，請重新查詢")

    def check(self, sources):
        with connect(self.dsn) as connection:
            self.bind(connection)
            rows = connection.execute(SELECT_CHUNKS + " AND c.id=ANY(%(ids)s)",
                {"tenant": self.identity["tenant_id"], "ids": [row["id"] for row in sources]}).fetchall()
            current = {row["id"]: row for row in rows}
            if len(current) != len(sources) or any(current.get(row["id"]) != row for row in sources):
                raise HTTPException(409, "身分或文件權限已變更，請重新查詢")

    def search(self, question):
        self.active()
        model = self.model or provider()
        self.model = model
        # Do not spend for an empty authorized corpus.
        with connect(self.dsn) as connection:
            self.bind(connection)
            if not connection.execute(SELECT_CHUNKS + " AND c.profile=%(profile)s LIMIT 1",
                {"tenant": self.identity["tenant_id"], "profile": model.profile}).fetchone():
                return []
        vector = embed_texts(model, self.dsn, self.bind, [question], self.active)[0]
        with connect(self.dsn) as connection:
            self.bind(connection)
            # Exact search; no ANN index and no unauthorized candidate fill or client-side filtering.
            rows = connection.execute(SELECT_CHUNKS +
                " AND c.profile=%(profile)s AND (c.embedding OPERATOR(public.<=>) %(vector)s::public.vector)<0.85"
                " ORDER BY c.embedding OPERATOR(public.<=>) %(vector)s::public.vector,c.id LIMIT 5",
                {"tenant": self.identity["tenant_id"], "profile": model.profile, "vector": str(vector)}).fetchall()
        if rows:
            self.check(rows)
        return rows

    def answer(self, question):
        sources = self.search(question)
        if not sources:
            return dict(REFUSAL)
        payload = [{"source_id": row["id"], "text": row["content"]} for row in sources]
        result = answer_sources(self.model, self.dsn, self.bind, question, payload, lambda: self.check(sources))
        citations = validate_evidence(result, sources)
        # Validate ALL sent sources, including those the model did not cite.
        self.check(sources)
        if not citations:
            return dict(REFUSAL)
        # The model cannot introduce unsupported factual prose. The answer is the verified evidence.
        return {"answer": "\n\n".join(f"「{item['quote']}」[{i}]" for i, item in enumerate(citations, 1)),
                "citations": citations, "refused": False}


def validate_evidence(result, sources):
    if not isinstance(result, dict) or set(result) != {"evidence"} or not isinstance(result["evidence"], list):
        return []
    if not 1 <= len(result["evidence"]) <= 5:
        return []
    rows = {row["id"]: row for row in sources}
    citations, seen = [], set()
    for evidence in result["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != {"source_id", "quote"}:
            return []
        source_id, quote = evidence["source_id"], evidence["quote"]
        if type(source_id) is not int or source_id not in rows or source_id in seen:
            return []
        row = rows[source_id]
        if not isinstance(quote, str) or not 4 <= len(quote.strip()) <= 1200 or quote not in row["content"]:
            return []
        seen.add(source_id)
        citations.append({"chunk_id": source_id, "document_id": row["document_id"], "version_id": row["version_id"],
                          "title": row["title"], "location_kind": row["location_kind"],
                          "location_no": row["location_no"],
                          "start_offset": row["start_offset"] + row["content"].index(quote),
                          "end_offset": row["start_offset"] + row["content"].index(quote) + len(quote),
                          "quote": quote, "url": f"/citations/{source_id}"})
    return citations
