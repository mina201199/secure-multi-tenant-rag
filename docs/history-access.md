# 歷史版本調閱與獨立授權（#20）

正式入口是登入後左側「歷史調閱」，網址 `/history`。僅列出未封存檔案中，你可管理或有明確歷史權的完成舊版；清單不含正文。一般閱讀、新版權限、公司公開、管理員與主管身分都不會自動授予歷史權。

## 這次採用的規則

2026-10-21 使用者明確選擇較嚴格的封存邊界：**即使已獲歷史授權，封存後後台也一律拒絕調閱**。清單、授權與稽核頁、API／HTML 正文都會拒絕；ACL 與稽核繼續保存。既有決策稿與封存說明已同步這項更正。

- 只有該歷史版本的擁有者可以授予／撤銷同公司帳號的歷史權。檔案目前擁有者沒有其他版本的隱含管理權。
- 授權對象必須有效；可撤銷已停用帳號。包含版本擁有者本人，都須明確授予歷史權後才能調閱。
- 授權只影響指定版本；不更改一般 ACL、新版可見性、有效版本指標、來源或 worker 狀態。
- 目前有效版本、queued／processing／failed／archived 版本與封存檔案不能走歷史入口。沒有解除封存或來源下載功能。
- 找不到與無權都回 404。未登入 401、CSRF／Origin 不符 403、請求欄位錯誤 422；版本競爭或舊版本鎖忙碌回 409，請重新載入再試。

## 操作練習

1. 以版本擁有者登入，在一份可用檔案新增 TXT／支援的 PDF 版本，等待 worker 成功發布。只有舊的完成版本會出現在歷史後台。
2. 點左側「歷史調閱」→ 該版本「歷史授權與稽核」；對自己或同公司有效帳號點「授予調閱權」。
3. 被授權者登入後進入 `/history`，點「調閱並記錄」。頁面標示歷史版本與稽核編號；PDF 保留實際頁次。一般檔案入口仍只遵循當前版本 ACL。
4. 版本擁有者在授權頁可看最近 50 筆授權、撤權與調閱紀錄。撤權後，下一次調閱回 404；管理員與主管不能藉角色開啟此頁。
5. 封存檔案後，已授權帳號也看不到歷史清單，舊調閱／管理網址失效；資料仍保留。已交付到瀏覽器的內容無法遠端收回，`no-store` 不等於撤回機制。

小練習：在虛構資料中，先授予 `a.reader` 一份歷史版本，再撤銷。重送原調閱表單應失敗；不要為了讓測試通過而加入管理員例外。

## API 契約

| 方法與路徑 | 權限與回應 |
| --- | --- |
| GET `/api/history/versions?after_id=0` | 摘要陣列，每頁最多 100 筆，依 version_id 遞增；下頁使用最後一筆 version_id |
| GET `/api/history/versions/{id}/access` | 僅版本擁有者；同公司候選人、逐人授權狀態與最近 50 筆稽核，不含正文 |
| PUT `/api/history/versions/{id}/access` | `reader_id`、嚴格 boolean `granted`、`csrf_token`；只變更指定帳號，不覆蓋他人的授權 |
| POST `/api/history/versions/{id}/read` | `csrf_token`；需要明確 ACL，回內容／PDF 頁次與已提交的 `audit_id` |

CSRF 亦可由 `X-CSRF-Token` 提供；Origin／Referer 檢查仍適用。拒絕額外 tenant／actor 欄位。GET 不交付正文、不新增調閱稽核；使用 POST 是為了讓使用者明確觸發被稽核的調閱，避免預取造成讀取紀錄。正式 HTML 有同樣的權限及 POST 表單。歷史回應設 `Cache-Control: no-store`。

## 資料庫與鎖定

migration 008 新增 `history_readers` 與 `history_audit_events`，用 tenant 複合外鍵限制版本、操作者及授權對象。`rag_history_access` 是 NOLOGIN 內部角色；web 僅可執行固定能力，不能直接讀寫新表或任意插入／修改／刪除稽核，worker／processor 無歷史能力。內部能力只可追加稽核，沒有 UPDATE／DELETE 權。

