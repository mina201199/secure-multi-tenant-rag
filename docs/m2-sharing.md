# M2-02 分享與撤權

本階段把既有「每次請求查有效版本與 ACL」接上可操作的分享設定。沒有加入 RLS、交接、歷史權限或新版寫入；管理員與主管仍沒有文件特權。

## 操作

1. 啟動 Compose 後以 `a.owner` 登入 `/login`，開啟自己的文件。
2. 按「管理分享與撤權」進入 `/documents/{文件 ID}/sharing`。
3. 選擇私人、本公司或指定人員。指定人員至少一位，只能選本公司其他有效帳號；儲存時會整批替換此有效版本的 ACL。
4. 指定 `a.reader` 後，其既有登入 Session 下一次請求即可讀；改私人後，同一 Session 下一次清單與詳細請求立即失去權限。已經送到瀏覽器的文字無法追回。

本公司模式不開放其他公司；私人與本公司模式會清除原有指定名單。畫面提交使用 Session CSRF 與 Origin/Referer 檢查。頁面只列本公司有效帳號，不提供全域使用者搜尋。

## API 契約

`PUT /api/documents/{id}/sharing` 使用登入 Cookie。JSON 範例：

```json
{
  "version_id": 12,
  "mode": "specified",
  "reader_ids": [4, 5],
  "csrf_token": "從 GET /api/me 取得的 csrf_token"
}
```

`mode` 為 `private`、`company`、`specified`。`version_id` 必填，避免舊頁面修改剛切換的新版；`reader_ids` 預設空清單，最多 500 個正整數，重複 ID 會去重。指定模式必須非空，其他模式必須空。未知欄位（例如 `tenant_id`、`owner_id`）拒絕；公司與操作者只取登入身分。也可用 `X-CSRF-Token` 標頭取代 JSON token。

成功回傳 `document_id`、`version_id`、`mode`、正規化後的 `reader_ids`。未登入 401；CSRF 或跨站來源 403；不存在、不可管理、封存、非 ready 文件/版本皆同一 404；有效版本不符 409；不合法模式、名單、跨公司、停用、擁有者自選等 422。不合法名單回應不區分不存在、跨公司與停用，且既有 ACL 不變。

只有目前有效 ready 版本的 `owner_id` 可以設定，文件層舊 owner 不作為授權依據。沒有接受任意版本 ID 的分享路徑。資料庫仍使用 `private/company`，指定模式為 `private` 加上 `version_readers`；ACL 與 visibility 同一交易寫入並在回應前 commit。

HTML 使用 `GET/POST /documents/{id}/sharing`；GET 不寫入，POST 執行相同授權與替換規則後 303 回到設定頁。切換為私人/本公司時，瀏覽器提交前會移除指定名單欄位。

## 交易與最低權限

003 migration 只新增 `rag_app` 對版本 `visibility` 的欄位 UPDATE 與版本 ACL INSERT/DELETE。文件有效版本指標、版本狀態、使用者啟停、owner 都維持不可寫。版本列 `FOR UPDATE` 序列化同版分享請求；取得鎖後以新的 SQL statement 重查有效版本指標、文件/版本狀態與 owner，避免等鎖期間保留的舊 JOIN snapshot 修改舊版本；帳號有效性在授權寫入時驗證，停用後每次讀取的登入檢查仍會阻擋。

後續版本切換、封存與交接必須先鎖定原有效版本列，再重查文件指標、版本 owner/狀態，並與分享操作使用一致的鎖定順序；不能假設本次版本列鎖已阻止任意未遵循契約的 owner SQL 改文件指標。目前這些生命週期操作尚未開放 runtime 寫入。RLS 仍待 M2-11，不把應用層檢查宣稱為資料庫隔離。

## 測試與證據（2026-10-20）

```powershell
docker compose --profile test run --build --rm tests pytest tests/test_sharing.py -q -p no:cacheprovider
```

測試使用真正 PostgreSQL 與受限 runtime 角色，建立/清理獨立虛構文件，不改 seed ACL。首輪 RED：9 failed、4 passed，失敗原因為分享 API 與頁面尚未存在。首輪 GREEN：13 passed。補嚴格 ID 驗證後 RED：2 failed、21 passed，布林及字串被誤轉 ID；修正後與 runtime 權限回歸合跑：35 passed、1 warning（23 項分享 + 12 項資料庫，2.38 秒）。既有 Starlette TestClient/HTTPX 棄用警告與功能無關。

另以兩條真實 PostgreSQL 連線和執行緒重現等鎖期間版本切換：RED 回傳 200 而應為 409；加入鎖後重查後修正。測試等待 pg_stat_activity 確認 Lock 後才切換版本，沒有依賴固定睡眠順序。輪詢前呼叫 pg_stat_clear_snapshot() 刷新交易內 activity 快照。最終獨占測試庫的分享與資料庫回歸合跑 36 passed、1 warning（24 項分享 + 12 項資料庫，2.63 秒）。中間一次與主流程完整測試重疊，停用閱讀者案例出現 1 failed、35 passed；兩組測試同時更動 seed 帳號，停止重疊後重跑通過，未改應用程式。覆蓋擁有者分享、原 Session 立即撤權、公司公開與跨公司拒讀、owner/主管/管理員邊界、無效/停用/跨公司名單、失敗保留 ACL、舊版本衝突、CSRF、Origin、表單實際儲存、封存與未就緒拒絕、版本 owner 與文件 owner 不一致。

小練習：以 `a.owner` 將私人文件指定給 `a.staff`，使用另一個已登入的視窗確認可讀，再切回私人；不登出該視窗，重新開啟文件應得到 404。
