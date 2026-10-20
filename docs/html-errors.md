# HTML 錯誤頁

文件頁與表單的 401、403、404、409、422 保留原本 HTTP 狀態碼，提供繁體中文說明、回到文件知識庫及重新登入的固定連結。409 提醒重新開啟最新分享設定後確認再送出；403 提醒重新開啟頁面以更新操作驗證。錯誤頁本身不重送任何操作。

`app/errors.py` 註冊 Starlette HTTPException（也包含 FastAPI HTTPException）與表單驗證錯誤的 handler。只處理既有的 HTTP 錯誤，不改動權限、資料庫或業務規則，也不攔截未知伺服器例外。既有 `/login` 自行處理的登入錯誤頁與 Cookie 行為保持不變。

## 回應格式

- `/api` 與其子路徑一律沿用 FastAPI JSON 錯誤，即使 Accept 是 `text/html`。`/static`、`/health`、`/docs`、`/redoc`、`/openapi.json` 及其子路徑也沿用 JSON 錯誤。
- `/documents` 及其子路徑、`/`、`/login`、`/logout`、`/demo` 在未指定 Accept 或 `*/*` 時提供 HTML。明確要求 JSON、HTML 的 q=0 或 JSON 品質權重較高時提供 JSON。
- 其他不存在的頁面只有明確接受 `text/html` 才提供 HTML 404；一般 API 用戶端仍收到 JSON。錯誤回應加入 `Vary: Accept`。
- HTTPException 的狀態碼與原有標頭（例如 WWW-Authenticate、Retry-After）保留。JSON 分支委派給 FastAPI 原本 handler，包含驗證錯誤結構。

## 資訊邊界

HTML 只使用狀態碼對應的固定文案，不輸出 exception detail、文件名稱、檔案路徑、網址或 query token、提交的欄位內容。不存在與無權閱讀的文件使用相同 404 頁面；分享設定也遵循此規則。錯誤頁不另外查詢身分或資料庫，透過 base.html 的 public_content 呈現，無需 `me`、CSRF 或 Session。導覽連結固定且不帶 return URL，避免反射 URL 參數。

## 驗證

`tests/test_errors.py` 涵蓋 HTML/JSON 協商、API 格式、routing 與 Starlette 404、錯誤標頭、固定復原連結、表單驗證資料不反射，以及真實跨公司文件與分享頁面的 404 等價性。使用既有 Docker 測試庫執行：

```powershell
docker compose --profile test run --build --rm tests pytest tests/test_errors.py -q -p no:cacheprovider
```