調閱、授權與撤權沿用 outgoing → document 鎖序，再以歷史版本 `FOR SHARE NOWAIT` 預先取得 ACL／稽核外鍵所需鎖；忙碌就回 409，不反向等待舊 outgoing。帳號用共享鎖保持交易內有效性。等待後重新查封存、current pointer、版本 owner／status、帳號及 ACL；後續交接須遵循相同協定。

每次成功調閱、授權與撤權均在同一交易寫入 actor、tenant、document、version、action、subject 與時間。重複授權不新增第二筆 ACL，但每次操作仍留下事件。稽核不保存正文或憑證；被拒絕的請求沒有交付內容，不記成成功調閱。內容回應必須等 commit 成功；測試以延後到 commit 才拋錯的真實 DB trigger，確認 API／HTML 都不會提前交付。

這不是 RLS 驗收：既有 web 對版本資料的底層 SELECT 權限尚待 #25／#26 收斂。本次是在既有可信 Session、受限能力與 HTTP 邊界內完成 #20；不可把 app 持有的資料庫憑證交給使用者。

## 部署與驗證

本次不修改 migrations 001～007，也不新增套件。對既有資料庫先備份，再執行 setup 安裝 008；未授予任何既有帳號隱含歷史權。測試只用一次性的 test-db，沒有替你的日常資料庫自動套用 migration。

```powershell
docker compose build setup tests
docker compose run --rm setup
docker compose --profile worker up -d web worker
docker compose --profile test run --rm tests pytest -q -p no:cacheprovider --tb=short
python -m unittest tools.test_check_rls_readiness tools.ci.test_run_tests -q
```

`tests/test_history.py` 的 24 項案例包含獨立權限、明確自授權、角色／公司隔離、版本擁有者、CSRF、欄位邊界、HTML escaping、PDF 頁次、分頁、稽核提交失敗、交易 rollback、受限角色，以及撤權／停用／封存／發布／擁有者變更的等待後重驗。

開發證據：先重現缺少 API 的 404 與缺少模板；並行測試及獨立複核發現稽核／ACL 外鍵的隱含歷史列鎖可能與舊 outgoing 互等，讀取／授權測試都曾因鎖逾時失敗。改為 NOWAIT 與 409 後，24 項專項測試通過；獨立再次複核無待修正問題。

2026-10-21 最終本機驗收：重建 runtime／test 映像，完整回歸 **415 passed、120 subtests passed**；僅既有 Starlette HTTPX deprecation warning。readiness／CI runner 工具 **12 tests OK**，`git diff --check` 通過。遠端 CI 狀態以本 PR checks 為準。

## PR #78 與 Issue 進度

[PR #78](https://github.com/mina201199/secure-multi-tenant-rag/pull/78) 交付 #20 的獨立歷史 ACL、後台與成功調閱稽核。實作提交 `5b26682` 的 [postgres／offline CI](https://github.com/mina201199/secure-multi-tenant-rag/actions/runs/39562411896) 全數通過；最新提交與合併狀態以 PR checks 為準。

同日合併前再次獨立複核，未發現待修正問題；完整重跑 **415 passed、120 subtests passed**，工具 **12 tests OK**。本輪追加的變更僅為 README 與本節的進度對照。

| Issue | 驗收與剩餘範圍 |
| --- | --- |
| #20 / M2-06 | 歷史權與一般 ACL 分離、版本擁有者管理、每次重驗、成功調閱／授權／撤權稽核、封存全面拒絕已交付，可依本次驗收關閉 |
| #19 / M2-05 | 新增「封存後歷史後台也拒絕」的整合驗證；下載、檢索與舊引用仍缺，維持開啟 |
| #25 / M2-11 | 新增 history_readers／history_audit_events、tenant 複合外鍵及受限能力角色；RLS 政策與連線拆分仍未交付，維持開啟 |
| #26 / M2-12 | 補歷史角色權限、稽核回滾與撤權／停用／封存／版本競態；不能取代實際 RLS 下的讀寫與連線重用驗收 |
| #27 / M2-13 | 正式歷史 API、Jinja2 頁面、操作教學已交付；交接、帳號管理與整體第二階段驗收仍未完成 |

#19 的下載／檢索前置仍未全部完成，但其封存核心已可支援本次歷史整合驗收；不因此把 #19 一併關閉。#20 關閉後，原 67 項任務為 17 項完成、50 項待完成，任務總索引 #1 同步維護。
