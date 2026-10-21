# M2-01：版本模型與驗收紀錄

日期：2026-10-20。分支：`codex/m2-version-model`。對應任務：[#15](https://github.com/mina201199/secure-multi-tenant-rag/issues/15)。本次只完成版本資料模型、資料升級與目前版本的閱讀流程。

## 設計

`documents` 保存固定文件 ID、公司、標題、分類、目前管理責任、狀態與 `current_version_id`。`document_versions` 保存版本號、版本擁有者、內容、可見範圍、處理狀態及建立時間。`version_readers` 將個別閱讀授權綁在版本上。

新版本可見範圍預設 private、處理狀態預設 queued；不自動建立任何閱讀 ACL。閱讀查詢僅使用有效指標指向的 ready 版本；該版本無權限時，不搜尋舊版。管理責任 owner_id 與版本 owner_id 分開保存，後續交接功能可以指定版本範圍。

有效指標使用 `(tenant_id, document_id, version_id)` 三欄外鍵，禁止同公司不同文件或跨公司版本混接。版本擁有者與 ACL 也使用 tenant 複合外鍵。runtime 對新表只有 SELECT，尚未開放寫入。

## 升級

`002_m2_versions.sql` 將每份 M1 文件轉成第 1 版、複製內容 / 狀態 / 擁有者 / 可見範圍與閱讀 ACL，再設定有效指標。完成後移除舊正文 / visibility 欄位及 document_readers 表，避免存在兩套授權來源。bootstrap 在同一交易中套 migration，失敗會回滾；重跑不重複套用。

這是向前升級，沒有向下 migration。升級後要使用此分支的程式；舊版程式仍依賴已移除欄位，不能直接換回舊映像。重要環境先備份並在副本驗證；本專案目前使用本機虛構資料。

## 實作與驗證順序

- [x] 以原始程式重跑 M1：40 passed。
- [x] 先寫版本測試並執行：因舊 schema 尚要求 documents.content、且缺少 document_versions，出現 8 errors / 1 failed。
- [x] 建立 migration、seed 與版本閱讀查詢，第一輪 50 passed。
- [x] 補不可用有效版本與全新安裝測試；首次測試發現測試 DSN 移除了密碼，修正為保留原設定再覆寫 dbname。
- [x] 完整 Docker 測試：56 passed, 1 warning。
- [x] 獨立程式審查：本次範圍無待修正問題；審查為靜態審查。
- [x] 本機 Compose 升級、健康檢查及實際 HTTP 登入閱讀驗證。
- [ ] PR 驗收後同步勾選 README M2-01 與關閉 #15。

驗證指令：

```powershell
docker compose --profile test run --build --rm tests
docker compose up --build -d
docker compose ps -a
docker compose logs --tail 10 setup web
Invoke-RestMethod http://127.0.0.1:8008/health
```

結果：setup 套用 002 成功且 Exited (0)，web healthy，health 為 ok。以 a.reader 經 HTTP 登入後清單為兩份文件、版本號皆為 1，內容頁 HTTP 200 並顯示「第 1 版」。

測試使用真正 PostgreSQL，包含 M1 回歸、ACL 撤銷、新版私人 / 不繼承 ACL、切換後禁止舊版回退、未發布版本不影響現有效版、不可用有效版本拒絕、同公司不同文件 / 跨公司有效指標拒絕、跨公司擁有者 / ACL 拒絕、舊資料庫升級及全新安裝 / 重複初始化。升級和全新安裝測試各自建立隨機測試資料庫，結束後移除。

既有警告為 Starlette TestClient 使用 HTTPX 的棄用通知，未在本次變更相依套件。

## 後續範圍

下一項是 M2-02 分享與撤權 API / 頁面。新增版本、狀態切換、封存、歷史授權、交接及 RLS 尚未實作；本次透過測試管理連線模擬版本狀態，不代表使用者已有版本管理功能。版本內容目前由受限 runtime 唯讀保護；後續新增寫入操作時仍須實作舊版不可覆寫與原子切換。沒有 worker、檔案指紋或模型整合。
