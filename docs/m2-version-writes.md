# M2 TXT 新版建立與工作狀態

本切片開放既有、有效版本 ready 文件的 TXT 新版建立。來源與新版本、工作保存在同一 PostgreSQL 交易，成功回應前明確 commit。這不代表 PDF、背景 worker、重試 API、向量搜尋或 RLS 已完成；目前一般使用者上傳後會停在 queued，沒有自動處理或公開發布端點。

## 操作頁面

文件管理者可從可閱讀的文件頁面選「上傳 TXT 建立新版」，或直接開啟 `/documents/{document_id}/versions`。管理權依 `documents.owner_id`；即使該管理者無權閱讀目前有效版，直接管理連結仍可使用。分享設定另外依目前有效版本 owner，兩種權限不可互相替代。讀者、主管、管理員與跨公司帳號均不因角色取得管理權。

表單自動帶入目前有效版本識別、Session CSRF 與隨機冪等識別；成功後以 303 導向同頁的安全工作狀態。上傳只建立 queued/private 新版，舊版閱讀與分享持續有效。新版發布後不繼承舊 ACL，失去新版閱讀權的舊讀者也不能回退讀舊版。

## HTTP 介面

`POST /api/documents/{document_id}/versions` 接受 multipart/form-data：

| 欄位 | 規則 |
| --- | --- |
| `file` | 副檔名 .txt；UTF-8 或 UTF-8 BOM；最多 1 MiB 原始 bytes、200,000 Unicode 字元；拒純空白、無效編碼、二進位控制字元與 PDF |
| `expected_current_version_id` | 正 bigint，須與當前有效指標一致 |
| `csrf_token` | 從 `/api/me` 取得，與 Session 配合驗證 |
| `Idempotency-Key` HTTP header | 16–128 個 ASCII 字母、數字、`-` 或 `_` |

需登入 Cookie 並通過同源 Origin/Referer 檢查。欄位必須各出現一次；額外欄位拒絕，包括 tenant、owner、visibility、status、路徑、讀者及客戶端版本號。HTML POST 使用相同 multipart 欄位，冪等識別改用隱藏 `idempotency_key` 欄位。

伺服器逐段累計實際收到的 bytes，整個 multipart 請求最多 1 MiB + 64 KiB，不依賴 Content-Length；超限立即停止讀取並關閉已開啟的暫存檔。檔案本身仍以 1 MiB + 1 byte 有界讀取判斷上限。表單文字欄位最多 64 KiB，防止任意大的 multipart 欄位或額外資料先佔用記憶體／暫存空間。

建立與合法重播皆回 202，包含 `document_id`、`version_id`、`version_number`、`processing_status: queued`、`is_current: false`、`job_id`。同 key 同來源／expected base 重播回原建立結果，即使工作稍後已發布仍相同；目前工作狀態應另外查詢。同 key 不同 fingerprint、有效指標已改變，或不同 key 遇到同文件 queued/processing 工作回 409。

`GET /api/jobs/{opaque_uuid}` 僅回當下有文件管理權者的安全白名單：`job_id`、`version_id`、`status`、`error_code`、`retryable`。不回原始 bytes、正文、檔名、內部路徑、lease 或資料庫例外。工作建立者失去管理權也失去查詢權。不存在、無權與不合法 job ID 均回相同 404。

未登入 401、CSRF/來源失敗 403、不可用文件／無管理權 404、已授權衝突 409、檔案 byte／字元容量或 multipart 請求總量超限 413、格式與欄位錯誤 422。`/api` 一律保留 JSON 錯誤；HTML 頁面依既有 Accept 協商使用通用安全錯誤頁。成功回應前若 commit 失敗，不回 202／303。

## 內部處理與驗收

`app.version_service` 是 route 唯一版本寫入入口，不匯入 prototypes。受限 `rag_version_processor` 內部能力可 claim／complete／fail；發布從不可變來源 bytes 產生正文並在同交易更新 ready 與有效指標。一般 web 角色沒有發布、任意正文覆寫、整表 INSERT／UPDATE 或 processor membership。

`tests/test_version_routes.py` 驗證真實 HTTP／PostgreSQL 邊界、權限區別、輸入限制、冪等、工作狀態、CSRF 與表單錯誤；`tests/test_version_writes.py` 另驗證受限角色、鎖序、原子發布、失敗保留舊版及並行行為。實際執行結果由本輪驗收紀錄提供，本文件不把尚未執行的驗收標為通過。
