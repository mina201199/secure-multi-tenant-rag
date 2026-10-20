# M2 TXT 版本寫入驗收

日期：2026-10-20。依 [寫入切片計畫](m2-version-write-plan.md) 與 [寫入契約](m2-version-write-contract.md) 編寫獨立驗收。協調者在真實 PostgreSQL 16 測試庫執行：專項 **98 passed**；完整回歸 **210 passed、120 subtests passed**，各有一則既有 TestClient 棄用警告。

## 驗收方式

`tests/test_version_writes.py` 在實作前建立，協調者已先觀察缺少 `app.version_service` 的 RED collection failure。功能實作後必須透過 Compose 測試服務，連接真實 PostgreSQL 執行；語法編譯不能替代資料庫驗收。

建立、查詢與一般權限測試使用 `DATABASE_URL` 的實際 `rag_app` 連線。內部處理測試使用獨立管理連線先 `SET ROLE rag_version_processor`，並確認 `current_user`；發布／失敗不以 owner 權限冒充。管理連線僅建立隔離文件、模擬失效、觀察未提交資料與清理。並行情境使用兩條真實連線；分享／發布競爭先從 `pg_stat_activity` 證實正在等待列鎖，才提交另一個操作。

每個測試建立獨立文件，不改 seed 文件或其 ACL。清理依序移除 pointer、jobs、sources、版本 ACL、versions、documents；停用模擬會恢復 seed owner 的有效狀態。

## 覆蓋範圍

| 契約 | 自動驗收 |
| --- | --- |
| 建立權屬於當下文件 owner | reader／staff／admin／manager／跨公司／僅版本 owner／偽 actor-tenant 配對拒絕；真正文件 owner 可建立 |
| 新版排隊且私人 | version 2、queued、NULL content、private、空 ACL；舊 pointer 不動，原始 bytes 與 SHA-256 留存 |
| 冪等與單一 pending | 排隊及發布後重播相同回應；相同 key 改 source/base 衝突；不同 key 有 pending 衝突；兩連線同 key 僅一份工作，不同 key 一成功一 409 |
| 失敗不影響舊版 | failed 工作保留舊 pointer、正文與 ACL；下次版本號遞增，不重用失敗號碼 |
| 原子發布 | BOM UTF-8 原始 bytes 建立正文；commit 前另一條連線仍看到舊狀態，commit 後 pointer／ready／正文一致；rollback 無半套發布 |
| 無舊版 fallback | 真正發布 private 新版後，只有舊版權限的 reader 在 API 與 HTML 都得到 404 |
| 發布重新驗證 | actor 停用、文件封存、文件 owner 變更、base 變更或 outgoing 不再 ready 時，工作 failed，文件 pointer／狀態／owner 不變 |
| 工作存取與 lease | reader／admin／其他公司不能查工作；重播及查詢重新驗證當下管理權；錯誤 token 409 且不影響合法工作；到期匹配 lease 以 lease_expired 失敗 |
| TXT 邊界 | 空白、PDF、無效 UTF-8、NUL、超過 1 MiB／200,000 字元、非法冪等 key 拒絕且不配置來源或工作 |
| 受限 SQL 能力 | web 與 processor 都無原始來源直接讀寫；web 無內部處理 EXECUTE、直接版本 INSERT 或 pointer UPDATE；來源與 digest 不可覆寫；ready 要求非空正文 |
| tenant 與角色 | source／job 的 tenant 複合 FK；writer／processor 非 LOGIN、非 superuser、無 BYPASSRLS；web 無 membership；功能固定 search_path、撤回 PUBLIC EXECUTE，writer 非資料表 owner |
| 分享／發布並行 | 分享等待發布後拒絕對過期版修改；發布等待分享後可完成，新版不繼承舊版 ACL |

## 執行紀錄

