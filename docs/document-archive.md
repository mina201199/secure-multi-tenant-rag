# 文件封存

文件擁有者可從版本管理頁選擇「封存文件」，確認後回到文件清單。封存後，後續一般前台請求對所有帳號（包含擁有者）都無法列出或開啟文件，也不能修改分享、建立新版、取得原工作狀態或重新進入管理頁。不存在與無權存取都回相同 404，不提供取消封存。

## API 與權限

`POST /api/documents/{document_id}/archive` 接受以下 JSON，沿用 Session、Origin／Referer 與 CSRF 檢查：

```json
{"expected_current_version_id": 123, "csrf_token": "由目前 Session 取得"}
```

成功回 200：`{"document_id":456,"status":"archived"}`。只接受正整數 bigint 範圍的版本識別與 CSRF，不接受 tenant、owner 或任意狀態。只有目前文件擁有者可以操作，主管／管理員／閱讀者沒有隱含權限；文件管理權與目前版本閱讀權分開判定。

過期表單遇到已切換的有效版本回 409；需重新開啟管理頁確認。相同擁有者、相同版本的重送回相同成功結果，不重寫最初封存人與時間。重送仍重驗身分、公司與目前擁有者，不洩漏別人的封存狀態。未登入 401、CSRF 403、欄位錯誤 422。

HTML 使用 GET／POST `/documents/{document_id}/archive`。GET 只顯示確認頁，不修改狀態；POST 成功 commit 後 303 回 `/documents`。

## 保存與背景處理

migration 007 增加 `documents.archived_at`／`archived_by` 與受限 `archive_document(tenant,actor,document,expected)` 能力，只有 web 的 `rag_app` 可執行；PUBLIC、worker 與 processor 均無此能力，web 也未取得直接更新狀態或讀取原始來源表的權限。既有封存資料不猜測封存人／時間，新增欄位保持 null。

封存採既有 outgoing version → document → target versions → jobs 鎖序，等待後重查權限與有效版本。在同一交易內：

1. 文件改為 archived，記錄封存人與時間。
2. queued／processing 版本改 failed，工作標記 failed／archived，清除租約 token 與期限。
3. 保留原始 bytes、摘要、來源格式、版本內容、PDF 頁次、有效版本指標、分享 ACL 及已完成工作紀錄。

不把已發布 PDF 版本改成 archived，因為封存邊界在文件層，版本內容與頁次需完整保留。所有前台有效版查詢都檢查文件 ready；不能藉保留的版本狀態或舊 URL 回退。未提交封存會全部回滾。

已領取的 parser 可能繼續到有界解析結束，但舊租約再提交會被拒絕；worker 會走既有重試迴圈，無法重新發布。取消後不會再被 claim／recover。若發布先完成，舊版本封存請求會 409；若封存先完成，等待中的發布、新版建立及分享會被拒絕。

## 下載與檢索的目前界線

目前尚無正式來源下載、歷史引用或 RAG 檢索 API。本次沒有以永遠回 404 的新增端點冒充下載功能；測試確認猜測下載 URL 回 404，原始檔仍在不可直接讀取的 DB 來源表，沒有公開靜態檔案路徑。這些檢查證明目前不存在可繞過封存的下載入口，**不代替未來授權下載／向量檢索的正反驗收**。

未來 #30 下載與 M3C 檢索／引用必須重新檢查文件非封存及當下授權；#19 的完整跨功能驗收仍待這些入口實作。RLS 與獨立歷史調閱尚未啟用。封存限制後續請求，無法收回先前已交付的內容。

## 操作與驗證

依既有 Compose 設定建置、執行 setup 套用 migration 007，再重啟 web／worker。更新資料庫前依部署流程備份。登入文件擁有者，開啟文件 → 上傳新版／版本管理 → 封存文件；確認後應從清單消失，舊網址應回 404。主管／管理員不會因角色取得封存權。

```powershell
docker compose build setup tests
docker compose run --rm setup
docker compose --profile worker up -d web worker
docker compose --profile test run --rm tests pytest -q -p no:cacheprovider --tb=short
```

驗收案例在 `tests/test_archive.py`：API／表單、Session／CSRF、公司／擁有者邊界、舊版表單、重送審計、原資料／PDF 頁次保留、交易 rollback、受限角色權限、TXT／PDF 待處理與已領取工作失效、解析中封存，以及實際資料庫鎖等待的發布／分享／建立競態。測試只使用虛構資料。

開發先確認缺少封存入口會回 404，再實作；額外表單測試發現 action 使用錯誤識別欄位，已改為管理查詢實際回傳的 `id` 並保留回歸測試。

2026-10-21：封存專項 **21 passed**，完整回歸 **390 passed、120 subtests passed**，只有既有 Starlette HTTPX deprecation warning。

最終另重建 runtime／test 映像及一次性的空 test-db，重跑相同完整測試得到 **390 passed、120 subtests passed**；readiness／CI runner 工具 **12 tests OK**。獨立程式複核檢查鎖序、權限、資料保留、UI 與 worker 邊界，無待修正問題。
