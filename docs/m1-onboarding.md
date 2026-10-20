# M1：從啟動到權限驗證

這個階段可以登入、列出文件與閱讀內容。每個帳號固定屬於一家公司；主管或管理員不會自動取得私人文件閱讀權。現在尚無 RAG 問答、上傳與資料庫 RLS。

## 啟動

安裝 Git 與可執行 Linux containers 的 Docker Desktop，clone 專案並進入專案目錄。第一次執行：

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(24))"
```

將 `.env` 的每個 `change-me` 分別換成隨機值，不要提交 `.env`。若 `.env` 已存在，就使用現有設定，不要覆寫。

```powershell
docker compose --profile test run --build --rm tests
docker compose up --build -d
docker compose ps -a
```

M1 原始基準為 40 passed；本分支含版本測試為 56 passed。setup 應以 0 結束，web 應為 healthy。開啟 <http://127.0.0.1:8008>，密碼使用自己 `.env` 的 `SEED_USER_PASSWORD`。

## 操作與預期

1. 登入 `a.reader`：看到出差補助規定與料件清冊，看不到主管會議紀錄。
2. 登出，再登入 `a.owner`：可看到以上三份文件，但看不到封存文件。
3. 登出，再登入 `a.admin` 或 `a.manager`：只看到本公司公開的出差補助規定。
4. 登入 `b.reader`：出差補助為 NT$3,000，甲公司為 NT$1,500。
5. 將甲公司文件網址貼給乙公司帳號，應回傳 404；不存在的 ID 也回傳相同結果。

完整帳號表在 README「測試帳號」。沒有註冊功能。

## 為什麼這樣設計

登入後瀏覽器保存隨機 Session Cookie，資料庫只保存其雜湊。每次請求重新查帳號是否有效，再由後端身分決定公司與閱讀權；網址或 query string 的 tenant_id 無法切換公司。Cookie 認證的變更操作需要 CSRF token 與本站 Origin，避免其他網站代替使用者操作。

setup 使用資料庫 owner 建表與 seed；web 使用受限的 rag_app，不能直接改業務資料或 schema。這仍不等於 RLS：目前 SQL 必須明確檢查授權。

## 小練習

閱讀 `tests/test_documents.py` 的權限矩陣，先預測 `a.manager` 能看到哪些文件，再執行：

```powershell
docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider tests/test_documents.py
```

修改 `app/templates/document.html` 的說明文字後重建服務，觀察頁面變化；不要修改權限 SQL 來讓測試過關。測試完成後可還原自己的文字修改。

## 驗收紀錄

2026-10-20 接手時以 Docker 重跑原始 M1：40 passed，1 個既有 TestClient 棄用警告。版本模型變更後完整回歸：56 passed，1 個相同警告。M1 任務勾選與 Issues 保留待 PR 驗收。
