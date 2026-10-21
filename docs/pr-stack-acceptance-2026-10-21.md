# PR #70～#75 整合驗收與進度

日期：2026-10-21。依使用者授權複核並按相依順序整合。這份記錄是本輪進度依據；較早的驗收文件保留當時結果與限制，不回寫成當時已有後續功能。

## 相依順序與複核

[#70](https://github.com/mina201199/secure-multi-tenant-rag/pull/70) 版本模型 → [#71](https://github.com/mina201199/secure-multi-tenant-rag/pull/71) 工作台 → [#72](https://github.com/mina201199/secure-multi-tenant-rag/pull/72) 分享／撤權 → [#73](https://github.com/mina201199/secure-multi-tenant-rag/pull/73) CI／安全錯誤頁／準備工作 → [#74](https://github.com/mina201199/secure-multi-tenant-rag/pull/74) TXT 版本寫入 → [#75](https://github.com/mina201199/secure-multi-tenant-rag/pull/75) worker／恢復。

合併採 merge commit，保留歷史；上游合併後將下一個 PR 改為 main。三組獨立唯讀 AI 複核涵蓋版本／分享、UI／CI、寫入／worker；修正以下兩項後沒有剩餘可行動 P0–P2。這不是人工題庫簽核，也不是正式部署驗收。

| 問題 | 修正及證據 |
| --- | --- |
| 正式頁面「文件知識庫」跳到展示資料 | #71 commit `c83b56b`：正式模式改回 `/documents`；直接 Jinja render 確認展示模式仍為 `/demo#documents`。分支回歸 59 passed。 |
| 分享等待舊版本鎖後可能寫入未鎖定新版 | #72 commit `a871e17`：保存實際鎖定版本，鎖定／重查／請求版本須一致，否則 409；兩個版本 ACL 不變。修正後獨立複核通過。 |

分享競態的 RED：在 publisher 持有 v1 鎖時，以已知的 v2 ID 請求分享；publisher 切至 v2 後，原程式回 200，期望為 409。原 fixture 只清理 v1 ACL，因此失敗重現同時出現 teardown 外鍵錯誤；fixture 已改為清理測試文件的所有版本 ACL，隔離 tmpfs 測試庫重建後再驗證。舊／新請求 ID 均參數化保留，沒有降低授權或測試要求。

## 實際驗證

Windows Docker Desktop、Linux Python 3.12、真實 PostgreSQL 16、虛構 seed；使用獨立 test-db，不讀寫正式企業資料。測試序列執行。

```powershell
docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider --tb=short
```

- 修正前整合基準：252 passed、120 subtests passed。
- #71 導覽修正：59 passed。
- #72 分享修正：96 passed、120 subtests passed。
- 所有修正同步至 #75（`9fc27f9`）後：**253 passed、120 subtests passed，17.45 秒**。
- 以上 pytest 均有 1 個既有 Starlette TestClient／HTTPX 棄用警告。
- `git diff --check` 通過；既有 JS 語法檢查通過。
- #73～#75 修正推送後重新執行 GitHub Actions；以各 PR 最新 head 的 checks 為合併門檻。#70～#72 早於 CI workflow，使用既有驗收、本輪分支回歸與整合回歸補驗。
- 新裝、M1 升級、重跑 bootstrap、受限角色、分享／發布並行、worker lease／SIGTERM／恢復均納入整合回歸。Compose HTTP health 與 worker 單輪入口另由 CI 執行。

先前實際啟動與驗收證據：[M1 教學](m1-onboarding.md)、[版本模型](m2-versions.md)、[TXT 寫入](m2-version-acceptance.md)、[TXT worker](txt-worker-acceptance.md)。

## README 與 Issue 判定

| 任務／Issue | 本輪狀態 | 尚未完成的範圍 |
| --- | --- | --- |
| M1-01～11，#4～#14 | 已驗收，README 勾選並關閉 Issue | 不包含 RLS 或正式部署 |
| M2-01／02，#15／#16 | 版本模型、三種分享、撤權與並行修正已驗收，勾選並關閉 | 後續生命週期及 RLS 不在這兩項內 |
| M2-03／04，#17／#18 | TXT 新版私人、空 ACL、舊版保留、原子發布與 no-fallback 已驗收；維持開啟 | PDF／TXT 完整產品範圍；沿用先前切片驗收界線，不因 TXT 完成而擴大宣稱 |
| M0-05／06，#2／#3 | 有授權盤點、共用契約與交付規則；維持開啟 | LICENSE／正式分發決定、完整 notices、模組負責人確認 |
| M2-11～13，#25～#27 | 有 RLS 設計、audit、角色限制與部分 API／教學；維持開啟 | RLS 政策、auth/business 拆分、RLS 行為測試；歷史、封存、交接、帳號管理 API |
| M3A-02～06，#29～#33 | TXT 正式輸入／不可變來源、PDF／TXT 離線原型與品質樣本；維持開啟 | PDF 正式採用、保護下載、資源隔離、清洗／來源品質閘門與正式串接 |
| M3A-08／09，#35／#36 | TXT 持久化 job、可信 DB 情境、有限租約恢復、去重與停用／封存重驗；維持開啟 | PDF／多步處理、段落／向量與付費步驟去重，以及完整生命週期整合 |
| M4-01～04，#54～#57 | 30 題草案、18/12 分集、來源欄位與完整性檢查；維持開啟 | 人工簽核、至少 100 題、保留集污染治理與實際 RAG 評估 |

尚無 RLS、chunks／embeddings、RAG、付費呼叫、封存／歷史／交接正式流程或主機重啟自動拉起 worker。公開 Demo 只展示虛構記憶體資料，不能作為這些功能完成的證據。

下一步以 TXT 契約接入 PDF 正式處理；可並行準備 RLS 連線拆分與題庫人工校對，後續串接生命週期與模型／預算閘門。各任務詳情與後續更新見 [總索引 #1](https://github.com/mina201199/secure-multi-tenant-rag/issues/1)。