- 已確認：`python -m py_compile tests/test_version_writes.py` 成功。
- 已由協調者觀察：實作前缺少版本服務的 RED。
- 專項指令：`docker compose --profile test run --build --rm tests pytest tests/test_version_writes.py tests/test_version_routes.py -q -p no:cacheprovider --tb=short`，98 passed、1 warning，6.79 秒。先移除僅有 tmpfs 的 test-db 容器，確認由空庫套用 001–004。
- 完整指令：`docker compose --profile test run --rm tests pytest -q -p no:cacheprovider --tb=short`，210 passed、120 subtests passed、1 warning，18.39 秒。包含獨立新裝、M1 舊庫升級至全部 migration、重跑 bootstrap 與既有登入／分享／隔離案例。
- RLS 工具：`python -m pytest --rootdir=. --confcutdir=. tools/test_check_rls_readiness.py -q -p no:cacheprovider`，5 passed。初次未指定 rootdir 時因主機父目錄讀取權限而 collection 失敗，指定專案根目錄後通過。
- 對隔離測試庫執行唯讀 catalog audit，結果仍為 NOT_READY／BLOCKED（預期 exit 1）：缺 auth／policy-reader、rag_app 角色旗標未收斂，九張表尚未 FORCE RLS。新 writer／processor 與 source／job 已納入稽核，不能把此結果當 RLS 通過。

### 保留的失敗與修正

- 缺 endpoint 時兩項 API 測試先失敗；缺服務時驗收 collection 失敗，確認測試先於實作。
- 第一輪 81 passed、2 failed：字元超量的期望碼由 422 更正為容量 413；有效 200,000 中文字觸發逐字 substr 的平方時間掃描，兩分鐘後取消隔離庫中的單筆測試查詢，改為單次字元範圍掃描後通過。
- multipart 總接收量／HTML 413 的兩項回歸先失敗，加入不依賴 Content-Length 的串流上限與安全 HTML 錯誤頁後通過。
- 審查新增的事件迴圈與 bigint 三項測試先失敗，改由 threadpool 執行寫入及 commit、限制正 bigint 後通過。兩項壞 boundary／header 案例原本已回 422，故撤回未重現的 parser 疑慮。
- [獨立審查](m2-version-review.md) 已複核修正，無剩餘可行動問題；範圍與未涵蓋能力仍按下段限制。

### 本機啟動驗證

`docker compose up --build -d --wait web` 成功，setup 正常結束、web healthy。本機 HTTP smoke 驗證 `/health` 為 ok、使用本機 seed 密碼登入回 303、文件 owner 的版本管理頁回 200，含伺服器產生的 CSRF／expected base／冪等欄位及 queued 說明。此 smoke 未建立或發布版本；功能寫入由上列隔離資料庫測試驗證。

## 後續相依工作

- worker 與恢復：先設計可信登入身分、lease 失聯回收與重試，沿用來源不可變、鎖序和發布重驗；目前排隊不會自動完成。
- PDF 正式整合：品質原型須接上同一來源／job 契約並補頁數、格式、逾時及失敗測試，不能直接開放原型作為正式 parser。
- RLS：可先並行拆 auth／business pool 與 LOCAL context，政策啟用前整合九表、writer／processor 權限與發布交易。見 [更新後的依賴](rls-implementation-plan.md)。
- 題庫人工簽核與既有堆疊 PR 審查可並行；AI 審查不替代人工簽核。本輪分支為 `codex/m2-version-write`，PR 基底為 #73 的 `codex/ci-parsing-readiness`，依堆疊順序審查與整合。

提交前再次執行完整 Compose 回歸：210 passed、120 subtests passed、1 warning，17.40 秒；RLS 工具單元測試 5 passed，`git diff --check` 通過。唯一警告仍是 Starlette TestClient／HTTPX 棄用通知。

本切片不驗收 PDF parser、背景 worker、重試 API、付費處理、chunks／embeddings、向量搜尋或 RLS。上述測試只證明受限內部 TXT 處理能力；不能據此宣稱這些後續功能完成。
