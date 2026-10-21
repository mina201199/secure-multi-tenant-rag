# M2-03／04：TXT 版本寫入切片計畫

依 `docs/m2-version-write-contract.md` 與使用者接續製作指示執行。這輪完成既有文件的 UTF-8 TXT 新版建立與受限內部發布；PDF、真正執行中的 worker、重試 API、RLS 政策與向量搜尋尚不在本切片。

## 固定介面與分工

- DB／服務代理：新增 `004_m2_version_writes.sql`；新增 `app/version_service.py`。只透過受限 SECURITY DEFINER 能力建立與發布，不擴大 rag_app 整表寫入。
- API／頁面代理：新增 `app/version_routes.py`、版本表單模板與最小註冊；使用下面服務介面。新增 `tests/test_version_routes.py`，不能自改 schema 或服務。
- 驗收代理：新增 `tests/test_version_writes.py`，以真實 PostgreSQL 測角色、冪等、失敗保留舊版、原子切換、同時請求；獨立於 DB 與 API 實作。
- 協調者：整合文件、序列化 Docker 測試與獨立審查；所有共用 SQL／函式介面變動先通知其他代理。

`create_version(connection, identity, document_id, expected_version_id, source_bytes, idempotency_key)` 回 `{document_id,version_id,version_number,processing_status:'queued',is_current:false,job_id}`，重播回相同建立回應。`get_job(connection, identity, job_id)` 回 job 狀態的安全白名單。`get_version_management(connection, identity, document_id)` 回 `{id,title,current_version_id,current_version_number}` 或 None，依 documents.owner_id 管理權。權限／資源不可用統一404，已授權的指標／冪等衝突409；服務拋 FastAPI HTTPException。所有可寫 route 成功回應前 commit。

測試用／未來內部處理介面：`claim_job(connection)` 回可信工作識別、source_bytes、lease_token 與必要 version/company metadata 或 None；`complete_job(connection, job_id, lease_token)` 由保存的原始 UTF-8 TXT 解碼一次，驗證當前 base／owner／active／ready／lease 再發布；`fail_job(connection, job_id, lease_token)` 留 failed 狀態與舊指標。這三項只允許專用 NOLOGIN `rag_version_processor` 角色執行，rag_app 無 membership／EXECUTE；測試用 admin 僅 SET ROLE 成為該角色，不能把 admin 連線當通過權限測試。沒有提供公開發布或任意正文寫 API。

SQL 函式 owner 為新 NOLOGIN 非 superuser `rag_version_writer`（非表 owner、無 BYPASSRLS），僅新增流程需要的列／欄位 grant，search_path 固定，PUBLIC EXECUTE 撤回；rag_app 只有 create／status／management 功能。來源 bytes 與 SHA-256 存於受保護新表且不授 web 直接讀寫；本小型 TXT 切片採 DB bytea，讓來源／job／版本建立同交易，避免提前導入檔案儲存崩潰窗口。來源／job 必須有 tenant 複合關聯。content 可為 NULL 僅在非 ready 時，ready 需非空正文。現有版本、ACL 與指標不變。

輸入明確限制：UTF-8／UTF-8 BOM 的 TXT，最多 1 MiB 原始 bytes、200,000 Unicode 字元，拒 PDF／無效編碼／二進位控制字元／純空白。此限制為本切片值，非 PDF parser 品質通過；不匯入 prototypes。Idempotency-Key 為16–128 ASCII alphanumeric／-／_；來源與 expected base 為 fingerprint。同文件至多一筆 queued／processing；failed 不阻止新版本，但不重用版本號。

所有建立／發布／分享皆遵守 outgoing-version 先鎖、文件再鎖與鎖後新 statement 重驗。發布再鎖 target→job；claim 只改 job，不帶 job 鎖再反向等 document。原始 sources 不可由 web／processor 直接改寫；發布從保存 bytes 建正文，不信任呼叫者回傳任意文字。重新驗證失敗時工作失敗而不切指標；lease stale 不影響其他工作。

## 執行順序與驗證

- [x] 寫 SQL／服務與 API 失敗測試並由協調者執行 RED（先缺功能，再實作）。
- [x] Migration、服務與 API／頁面實作，各自依固定檔案範圍工作。
- [x] 真實 PostgreSQL 驗證 source 不可覆寫、runtime 無發布權、同 tenant／owner／active 邊界、冪等重播與發布後重播。
- [x] 驗證失敗保留舊版、新版私人不繼承 ACL、發布後舊讀者 API／HTML 不可回退。
- [x] 並行建立／分享與發布、過期 expected base、失效 lease、封存／停用／owner變更前後的正反案例。
- [x] 新裝／升級／bootstrap 重跑及既有完整回歸；離線原型與工具只在受影響時重跑。
- [x] 獨立審查、修正、教學與 README；不把此切片勾成 PDF／worker／RLS 完成。
