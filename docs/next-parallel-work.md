# 審查與實作準備交付（2026-10-20）

本文件保留上一輪交付截點；下方原列「未實作」的五項已在後續一輪推進，最新成果與限制見 [五項並行工作驗證](parallel-followup-results.md)。

本輪依使用者指定完成 PR 審查、題庫審核交接、PDF／TXT 原型及 RLS 實作準備。沒有合併 PR、提交 GitHub APPROVE、啟用 RLS、啟用解析上傳或呼叫付費模型。原有正式程式與 migration 未修改；新成果保留在工作目錄，尚未提交或推送。

## 成果與驗證

| 工作 | 交付 | 結果／限制 |
| --- | --- | --- |
| PR #70／71／72 審查 | [獨立 AI 審查報告](pr-review-2026-10-20.md) | 未確認 P0–P2 回歸；不是人類審查或正式 GitHub approval；三個 PR 仍未合併 |
| 核心題庫 | [30 題 AI 複核](../evals/reviews/2026-10-20-ai-audit.md)、[人工校對表](../evals/reviews/2026-10-20-human-review.csv) | 未發現可證明的事實／ACL／來源矛盾；人工欄位全留白、signoff=false，M4-01 尚未完成 |
| PDF／TXT 解析原型 | [說明與介面](../prototypes/parsing/README.md)、原型及 18 項測試 | 18 項通過；可信虛構輸入、繁中頁次／段落位置；所有結果 indexable=false，未接正式流程 |
| RLS 實作準備 | [分階段實作計畫](rls-implementation-plan.md)、[唯讀 metadata audit](../tools/check_rls_readiness.py) | 分類工具 4 項測試通過；實際唯讀 test DB audit 為 NOT_READY／BLOCKED，security_acceptance=NOT_RUN |

既有完整 Docker 回歸重跑：`docker compose --profile test run --build --rm tests`，**95 passed, 1 warning, 120 subtests passed，10.70 秒**。題庫的 10 項完整性檢查也獨立重跑通過；它們已包含在 95 項中，不重複加總。原型 18 項與 RLS audit 工具 4 項另用 Codex 隨附 Python 執行，不屬於現有 Docker 測試映像。

原型獨立審查曾發現不支援 PDF filter 會逸出 NotImplementedError，已先重現，再改回受控 unsupported_pdf_feature 並新增回歸；最新獨立審查確認修正。沒有用原型測試通過代表解析品質、沙箱資源保護或 M3A 完成。

RLS audit 僅在 READ ONLY 交易讀 pg_catalog，未 bootstrap／DDL／DML；刻意回傳 exit 1 表示尚未準備完成，不是已通過安全測試。目前缺 rag_auth／rag_policy_reader，七張目標表未 FORCE RLS，rag_app 的 INHERIT 與目標基線不符（不是已確認可利用漏洞）。認證／業務 pool 尚共用，分享及認證顯式 commit 需一併重構。詳細未驗證面向見工具輸出及計畫。

人工校對須由真實審核者逐題填寫表內 human_* 欄位，檢查事實、引用、公司／ACL、版本狀態、拒答洩漏、料號單位及保留集污染。AI 複核、CSV 產生或完整性通過均不替代簽核；不得自動更新 independent_human_signoff。

## 還能並行的項目

以下是建議待辦，**本輪未實作**：

| 優先 | 工作 | 可立即獨立處理的範圍 | 依賴／合併界線 |
| --- | --- | --- | --- |
| 高 | CI | Linux Docker Compose 啟動與現有完整測試、保存結果 | 測試庫串行且獨立 secret；不自動合併 PR |
| 高 | 新版建立／切換契約 M2-03／04 | API、狀態機、新版私人、失敗保留舊版與鎖順序設計 | 與分享 outgoing-version 鎖及未來 RLS 權限協調後才改 schema／程式 |
| 中 | HTML 錯誤頁 | 401／403／404／409 頁面文案及模板 | 保留原狀態碼與不可讀／不存在同回應，不洩漏檔名；避免和 RLS 路由重構同改 |
| 中 | LICENSE／相依授權盤點 | 列現有及候選 PDF parser 的套件授權、保存來源 | 最終專案授權由負責人決定，原型套件不直接升正式依賴 |
| 中 | 文件盤點與品質樣本 | 擴充虛構文字 PDF／TXT、閱讀順序與料號單位的人工預期 | 不匯入真實機密，不把原型抽字成功當品質驗收 |
| 中 | 評估方法 | 定義料號／規格／單位關聯、語意判分、拒答與來源越權追蹤格式 | 本輪發現 C29 required_facts 不足以單獨驗證所有單位／關聯，不能只做字串命中評分 |

優先安排 CI 與新版契約設計；人工校對及授權盤點可由其他人同步進行。RLS 正式第一批建議做認證／業務連線與交易情境拆分，接著依計畫補政策及真正 SQL 正反驗收；本輪沒有提前套政策。
