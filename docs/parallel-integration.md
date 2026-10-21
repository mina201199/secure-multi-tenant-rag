# 四條工作線整合與驗證

日期：2026-10-20。從 `codex/workspace-demo` 上的工作目錄開始，由協調者與三個子代理並行完成整合；交付分支為 `codex/m2-sharing-integration`，PR 以 `codex/workspace-demo` 為基底，依賴展示功能 PR #71。Issue 保留到 PR 驗收後處理。

## 交付範圍

| 工作線 | 結果 | 依賴與限制 |
| --- | --- | --- |
| M2-01 驗收 | 重跑既有完整測試、Compose 與健康檢查 | 基準 59 passed；PR 驗收仍待協作流程完成 |
| M2-02 分享／撤權 | 目前有效版本擁有者可設定私人、公司公開及指定有效帳號；API、表單、migration、正反測試與教學 | 沒有新增版本建立、封存、交接或 RLS |
| RLS 設計 | 角色、認證／業務連線分離、交易 LOCAL 情境、政策、安全 helper 與驗收矩陣 | SQL 是設計草圖，未套 migration，不標 M2-11／12 完成 |
| 虛構資料與核心題庫 | 30 題 AI 撰寫規格、獨立虛構 corpus、18 題 dev／12 題保留驗收、10 項完整性測試 | 尚待獨立人工審核、匯入器、RAG runner 與至少 100 題驗收集；不標 M4-01 完成 |

分享與 RLS 的契約一致使用目前有效版本的 owner；文件管理責任 owner 用於未來建立新版，不混用。003 僅授 UPDATE(visibility) 與 ACL INSERT／DELETE；帳號狀態與有效指標仍不可由 runtime 更新。未實作 RLS 前，runtime 直接 SQL 仍可能繞過 API 的逐列授權，不能宣稱已完成多層隔離。

## 驗證證據

完整測試指令（Docker test stage 已納入 evals）：

```powershell
docker compose --profile test run --build --rm tests
docker compose up --build -d --force-recreate setup web
docker compose ps -a
```

整合測試：**95 passed, 1 warning, 120 subtests passed**，包含既有 59 項、24 項分享、2 項額外最低權限回歸與 10 項題庫完整性檢查。使用真實 PostgreSQL 與受限 runtime，包含新裝與升級測試。警告為既有 Starlette TestClient／HTTPX 棄用通知。

Compose setup Exited (0)、web healthy；實際 HTTP smoke 驗證 health ok、a.owner 登入、三份授權文件及三個分享表單可開啟。沒有修改開發資料庫 seed ACL、刪除 volume 或呼叫付費 API。

## 審查、失敗與修正

- 分享測試先確認缺少路由造成預期失敗，再實作。嚴格 reader ID 測試先揭露 bool／string 轉型問題，修正後通過；詳見 M2-02 教學。
- 審查發現列鎖需要 UPDATE grant，因此取消為鎖定而擴大的帳號狀態／有效指標權限，只鎖版本列。測試資料庫中開發草稿的額外 grant 已撤回；正式 003 migration 不含這些 grant。
- 審查指出等待版本鎖的 JOIN 可能保留舊文件 snapshot；真實並行回歸先觀察到錯誤 200，修正為鎖定後另發 READ COMMITTED 查詢重驗，失效版本回傳 409 且舊 ACL 未被加入。未來發布／封存／交接仍須遵守同一 outgoing-version 鎖協定。
- 並行測試使用資料庫 Lock wait 狀態同步；每次讀取統計前清除快照，避免同交易內快取造成假逾時。
- 整合中一次驗證與代理的測試重疊，導致共用 a.reader 啟用狀態互相干擾：代理測試曾出現 1 failed／35 passed。停止重疊後串行重跑；這次失敗保留在此，不作為產品測試通過證據。
- 獨立代理審查目前分享流程與題庫，未發現待修正的租戶／owner 授權繞過或來源矛盾。RLS 文件完成靜態檢查，沒有 RLS 執行成績。

目標帳號 active 驗證是 statement snapshot，不能宣稱提交瞬間仍 active；並行停用後帳號仍無法通過下一次身分驗證。已交付瀏覽器的內容無法因撤權追回。

## 接手順序

先 PR 驗收 M2-01／02，接續 M2-03／04 新版建立與切換；依 RLS 設計拆認證能力後再實作隔離。題庫先人工審核，再待文件處理、預算閘門與 RAG 完成後執行實驗。任務勾選與 Issue 關閉仍待 PR 驗收。
