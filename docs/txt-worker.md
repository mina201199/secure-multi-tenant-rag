# TXT 背景處理與恢復

本輪承接 PR #74：上傳仍先回 202／queued；啟動 worker 後，自動從保存的 TXT 原始 bytes 完成受限原子發布。成功前舊版可讀，成功後新版私人且不繼承舊版 ACL。沒有新版權限者不能回退閱讀舊版。

## 啟動

在 `.env` 新增 `WORKER_DB_PASSWORD`，使用獨立隨機英數密碼，例如執行 `python -c "import secrets; print(secrets.token_hex(24))"` 後貼入。不要沿用 owner／web 密碼，也不要提交 `.env`。`TEST_WORKER_DB_PASSWORD` 可另設獨立測試密碼；整合測試亦會產生暫時密碼驗證真實 LOGIN。

```powershell
docker compose --profile worker up --build -d web worker
docker compose --profile worker logs --tail 30 worker
```

預期看到 `worker_started`；有處理或恢復時出現 `worker_cycle_completed`。登入 `/login`，以文件擁有者開啟版本管理頁，上傳 UTF-8 TXT 後重新整理工作狀態，應由 queued／processing 變為 ready。工作成功時有效版本更新；其他帳號必須重新取得新版分享權限。

`worker` 是選用 profile；沒有啟動時工作繼續保存在資料庫。未設定專用密碼，bootstrap 會將 `rag_worker` 設為 NOLOGIN 並清除其密碼；worker 不會退回使用 web／owner 憑證。密碼變更或停用後請停止舊 worker，再重跑 setup；NOLOGIN 不會撤銷既有連線。

```powershell
docker compose --profile worker stop worker
docker compose run --rm setup
docker compose --profile worker up -d worker
# 僅執行一輪，適合啟動檢查；不會清空整個佇列。
docker compose --profile worker run --rm worker python -m app.worker --once
```

## 中斷後如何恢復

每輪先恢復至多一筆過期工作，再領取至多一筆可執行工作。領取先提交，產生 5 分鐘租約與新 token；完成則在另一交易重驗權限、驗證保存的來源、寫入正文／ready／有效指標與工作結果。

- 程序被強制中斷或資料庫暫時斷線：未提交交易回滾，已提交的租約保留。worker 重啟／重連後自動掃描過期工作。
- 第一次／第二次失聯：回收為 queued，分別退避 10／20 秒，再次領取會使用不同 token。最多領取 3 次；第三次失聯終止為 failed／lease_expired。
- 停用帳號、封存、擁有權變更、有效版不符或版本不可用：恢復時直接終止；發布前也再驗證一次，不會把失效資料重新公開。
- 過期候選所需的資料列被鎖住時，依序使用 NOWAIT 並回滾該候選的鎖，繼續處理其他候選；一份文件不會拖住全部恢復與正常領取。
- ready／failed 都是終止狀態，掃描不會重新排隊。既有 `complete_job` 遇過期 token／驗證失敗，以及顯式 `fail_job` 的終止行為保留；自動重試僅適用仍在 processing 的失聯工作。
- SIGTERM／SIGINT 會停止下一次領取，完成已領取工作的短交易後結束；硬中斷則走租約恢復。Docker 主機重啟後需重新執行啟動指令，本輪沒有加入 restart policy 或主機排程。

若工作最後 failed，舊有效版保留。擁有者可重新開啟上傳頁，以新請求建立下一個版本；舊冪等 key 重播不會重新執行。沒有人工重試 API。工作 API 的 `retryable=false` 表示不提供客戶端重試動作；後端失聯恢復由租約狀態決定。

## 安全與交易

005 migration 新增 attempts、available_at 及掃描索引；升級時既有 processing 工作計為第 1 次領取，保留來源、digest、版本、租約與冪等識別。沒有改寫已發布的 001–004 migration。

`rag_worker` 是獨立 LOGIN，NOINHERIT、非 owner、無 BYPASSRLS、無 role membership；僅有四個處理函式的 EXECUTE 與 schema USAGE，不能直接讀來源／使用者／版本／工作表，不能自行改指標或建立版本。SQL helper 仍由 NOLOGIN 的 `rag_version_writer` 持有；web 沒有新增處理能力。worker 連線會驗證 session_user、current_user、角色旗標與 membership，拒絕 web／owner DSN。

恢復沿用 outgoing version → document → target version → job 的鎖序；取得鎖後以新 statement 重讀，與發布／分享一致。並行 worker 使用 `SKIP LOCKED` 領取。來源在資料庫不可變，worker 不能提交任意正文或傳入 tenant／actor 替換工作身分。

statement timeout 30 秒、lock timeout 5 秒、idle transaction timeout 30 秒、連線 timeout 5 秒；閒置等 2 秒，資料庫錯誤重連前等 5 秒。日誌只有固定事件，不輸出文件正文、token、DSN 或 driver 例外。`worker_cycle_completed` 代表一輪已提交，實際 ready／failed 請查授權的工作狀態 API。

## 驗證

```powershell
docker compose --profile test run --build --rm tests pytest tests/test_worker.py -q -p no:cacheprovider --tb=short
docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider --tb=short
python -m unittest tools.test_check_rls_readiness tools.ci.test_run_tests -v
git diff --check
```

`tests/test_worker.py` 使用真正 `rag_worker` 密碼連線；管理連線只建立虛構測試資料、模擬時間／停用／封存及觀察結果。涵蓋兩個 worker、兩個回收者、四種候選列鎖、舊 token、退避與上限、回滾、程序被 kill、SIGTERM、004 升級、私有發布及最低權限。測試在隔離 tmpfs PostgreSQL 執行，沒有對正式資料執行時間調整。

本輪驗證結果與失敗紀錄見 [驗收紀錄](txt-worker-acceptance.md)。目前仍無 RLS／PDF 正式整合／chunks／embedding／RAG／付費呼叫；不能把 TXT worker 的測試當作完整 M3A 或多層資料隔離驗收。
