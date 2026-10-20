# 五項並行工作交付與驗證（2026-10-20）

依使用者「開始處理」推進前輪建議的五項工作。協調者負責版本契約／整合，三條子代理線負責 CI＋授權盤點、HTML 錯誤頁、解析品質樣本，另由獨立代理審查。保留上一輪未提交成果；沒有 commit、push、合併 PR、替題庫簽人工審核、套用 RLS 或開放正式解析上傳。

以上為開發完成時的截點。本批成果後續由 `codex/ci-parsing-readiness` 交付，以 `codex/m2-sharing-integration`（PR #72）為基底；發布階段再次重跑 112 項＋120 子測試、26 項原型／品質及 10 項工具測試通過。遠端 CI 狀態以交付 PR 對應 commit 的 checks 為準，不回填為本機驗證。

## 交付與邊界

| 工作 | 交付 | 現況與後續 |
| --- | --- | --- |
| CI | [.github/workflows/ci.yml](../.github/workflows/ci.yml)、[CI 說明](ci.md)、counts-only runner 與 6 項測試 | PR（含相依 codex 分支）／push 觸發；完整 PostgreSQL 與題庫串行，離線原型／工具另跑；本機等效驗證通過，GitHub hosted run 尚未執行 |
| M2-03／04 契約 | [新版建立／切換草案](m2-version-write-contract.md) | owner 責任、預設私人、冪等與發布後重播、pending、鎖序、完整性及 14 組未來驗收；無 endpoint／migration 實作，不勾完成 |
| HTML 錯誤頁 | [行為說明](html-errors.md)、handler／template／CSS、17 項測試 | 401／403／404／409＋通用422；保留狀態與 headers、API JSON、不反映路徑／表單／敏感例外；登入頁沿用原流程 |
| 授權盤點 | [依賴清單](dependency-licenses.md) | 對照實際 test image 的 32 個 lock pin，另列原型、字型與原生／容器缺口；沒有選定專案 LICENSE 或宣稱法律相容性完成 |
| 解析品質 | [品質說明](parsing-quality.md)、10 個 gold 樣本、生成器／checker、8 項測試 | 3 supported_basic、3 needs_manual_quality_review、4 rejected；全部 indexable=false，人工簽核為 null |

## 驗證證據

1. HTML 測試先 RED（9 failed、8 passed），實作後 focused 17 passed。完整 DB 回歸使用 Docker、真實 PostgreSQL 與受限 runtime，只由協調者串行執行。
2. 最終 CI runner 的 full invocation 明確包含 `tests evals/tests`，exit 0；counts JSON 為 tests=232、failures=0、errors=0、skipped=0，count_unit=`junit_reported_cases_including_subtests`。另用 collect-only 確認 **112 個獨立 pytest 案例**；另外 120 筆是既有 subtests，不宣稱 232 個獨立功能測試。
3. CI helper 6 項與 RLS readiness 分類工具 4 項本機通過。原型／品質共 26 項在 Windows 通過，也在一次性 Debian slim 容器安裝 pinned 原型依賴、`fonts-wqy-microhei`、設定 `PARSING_QUALITY_CJK_FONT` 後通過（2.772 秒）；未改 production 依賴。
4. 一次性容器以 PyYAML BaseLoader 驗 workflow YAML，核對 triggers／permissions，並對每段 run script 執行 `bash -n`，全部通過。這不是 GitHub Actions schema／actionlint／遠端執行的完整替代。
5. 本機 Compose web 已更新並通過 healthcheck。獨立 headless browser 檢查 422 桌面畫面及 390×844 的404手機畫面，文字與兩個回復連結可見，沒有裁切／水平溢出版面；未以截圖取代 API／授權測試。瀏覽器測試工作階段已關閉。
6. 品質代理以 Poppler 檢查九張有效 PDF 樣本頁，確認修正後字型可見；不提交生成 PDF 或字型二進位，也不宣稱原始 corpus 已通過人類品質審核。

DB runner 的原始 test stdout／stderr／JUnit 不進 artifacts；JSON 只含固定欄位與數字，原始 XML 暫存後刪除。GitHub workflow 使用臨時隨機 fixture 憑證、唯讀 contents 權限、checkout 不留 Git credentials、unique compose project、always cleanup。這些措施不代表能限制不可信 PR 任意輸出，因此不提供真實密鑰或資料給 job。第一個遠端 run 仍須核對 startup／tests／artifacts／cleanup。

## 審查與修正紀錄

- CI 初稿只傳 `tests`，會漏掉 `evals/tests`；已補兩個目錄。
- 品質 PDF 一開始 CID-only 字型可被抽字但渲染空白，改為測試環境嵌入的 CJK TrueType。Windows 系統字型不複製入 repo，Linux 使用 WQY 字型，字型授權在盤點另記；不能把抽字成功當成來源渲染正確。
- CI helper 一開始在 child exit 0 但沒有報告時仍回成功；獨立審查重現後，新增三個 RED 測試，再修正為缺少／空／失敗報告不能成功，最終六項通過。
- 版本契約原有成功發布後重播的順序歧義；已明確為先重新授權、查冪等紀錄，再對新請求檢查 expected base。發布後合法同請求仍回原202，封存／失權不可重播。
- 獨立審查未確認 HTML handler 或版本鎖序的 P0–P2 缺陷；完整結果見 [本輪審查](round3-review.md)。

## 下一步

推送後確認遠端 CI 首次執行；依相依順序審查 PR #70／71／72，再安排新版服務與 RLS 的實作切片。版本契約中的 pending 限制、來源保存策略等建議要在實作 PR 定案；不可直接把原型接成上傳服務。人工題庫審核與專案 LICENSE 選擇仍需負責人完成。本輪只完成工程盤點，不代填這些決策。
