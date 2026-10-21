# 第三階段驗收紀錄

日期：2026-10-21（Asia/Taipei）。範圍包含 M3A 文件處理、M3B 模型介面與預算、M3C 授權檢索與引用。**真實 OpenAI API 驗收尚未執行**：本機未設定金鑰，`OPENAI_ENABLED=false`；沒有將模型替身當作真實模型品質證據。

## 已完成的驗證

| 驗證 | 實際結果 |
| --- | --- |
| M2 表單與 worker 租約相容檢查 | 2 passed，1 warning，5.85 秒 |
| 第一輪 M3 新案例 | 42 passed、1 failed，73.03 秒；錯誤測試預期與修正見下方 |
| 三階段完整回歸 | 139 passed，1 warning，135.53 秒 |
| 最終完整回歸（含舊工作 migration） | **140 passed，1 warning，134.61 秒** |
| 真正 HTTP + 獨立 worker + PostgreSQL | **46 次檢查通過**，模型替身，不付費 |
| 升級後既有 HTTP 權限回歸 | **10 帳號登入／清單／登出及 140 次文件 API／頁面檢查通過** |
| 瀏覽器 | 問答表單、繁中證據、PDF 第一頁引用與來源頁；8008 新版上傳入口正常 |
| PDF 視覺及解析 | 兩家公司、每份兩頁，四頁檢查正常；繁中金額、日期、料號、規格、單位與實際頁次均納入測試 |
| 本機升級 | 升級前備份完成；setup Exited(0)，web／db 健康，worker 運行 |
| 實際付費呼叫 | **0**；本機 `model_usage` 為零筆 |

Starlette TestClient 的 httpx 棄用警告仍存在，沒有隱藏。pip 建置時 root 安裝套件是映像建置步驟；執行中的服務使用既有非 root `rag` 使用者。

完整回歸：

```powershell
docker compose --profile test run --build --rm tests
```

真正 HTTP：

```powershell
docker compose --profile http-test up --build -d test-web test-worker
docker compose --profile test run --rm --no-deps tests python -m tests.smoke_milestone3
docker compose --profile http-test stop test-web test-worker
```

已取得的 HTTP 輸出：

```json
{
  "result": "PASS",
  "model": "TEST DOUBLE; no paid OpenAI calls",
  "http_checks": 46,
  "document_ids": [2000000014, 2000000015],
  "a_sample_ready": true
}
```

資料只在隔離 `test-db/rag_test`。甲公司範例供瀏覽器截圖，乙公司範例於測試中封存，驗證舊引用 404 與問答拒答。截圖完成後已停止臨時 18008 web／worker；後續完整回歸會重設這個測試資料庫，文件 ID 不應視為永久示範連結。

## 權限與越權涵蓋

| 主題 | 已驗證 |
| --- | --- |
| 上傳 | 未登入、缺少 CSRF、假租戶欄位、非擁有者更新、跨公司更新均拒絕 |
| 輸入 | 副檔名／MIME、PDF magic、空檔、10 MiB、UTF-8、NUL、TXT 大小與多餘欄位 |
| PDF | 損壞、空白頁、超頁數、表格、多欄失敗；繁中與英文解析、文字來源範圍、受限子程序逾時 |
| 隔離 | 私人／公司／指定分享；管理員、主管、同公司無 ACL、另一公司均不能繞過 |
| RLS | 原檔、解析文字、chunks、manifest 無身分／偽造情境讀不到；runtime／worker 原始寫入被拒絕 |
| Worker | 無租約／已完成租約不能讀來源；來源解析可重用，重試不重複建 chunks；沒有完整索引不能標示成功 |
| 來源關聯 | 跨公司來源外鍵失敗，原檔不可改寫；向量與來源 offsets、頁次、段落一致 |
| 搜尋 | 精確 Top-5，授權候選不足不補未授權資料；兩家公司相同問題不混入另一家金額 |
| 模型邊界 | Spy 捕捉送出的上下文，確認無另一公司內容；送模型前撤權時不送出上下文 |
| 回答競態 | 模型處理中撤權、帳號停用、封存、換版、Session 撤銷，全數丟棄答案 |
| 引用 | 捏造 ID、修改金額、不存在引句、錯誤 JSON 或無證據都拒答；歷史權限不讓舊引用復活 |
| Prompt Injection | 惡意來源無法加入工具或另一公司內容；僅交付逐字驗證的原文證據；HTML 跳脫 |
| 預算 | 不同公司的並行預留共用上限；超額拒絕、最多 4 筆並行、未知扣款保留、只結算一次、用量跨公司隔離 |
| 無金鑰 | 明確 503 與工作錯誤，舊版可用；設定後有界重試可接續，沒有假成功 |
| 升級 | M1 → 新版 migration 可重跑、內容及 ACL 保留；M2 舊工作不會被標為已完成向量索引 |

權限通過表示本次已定義案例的未授權成功次數為零，不是形式證明或正式企業環境的安全認證。

## 已觀察失敗及修正

第一輪 M3 測試 `test_private_pdf_cannot_enter_search_model_or_citations[b.owner]` 失敗：斷言錯誤要求乙公司擁有者的盤點為空。實際返回的是乙公司自己原有的三份文件，沒有甲公司內容；這符合權限規格。修正為「盤點不包含甲公司目標文件」，保留該案例及其他跨公司檢查，之後完整回歸通過。

本機 HTTP 回歸第一次在沙箱內無法連線 localhost；改用獲准的本機執行權限後完成 140 次檢查。此為工具網路限制，未改動應用安全規則。

最終只讀檢查發現 M2 舊工作 `status=done` 搭配新欄位預設 `stage=queued`。新增 `005_legacy_job_stage.sql` 將舊完成工作標成 `legacy_complete`，不偽裝為 `searchable`，並加入專門的升級測試。

## 本機保護與目前狀態

升級前備份：`data/before-milestone-3-20261021.sql`，64,565 bytes，位於 Git 忽略的資料夾。沒有刪除本機資料卷，原有六份文件及 M2 封存範例保留。

只讀確認：

- 17 張資料表同時啟用 RLS 與 FORCE RLS。
- web 沒有 `DATABASE_ADMIN_URL`、`WORKER_DATABASE_URL` 或測試替身設定。
- web 的 OpenAI 金鑰為空，`OPENAI_ENABLED=false`。
- 六份既有文件已解析並保留來源對應，但因沒有啟用模型，索引工作失敗且可手動重試；原本文件仍可閱讀。
- 本機付費帳務零筆。測試用量在隔離測試資料庫，與本機預算無關。

上述驗收完成時，M1／M2／M3 變更尚在本機工作區。後續交接將這些程式、測試及教學一併納入版本控制；提交版本以 Git 歷史為準。遠端 Issues 尚未同步關閉，完成範圍以 README 為準。

## 尚未完成

1. 本機安全設定 OpenAI 金鑰，確認該帳號能使用指定模型，再執行虛構文件的小額真實 API 驗收。README 的 M3B-05 保持未勾選。
2. 真實模型的檢索相關性、拒答、Prompt Injection 抵抗與中文答案品質，需要 M4 固定題庫與人工抽查。替身僅驗證管線及權限控制。
3. OCR、複雜／跨頁表格、其他格式、Hybrid、Reranker 與多模型比較沒有納入本階段；對應 M4 範圍另見 README。

## 畫面

測試服務的證據回答，畫面明確標示模型替身：

![RAG 測試畫面](milestone-3-rag-test.jpg)

已升級的本機 8008 上傳頁：

![PDF 上傳](milestone-3-upload.jpg)
