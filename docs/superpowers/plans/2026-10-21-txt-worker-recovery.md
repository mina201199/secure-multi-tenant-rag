# TXT worker 與恢復機制實作計畫

> 執行方式：本對話依 executing-plans 逐項實作，先跑失敗測試，再實作與驗收。

**Goal:** 讓既有 TXT 排隊工作由獨立受限 worker 自動發布，並安全恢復失聯工作。

**Architecture:** PostgreSQL 仍是唯一持久化佇列，Compose 新增 worker profile。領取、恢復、發布各自一個短交易；發布沿用 004 的 outgoing-version → document → target → job 鎖序及權限重驗。來源始終從保存的 bytes 取得。

**Tech Stack:** Python、psycopg、PostgreSQL 16、Docker Compose；無新增套件或付費呼叫。

**Spec:** 使用者於 2026-10-21 同意本對話的 TXT worker、獨立受限登入、逾時回收、有限重試、發布重驗設計並要求直接實作及測試。

## 固定契約

- rag_worker 是獨立 LOGIN、NOINHERIT、非 owner、無 BYPASSRLS／角色 membership；僅授予領取、發布、失敗與恢復函式。
- 只有設定 WORKER_DB_PASSWORD 才啟用登入；worker profile 要求專用密碼。web 無 worker 憑證。
- 每筆工作最多 3 次領取，租約 5 分鐘；失聯回收後退避 10／20 秒，不變更版本號、來源或冪等 key。
- 僅 processing 且租約已過期可恢復；ready／failed 不復活。恢復時 owner、active、封存、base、target 皆重驗。
- 舊 token 不能發布；第三次失聯標記 failed／lease_expired。權限失效直接 failed。既有 complete/fail 的終止契約保留。
- SQL statement timeout 30 秒、lock timeout 5 秒、閒置交易 timeout 30 秒；worker 每輪最多一筆恢復與一筆處理，無工作等待 2 秒，連線故障等待 5 秒。
- SIGTERM／SIGINT 停止領取新工作，當前短交易完成；硬中斷交由租約恢復。日誌只有固定事件／狀態，不輸出來源、token、密碼或例外內容。
- 不開放人工重試 API、不整合 PDF／embedding／RAG／RLS，不變更現有分享規則。

## Task 1：持久化重試及最低權限

Files: `app/migrations/005_txt_worker_recovery.sql`、`app/bootstrap.py`、`app/version_service.py`、`tests/test_worker.py`。

- [x] 先寫真實 DB 測試：未到期不回收、到期回收、退避、舊 token、三次上限、失效不復活、實際受限 LOGIN。
- [x] 執行 `docker compose --profile test run --build --rm tests pytest tests/test_worker.py -q -p no:cacheprovider --tb=short`，確認缺少 worker／恢復功能的失敗。
- [x] 新增 attempts／available_at 欄位及索引；升級中已 processing 的 attempts 設為 1。
- [x] `claim_version_job()` 只領取到期 queued 且 attempts < 3，原子增加次數。
- [x] `recover_version_job()` 從 DB 選一筆過期工作，以既有鎖序鎖定及重查；合法且未達上限則排隊退避，否則終止並保留原指標。
- [x] bootstrap 綁定專用登入密碼；測試真正以 rag_worker 連線，拒絕直接資料存取和 web 呼叫恢復。

## Task 2：worker 與 Compose

Files: `app/worker.py`、`compose.yaml`、`.env.example`、`.github/workflows/ci.yml`、`tests/test_worker.py`。

- [x] 先測試 `runOnce(connection)` 實際完成 TXT、空佇列、恢復後發布、CLI --once、SIGTERM 停止、錯誤日誌不洩漏。
- [x] `connectWorker(dsn)` 驗證 session_user/current_user=rag_worker 與角色旗標，設定交易 timeout；拒絕 owner／web 憑證。
- [x] `runOnce(connection)` 分別 commit 恢復、領取、完成。斷線／鎖 timeout 留給租約恢復，外層重新連線。
- [x] `main()` 提供 --once 與持續執行；訊號 Event 可中止等待，錯誤只輸出固定事件。
- [x] Compose worker profile 使用獨立 DSN；測試與 CI 使用獨立測試密碼。既有未設定 worker 密碼的 web 啟動仍可使用。

## Task 3：整合驗收與操作說明

Files: `app/templates/versions.html`、`README.md`、`docs/txt-worker.md`、`tools/check_rls_readiness.py`、`tools/test_check_rls_readiness.py`。

- [x] 管理頁說明 worker 啟動後自動處理、失敗保留舊版、新版私人；維持安全工作查詢契約。
- [x] 加入實際雙連線領取／恢復／發布競爭與 004 已有工作升級測試。
- [x] 執行專項及完整 `docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider --tb=short`。
- [x] 執行 readiness 工具單元測試、Compose worker profile 實際 CLI 驗證、`git diff --check`。
- [x] 審查安全邊界、修正問題並重測；記錄通過數、失敗／修正、啟動步驟與未涵蓋範圍。
