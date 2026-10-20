# M2-03／04：受限 TXT 版本服務

這個切片提供既有文件的 UTF-8 TXT 新版建立、持久化工作與內部發布能力。沒有自動執行的 worker，建立成功只代表已排隊；不包含 PDF、重試 API、lease 自動回收、RLS 或向量索引。

`app/version_service.py` 使用 migration 004 的固定 SQL 能力。所有身分來自已驗證 Session 的 tenant 與 user ID；SQL 再查當下帳號有效性與文件 owner。這延續目前可信後端的身分邊界，並不聲稱已防止遭入侵的 `rag_app` 偽造呼叫參數。

## 呼叫介面

| Python 函式 | SQL 能力 | 呼叫角色 |
| --- | --- | --- |
| `get_version_management(connection, identity, document_id)` | `get_version_management(bigint,bigint,bigint)` | rag_app |
| `create_version(connection, identity, document_id, expected_version_id, source_bytes, idempotency_key)` | `create_version(bigint,bigint,bigint,bigint,bytea,text)` | rag_app |
| `get_job(connection, identity, job_id)` | `get_job(bigint,bigint,uuid)` | rag_app |
| `claim_job(connection)` | `claim_version_job()` | rag_version_processor |
| `complete_job(connection, job_id, lease_token)` | `complete_version_job(uuid,uuid)` | rag_version_processor |
| `fail_job(connection, job_id, lease_token)` | `fail_version_job(uuid,uuid)` | rag_version_processor |

管理查詢無權限時回 `None`。建立／狀態服務對不可用資源回 404；已授權的 base、pending、冪等或 lease 衝突回 409。原始 bytes 最多 1 MiB、正文最多 200,000 Unicode 字元，超量回 413；無效 UTF-8、PDF 標記、二進位控制字元、純空白及無效請求欄位回 422。允許 UTF-8 BOM，發布時只移除開頭 BOM，保留換行與其他正文。

每個成功寫入操作由呼叫者提交交易。claim 完成後須先 commit，再進行處理與 complete／fail；不得持有 claim 的 job 鎖開始發布。前台寫入路由在回應成功前 commit。處理介面回 `failed` 也是必須 commit 的結果，不能改成拋例外後整筆 rollback。

## 資料與權限

`version_sources` 以 bytea 保存原始 bytes，SHA-256 為 PostgreSQL 內建 `sha256` 計算的 generated 欄位。更新來源會被 trigger 拒絕；原始來源、版本與工作同交易建立，不存在外部檔案保存窗口。來源與工作均透過 tenant／document／version 複合外鍵綁定。來源表不保存使用者提供的檔名或路徑。

新版本為 private／queued、正文為 NULL，沒有複製舊版 ACL。ready 版本必須有非空正文。`version_jobs` 的 queued／processing 部分唯一索引限制同文件只有一筆 pending；失敗保留版本號。相同 actor／文件／key 的 fingerprint 包含原始 SHA、TXT 格式與 expected base；合法重播在新 base 與 pending 驗證前回原始建立回應，即使工作已發布仍回原本 queued 的 202 內容。最新狀態透過 `get_job` 查詢。

來源容量、編碼與請求格式驗證先於冪等查詢，所以格式不合法先回 422、超量先回 413；相同 key 改用另一份合法來源或不同合法 expected base 則回 409。

`rag_version_writer` 是專用 NOLOGIN、NOINHERIT、NOSUPERUSER、NOBYPASSRLS、非表 owner 角色，只取得流程需要的表／欄位權限。函式固定 `search_path = pg_catalog, app_core, pg_temp`，撤銷 PUBLIC EXECUTE。`rag_app` 只新增建立／管理／狀態函式的 EXECUTE，無來源表權限、版本 INSERT、正文／狀態／指標寫入權限。既有分享欄位權限維持 migration 003 定義。

`rag_version_processor` 是另一個 NOLOGIN 角色，只能執行 claim／complete／fail，不能直接讀寫資料表，且不授予 rag_app membership。測試用 admin 連線使用 `SET ROLE rag_version_processor` 驗證這個實際受限身分。沒有公開發布路由。內部 helper 與文字驗證函式也撤銷 PUBLIC EXECUTE。

## 處理與鎖序

create／complete／fail 依 outgoing version → document 的順序取鎖，然後用新 statement 重驗指標與權限；發布再依 target version → job 取鎖。鎖等待後指標不同即 409，呼叫者 rollback，不能繼續追加鎖另一個 current。分享與版本寫入使用相同 outgoing 鎖協議。

claim 只更新 job，使用 `SKIP LOCKED`，給隨機 lease 與五分鐘期限。版本列維持 queued，job 的 processing 是此切片正在處理的狀態。claim 回可信 job／tenant／document／version／actor／base metadata、原始 bytes、SHA 與 lease；一般狀態查詢只有 job_id、version_id、status、error_code、retryable，不洩露來源或 lease。

complete 只從保存的來源解碼，不接受呼叫者提供正文。只有有效 lease、未過期、原 actor 有效、文件與目標 owner 未變、文件／舊有效版仍 ready、base 未變、目標未發布、來源有效時，才同交易寫入新正文、ready 狀態、有效指標與完成工作。

正確 token 已過期回 `failed/lease_expired`，owner／active／封存／base 等失效也回安全代碼的 failed 結果，保留舊指標；`fail_job` 可用同一個過期 token 主動失敗。錯誤或已失效 token 回 409，不變更其他工作。沒有自動回收失聯 lease、重試或排程；只有排隊與受限處理能力完成，不代表背景處理已啟用。

帳號停用僅按 READ COMMITTED 的重驗邊界處理，不鎖 users，也沒有為鎖帳號擴大 UPDATE 權限。未來封存／交接／worker／RLS 必須延續本鎖序與最小權限，不能以一般角色直接 UPDATE 指標替代。
