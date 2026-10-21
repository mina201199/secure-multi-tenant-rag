# History Access Implementation Plan

> **For agentic workers:** 依 executing-plans 執行並在完成後使用 requesting-code-review 獨立複核。

**Goal:** 交付 #20 獨立歷史授權、調閱後台與交易稽核。
**Architecture:** 新 capability 控制歷史 ACL、調閱與稽核；沿用 Session、CSRF、版本及封存狀態。所有歷史入口拒絕封存文件。
**Tech Stack:** FastAPI、Jinja2、psycopg、PostgreSQL 16、pytest、Docker Compose。
**Spec:** ../specs/2026-10-21-history-access-design.md

## Global Constraints

- 不改 migrations 001～007、不新增相依套件、不啟用 RLS、不開放來源下載。
- 檔案與稽核不含真實資料／憑證。Python 新程式使用兩空白縮排與 camelCase。
- 原 391 項／120 子測試為基線；資料庫測試循序執行。

## Task 1：能力與 API

檔案：`app/migrations/008_history_access.sql`、`app/history_service.py`、`app/history_routes.py`、`app/main.py`、`tests/test_history.py`。

- [x] 新增真實兩版本 fixture、授權／讀取與隔離測試。預期歷史清單 200，尚無路由時應 404。
- [x] 執行 `pytest tests/test_history.py -x`，確認上述失敗。
- [x] 實作 `list_history_versions(tenant,actor,after)`、`get_history_access(tenant,actor,version)`、`set_history_access(tenant,actor,version,reader,granted)`、`read_history_version(tenant,actor,version)` 能力與固定參數服務包裝。
- [x] HTTP 測試驗證 grant 後可 read 且 audit_id 已提交，revoke 後 404；owner 無明確 ACL 也不得調閱。完整欄位與權限依 spec。
- [x] 新增並行撤權／封存／停用回歸，確認等待後重驗，歷史版本鎖只能 NOWAIT，衝突回 409。

## Task 2：正式後台與驗收

檔案：`app/templates/history.html`、`history_access.html`、`history_read.html`、`base.html`、README、操作文件與決策稿。

- [x] 新增 HTML 清單、逐人授權表單、POST 調閱與 escaped PDF 頁次／TXT 正文測試；GET 不回正文。
- [x] 實作模板與正式導覽，所有成功／錯誤歷史回應 no-store。
- [x] 更新嚴格封存規則、操作步驟、失敗與修正、驗收範圍及部署 migration 指令。
- [x] 重建映像，執行完整 pytest、工具 unittest 與 git diff --check。
- [x] 獨立複核、處理發現問題並驗證。

後續交付：提交推送、建立 PR 並確認最新 CI；狀態以 GitHub PR 為準。
