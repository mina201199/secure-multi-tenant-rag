# 第一階段：登入與授權文件

此文件保留第一階段交付時的範圍。現行專案已完成第二階段，請以 [第二階段操作教學](milestone-2-guide.md) 的 RLS、版本與啟動說明為準。

這一階段提供可登入的文件網站。Session 決定「你是誰、屬於哪家公司」；文件擁有者、分享範圍及指定閱讀名單決定「你能看到哪些文件」。目前還沒有上傳、問答、版本管理或 RLS。

## 啟動與停止

先啟動 Docker Desktop，使用 Linux containers，在專案目錄執行：

```powershell
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps -a
```

預期 `db`、`web` 為 healthy；`setup` 為 Exited (0)，表示初始化正常完成。開啟 http://127.0.0.1:8008/login。健康檢查在 `/health`；API 文件在 `/docs`（Swagger 介面需載入外部 CDN）。

停止服務且保留資料：

```powershell
docker compose --profile test down
```

不要在一般停止時加 `-v`，那會刪除資料卷。若只要重置測試資料庫，可執行 `docker compose --profile test rm -s -f test-db`，再重新執行測試；該容器只保存可丟棄的測試資料。開發資料庫 `rag_data` 不會因此被刪除。

程式更新後重新執行 `up --build -d --wait`。初始化可重跑，不會重設既有使用者密碼、停用狀態或文件內容。需要結構變更時必須新增 migration，不能把初始 schema 當成完整的升版機制。

## 測試帳號與可見範圍

所有下列帳號的示範密碼均為 `Demo-Rag-2026!`，只供本機虛構資料使用。

| 身分 | 甲公司 | 乙公司 | 同公司可讀文件 |
| --- | --- | --- | --- |
| 公司管理員 | a.admin | b.admin | 公司公開文件 |
| 主管 | a.supervisor | b.supervisor | 公司公開文件 |
| 文件擁有者 | a.owner | b.owner | 公開、私人、指定分享，共三份 |
| 獲指定授權員工 | a.reader | b.reader | 公開、指定分享，共兩份 |
| 未獲指定授權員工 | a.outsider | b.outsider | 公司公開文件 |

主管是員工的固定關係，不是第三種角色。公司管理員與一般員工為兩種角色；第一階段尚未提供帳號管理頁面。

| 文件 | 甲公司 ID | 乙公司 ID | 內容差異 |
| --- | --- | --- | --- |
| 出差補助辦法 | 1001 | 2001 | 甲公司 NT$1,500；乙公司 NT$3,000 |
| 私人工作筆記 | 1002 | 2002 | 僅各自擁有者可讀 |
| 指定分享作業規定 | 1003 | 2003 | 各自擁有者與 reader 可讀 |

## 手動操作驗收

1. 使用 `a.reader` 登入，應看到兩份文件；開啟出差補助辦法，金額為 NT$1,500。
2. 將網址改成 `/documents/1002`、`/documents/2001` 或 `/documents/999999`，都應收到相同的「找不到文件」，HTTP 404。
3. 直接開啟 `/api/documents/2001`，也必須是 404。介面隱藏連結本身不構成授權保護。
4. 登出再登入 `b.reader`，應看到乙公司的兩份文件，補助為 NT$3,000；看不到甲公司的文件。
5. 登入 `a.admin` 或 `a.supervisor`，仍不能讀取 `1002`、`1003`。
6. 登入 `a.owner`，應能開啟甲公司的三份文件。
7. 登出後直接開啟 `/api/documents`，應收到 HTTP 401。

同一瀏覽器共用 Cookie，切換帳號會替換目前 Session。若需要同時比較兩家公司，使用不同瀏覽器或分開的無痕工作階段。

## 自動化驗收

使用真實 PostgreSQL 執行登入、CSRF、Session、授權矩陣、撤權、停用、SQL 關聯約束與交易回滾測試：

```powershell
docker compose --profile test run --build --rm tests
```

測試僅允許 `APP_ENV=test` 且主機為 `test-db`、資料庫為 `rag_test` 的連線。它會變更測試資料，不能改成連線開發或正式資料庫。測試資料庫使用 tmpfs，與示範服務的命名資料卷分離。

服務啟動後，再從 Windows 主機驗證實際 HTTP 路由：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-running.ps1
```

這個腳本會驗證十個帳號的登入、清單、登出，以及 140 次文件 API／頁面存取，最後列出 PASS。`ExecutionPolicy Bypass` 僅用於該次程序，不修改系統政策。腳本只使用示範帳號，不修改文件或帳號資料。

## 一次請求如何運作

1. 瀏覽器提交登入表單，帶上伺服器簽章的 CSRF 值與對應 Cookie。
2. 後端檢查 Origin、CSRF、登入頻率、Argon2 密碼與帳號狀態。
3. 後端產生隨機 Session；瀏覽器得到 HttpOnly Cookie，資料庫只保存 token 的雜湊。
4. 每次文件請求重新從 Session 找出有效帳號、公司、過期狀態，不能用網址的 `tenant_id` 代替。
5. SQL 同時限制公司、ready 狀態與閱讀權。只有本人擁有、公司公開或 selected 名單內的文件才會回傳。
6. HTML 內容自動跳脫，回應禁止快取；未授權與不存在的文件使用相同 404。

每次一般請求使用單一資料庫交易。發生例外就回滾；登入失敗計數則刻意先提交，避免回滾讓攻擊者能無限重試。web 僅持有 `rag_app` 憑證，沒有初始化管理者環境變數。

## 小修改與回述練習

以下練習未代替作者完成，請自行操作與解釋：

- 修改登入頁標題後重新建置，確認不影響登入。
- 在測試中為 `a.outsider` 新增「不能讀取 1003」的斷言並執行，說明與跨公司測試的差異。
- 說明管理員為何不能讀取私人文件，以及為何「隱藏按鈕」不能代替 API 授權。
- 說明資料庫管理者與 `rag_app` 的差異，以及目前只有 API 查詢隔離、尚未有 RLS 的限制。

## 目前邊界

第一階段的隔離靠 API 授權查詢與資料庫關聯約束。`rag_app` 仍可直接 SELECT 第一階段資料表，因此不可把本階段宣稱為已完成資料庫 RLS 隔離。M2 才會加入 RLS、文件管理、版本、分享變更與帳號啟停介面。

目前提供本機虛構資料示範，不接外部模型，不需要 API 金鑰。第一階段的自動化驗收結果見 [驗收紀錄](milestone-1-verification.md)。
