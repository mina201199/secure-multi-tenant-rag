# 第三輪獨立程式審查（2026-10-20）

結論：對本輪 CI、HTML 錯誤頁、新版寫入契約、解析品質樣本及授權盤點的目前工作目錄版本，未發現尚未處理的 P0–P2 問題。這是獨立 AI 程式審查，不是人工簽核、GitHub approval、遠端 CI 成功或正式產品安全驗收。

## 範圍與方式

唯讀檢查 `.github/workflows/ci.yml`、`tools/ci/`、`app/errors.py`、註冊位置、錯誤模板及測試、`docs/m2-version-write-contract.md`、`prototypes/parsing/quality/` 與相關測試、`docs/dependency-licenses.md`。既有 RLS 準備與原始 parser 工作不一律歸為本輪新增。除本報告外，審查者未修改程式、提交、推送、對外傳訊或操作 Docker／資料庫。

## 已發現並確認修正

| 問題 | 證據與目前狀態 |
| --- | --- |
| CI 只收集 `tests`，漏掉題庫契約測試 | PostgreSQL job 現在明列 `tests evals/tests`，與完整驗收範圍一致。 |
| Linux 品質樣本缺少必要 CJK 字型 | offline job 現在安裝 `fonts-wqy-microhei`、設定 `PARSING_QUALITY_CJK_FONT` 並先驗證檔案可讀；來源產生器以該設定嵌入字型。 |
| helper 在 child exit 0 但缺少 JUnit 時仍回成功 | 審查曾以 mock child 重現 exit 0、無 counts；目前必須有非零測試數，缺報告／零測試回非零，報告存在 failures／errors 也拒絕成功。新增三項故障注入回歸測試已通過。 |
| 字型授權盤點沿用舊 CID 描述 | 文件已區分原始 MSung-Light CID 測試與新增 MSJhengHei／WQY 嵌入樣本，記錄來源、例外條款、環境差異及尚待核對事項，不宣稱已取得完整散佈核准。 |
| 發布後合法冪等重播與 expected base 檢查順序 | 契約現明定先重新授權、再查 actor／document 冪等紀錄，僅新請求檢查 base／pending；V06 加入發布後回原 202 的驗收。 |

## 其餘檢查

- HTML 錯誤只用固定文案，不反射 exception detail、提交值、URL 或文件資訊；無額外 Session／資料庫查詢。API／基礎設施路徑維持原 JSON handler；狀態碼與既有標頭保留，Accept 明確排除及權重處理合理。找不到與無權讀取的頁面共用回應。
- 新版契約的 outgoing version → document → target version → job 鎖序與目前分享流程相容；鎖後新 statement 重查，pointer 改變時整筆交易放棄／重試，避免持舊鎖再追鎖新的 current。文件明示帳號停用的 snapshot 邊界、來源與 DB 的非原子性，以及未實作的驗收，不冒充已可上傳／發布。
- 品質 gold 與來源產生文字分開維護，檢查料號／規格／數量／單位的局部關聯及來源位置；交換數量或頁次會失敗。多欄、表格與跨頁案例不自稱結構支援；所有結果維持 indexable=false、人工審核欄位留白。
- CI 結果輸出限數值白名單及固定 metadata，child 輸出丟棄，原始 JUnit 位於私有暫存目錄並清除；artifact 不包含正文／原始測試日誌。JUnit 的 tests 包含 subtests，summary 已以 count_unit 明示，不能當成 pytest collected items 數。

## 審查者獨立執行證據

使用 Codex 隨附本機 Python，不連 DB、不讀取 `.env`、不呼叫付費服務：

| 指令 | 結果 |
| --- | --- |
| `python -m unittest discover -s prototypes/parsing/tests -q` | 26 tests，OK |
| `python -m unittest tools.ci.test_run_tests tools.test_check_rls_readiness -q` | 10 tests（helper 6、readiness 4），OK |
| `python -m unittest discover -s evals/tests -q` | 10 tests，OK |

本審查未獨立重跑 PostgreSQL、Linux 容器或 GitHub Actions；主協調者另有 PostgreSQL 112 collected items／120 subtests、JUnit 232 cases、零 failures／errors／skips，以及 Linux WQY 26 項 parser 通過的執行證據。這些屬協調者回報，與上表審查者親自執行的證據分開記錄。遠端 workflow 尚須在實際 PR／push 後確認。
