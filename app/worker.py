"""Restartable extraction and indexing under a bounded, credentialed lease."""
import os
import time
from psycopg.types.json import Jsonb
from app.db import connect
from app.ingestion import ProcessingError, chunk_locations, extract_bounded
from app.models import provider, embed_texts


def bind_job(connection, job):
    connection.execute("SELECT set_config('app.job_id',%s,true)", (str(job["job_id"]),))
    connection.execute("SELECT set_config('app.job_token',%s,true)", (job["lease_token"],))


def process_job(dsn, job, model=None):
    def bind(connection):
        bind_job(connection, job)

    def stage(name):
        with connect(dsn) as connection:
            bind(connection)
            if not connection.execute("SELECT app_core.worker_stage(%s,%s) AS ok",
                                      (job["version_id"], name)).fetchone()["ok"]:
                raise ProcessingError("工作權限已失效")
    try:
        stage("parsing")
        with connect(dsn) as connection:
            bind(connection)
            parsed = connection.execute("SELECT content,locations,parser FROM app_core.parsed_sources WHERE version_id=%s",
                                        (job["version_id"],)).fetchone()
            manifest = connection.execute("SELECT version_id FROM app_core.index_manifests WHERE version_id=%s",
                                          (job["version_id"],)).fetchone()
            if not parsed:
                source = connection.execute("SELECT body,media_type FROM app_core.source_files WHERE version_id=%s",
                                            (job["version_id"],)).fetchone()
                if not source:
                    version = connection.execute("SELECT content FROM app_core.document_versions WHERE id=%s",
                                                 (job["version_id"],)).fetchone()
                    if not version:
                        raise ProcessingError("工作權限已失效")
                    source = {"body": version["content"].encode(), "media_type": "text/plain"}
        if not parsed:
            parsed = extract_bounded(bytes(source["body"]), source["media_type"])
            with connect(dsn) as connection:
                bind(connection)
                if not connection.execute("SELECT app_core.save_parsed(%s,%s,%s,%s) AS ok",
                    (job["version_id"], parsed["content"], Jsonb(parsed["locations"]), parsed["parser"])).fetchone()["ok"]:
                    raise ProcessingError("工作權限已失效")
        if not manifest:
            chunks = chunk_locations(parsed["locations"])
            stage("embedding")
            model = model or provider()
            vectors = embed_texts(model, dsn, bind, [c["content"] for c in chunks],
                                 before_send=lambda: stage("embedding"))
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk["embedding"] = str(vector)
            stage("publishing")
            with connect(dsn) as connection:
                bind(connection)
                if not connection.execute("SELECT app_core.publish_chunks(%s,%s,%s) AS ok",
                    (job["version_id"], model.profile, Jsonb(chunks))).fetchone()["ok"]:
                    raise ProcessingError("工作權限已失效")
                if not connection.execute("SELECT app_core.finish_job(%s,%s,true) AS ok",
                    (job["job_id"], job["lease_token"])).fetchone()["ok"]:
                    raise ProcessingError("工作權限或文件狀態已變更")
                return True
        with connect(dsn) as connection:
            return connection.execute("SELECT app_core.finish_job(%s,%s,true) AS ok",
                                      (job["job_id"], job["lease_token"])).fetchone()["ok"]
    except Exception as error:
        message = str(error) if isinstance(error, ProcessingError) else "文件處理暫時失敗，請重試"
        with connect(dsn) as connection:
            bind(connection)
            connection.execute("SELECT app_core.worker_stage(%s,'failed',%s)", (job["version_id"], message))
            connection.execute("SELECT app_core.finish_job(%s,%s,false)", (job["job_id"], job["lease_token"]))
        return False


def run_once(dsn, model=None):
    with connect(dsn) as connection:
        job = connection.execute("SELECT * FROM app_core.claim_job()").fetchone()
    if not job:
        return False
    process_job(dsn, job, model)
    return True


if __name__ == "__main__":
    while True:
        try:
            if not run_once(os.environ["WORKER_DATABASE_URL"]):
                time.sleep(2)
        except Exception:
            print("版本處理暫時失敗，將由工作租約恢復", flush=True)
            time.sleep(5)
