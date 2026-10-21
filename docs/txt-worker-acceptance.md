# TXT worker 與恢復驗收

日期：2026-10-21（Asia/Taipei）。基底為 PR #74／`41420ae`；功能分支 `codex/txt-worker-recovery`。本輪是 TXT 自動處理與失聯恢復子集，不代表 PDF、RLS 或完整 M3A 驗收。

## 最終驗證結果

| 驗證 | 指令／方法 | 結果 |
| --- | --- | --- |
| worker 專項 | `docker compose --profile test run --build --rm tests pytest tests/test_worker.py -q -p no:cacheprovider --tb=short` | 42 passed，3.36 秒 |
| 完整回歸 | `docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider --tb=short` | 252 passed、120 subtests passed，14.95 秒 |
| 工具 | `python -m unittest tools.test_check_rls_readiness tools.ci.test_run_tests -v` | 12 tests OK |
| Compose 實際啟動 | 獨立臨時 project、全新 DB、隨機 fixture 密碼，啟動 setup／worker；以 rag_app 建立 TXT，再輪詢工作狀態 | ready、private、attempts=1；正文與指標正確 |
| 單輪入口 | 臨時 project 的 worker service 執行 `python -m app.worker --once` | exit 0 |
| 正常停止 | `docker compose ... stop worker` | worker_started → worker_cycle_completed → worker_stopped |
| 差異格式 | `git diff --check` | 通過 |

完整回歸包含新裝、M1 升級、bootstrap 重跑、原有登入／分享／版本 API、安全錯誤頁、題庫與新增 worker 案例。唯一 pytest 警告仍為既有 Starlette TestClient 使用 HTTPX 的棄用提示。

Compose smoke 使用 `secure-rag-worker-smoke-875d8b4b` 獨立 project，沒有使用開發資料庫、既有 `.env` 或真實文件。驗證完成已清除該 project 的容器、network、volume 與臨時環境檔。未變更使用者開發 `.env`；啟動正式本機 worker 前仍須按教學設定獨立密碼。

本文件記錄提交前的本機驗證；本輪 GitHub Actions 結果須以對應提交的 PR checks 為準，既有 #74 的遠端成功不能代替本輪 CI。CI 已加專用 worker fixture 密碼與受限 `--once` 啟動檢查。

## 自動驗收邊界

- 真實 `rag_worker` LOGIN：禁止讀來源／帳號／版本／工作表、改有效指標、改工作狀態、建立版本、建立 schema 物件或 SET ROLE 冒充 web／writer。web 無恢復 EXECUTE；worker 拒絕 owner／web DSN。
- TXT 正文來自保存 bytes，BOM 正確處理；原子切換後新版私人、無舊 ACL；舊讀者 API／HTML 都無法回退，owner 可讀新正文。
- 未到期租約不回收、不重複領取；到期後保留同一工作／版本／來源，退避且換 token；舊 token 被拒絕。三次失聯終止，舊有效版保留。
- 帳號停用、封存、文件／版本 owner 變更、base 變更、target／outgoing 不可用均阻止恢復；恢復後發布也重新驗權。
- ready／failed 不復活；回收 rollback 不改原租約與次數。兩個 worker 不重複發布，兩個回收者只排隊一次。
- 分別鎖住 A 的 outgoing／document／target／job；B 的過期工作仍能恢復，C 的 queued 工作仍能發布。
- 子程序領取並 commit 後遭 kill，新程序能恢復後發布；SIGTERM 停止閒置輪詢，停止旗標在回收後、領取前再檢查。
- 從 001–004 建立並領取真實工作後套 005：來源／digest／租約／版本不變，attempts 計為 1，恢復後同一工作及版本完成；migration 可重跑。

時間測試透過隔離資料庫管理連線設定 expired／available 時點，避免真正等待多輪 5 分鐘；這不是外部系統時鐘漂移或長時間壓力測試。

## 保留的失敗、修正與複核

1. 新測試先因缺少 `app.worker` 發生 import error（RED）；初版實作後 30 項通過。補中斷／升級案例後完整回歸 247 項、120 子測試通過。
2. 獨立審查發現：最早過期工作被鎖會阻塞所有 worker 的恢復及正常領取；停止旗標在 recovery 期間到達仍可能再 claim。新增測試先得到 5 failed（四種 lock timeout、一個缺少 stopEvent 介面）。
3. recovery 改為逐候選 NOWAIT，候選子交易回滾後釋放鎖並繼續；runOnce 增加停止檢查。重建僅有 tmpfs 的 test-db，讓尚未交付的 005 修正重新由空庫套用；42 項通過。既有開發 DB／volume 未刪除。
4. readiness 工具先以 worker BYPASSRLS 案例重現漏檢，再將新角色納入旗標、membership、table-owner catalog 查詢；12 項工具測試通過。工具仍不能證明 RLS 已啟用或通過。
5. 獨立代理唯讀複核上述修正，沒有剩餘可行動 P0–P2 問題。代理未執行共享 Docker 測試；本文件測試結果均由主代理執行並核對 exit code。

## 尚未涵蓋

PDF／切塊／embedding／向量檢索／RAG／付費預算、RLS 政策及 auth/business pool 拆分、人工 retry API、主機重啟自動拉起服務、正式部署與長時間負載測試。新的 worker 是可信內部處理者；不宣稱可抵抗主機或 DB owner 遭入侵。

操作方式與已知終止契約見 [TXT worker 教學](txt-worker.md)。任務 Issue／README 里程碑勾選仍留待 PR 驗收，不將部分成果標成完整階段完成。
