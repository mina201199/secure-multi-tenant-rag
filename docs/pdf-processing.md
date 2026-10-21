# PDF 正式處理與驗收

本分支沿用 TXT 的版本、持久化工作、專用 worker 登入與失聯恢復。上傳先回傳 202／queued，解析成功後才在同一筆交易保存全文、實際頁次、parser 版本並切換有效版。新版為私人且不繼承分享；失敗或中斷時舊版維持可讀。沒有新版權限者不能回退舊版。

## 支援範圍

- TXT 維持 UTF-8、1 MiB、200,000 字元。PDF 為 2 MiB、1–20 個實際檔案頁、全文含頁間換行最多 200,000 字元。
- 副檔名與 `%PDF-` 開頭必須一致，不信任 MIME；結構驗證只在 worker 執行。
- PDF 僅接受原生、黑色、單欄、由上往下且左緣一致的文字。保留繁體中文、數值、料號及頁次，不做 OCR 或語意改寫。
- 加密、空白／掃描頁、圖片、表單／附件／動作、旋轉、圖形、裁切、雙欄、定位表格與無法驗證的文字會失敗。字寬採保守上界，檢查文字落在 CropBox／MediaBox 交集且行間不重疊；每個 PDF 文字物件限一次繪字操作，拒絕非零 TJ 位移與合併文字區段。長行、縮排、頁碼、標題、同一行多次繪字或不同工具產生的正常 PDF 也可能超出此保守格式；可改用已核對的 UTF-8 TXT。
- 這不是任意 PDF 版面正確性的證明。PDF 本身未必包含可還原的閱讀順序；複雜表格、跨頁欄位關係與代表性真實文件評估仍待後續。

## 執行與恢復

沿用 [worker 啟動方式](txt-worker.md)，更新程式後重新建置並執行 setup 套用 migration 006，再啟動 worker。請先備份既有資料庫，依現有部署流程操作：

```powershell
docker compose build setup tests
docker compose run --rm setup
docker compose --profile worker up -d web worker
```

以文件擁有者登入 `/login`，從文件的版本管理上傳 `.pdf`。重新整理工作狀態；ready 後閱讀頁會顯示「第 1 頁」等實際檔案頁次。API 的 `source_pages` 是連續 1 起算的 `{page,text}` 陣列，`parser_version` 為 `pypdf-6.21.1-v1`；TXT 兩欄為 null。頁次不是 PDF 上印出的頁碼，也還不是 RAG 引用。

worker 領取先 commit，解析不占 DB 交易。5 分鐘租約、10／20 秒退避與最多 3 次領取均沿用 TXT。停止訊號會在解析結束後放棄發布；程序被殺後由下一個 worker 回收同一筆工作，新 token 取代舊 token。確定的解析失敗為 terminal，不無限重試。沒有額外 queue、直接 worker 資料表權限或自動主機重啟服務。

DB 發布能力重新驗證租約、擁有者、帳號、封存狀態、基礎版本、來源格式與 SHA-256，並獨立驗證頁次及文字形狀。六參數 TXT 建立能力與舊指紋不變；升級前已排隊的 TXT 可繼續執行。

## 解析程序邊界

正式 parser 使用 pypdf 6.21.1，在 Linux x86_64／aarch64 子程序執行。環境不繼承 DB／Session 憑證，關閉繼承描述符；父程序死亡時終止子程序。限制 512 MiB 位址空間、10 秒 CPU、15 秒 wall time、2 MiB 輸入及 1,300,000 bytes 輸出，禁止 core dump。seccomp 拒絕網路、另開程序與列出的跨程序操作；無法設限時拒絕解析。

這是資源與程序限制，**不是完整檔案系統沙箱**：子程序仍以同一容器使用者讀取可見檔案，syscall 採阻擋清單。不得把此機制視為防禦任意 parser 程式碼執行的完整隔離。部署不要額外掛載秘密檔案；目前 worker 僅配置專用 DB 登入。沒有 RLS／OCR／chunks／embedding／RAG／付費呼叫。

錯誤只保存固定代碼，不輸出 parser stderr、原文、SQL 或 DSN：

| 代碼 | 意義 |
| --- | --- |
| `invalid_pdf` | 結構或子程序回應無效 |
| `encrypted_pdf` | 加密文件 |
| `unreadable_page` | 空白、無文字或不安全字元 |
| `unsupported_pdf` | 超出保守支援格式 |
| `pdf_limit_exceeded` | bytes、頁數、字元或輸出超限 |
| `pdf_timeout` | wall timeout |
| `pdf_resource_limit` | 記憶體／CPU／訊號終止 |
| `pdf_parser_unavailable` | 平台或限制設定不可用 |

## 驗收紀錄

2026-10-21：完整 PostgreSQL／HTTP／worker／parser／題庫回歸 **369 passed、120 subtests passed**；僅既有 Starlette HTTPX deprecation warning。離線 readiness／CI runner 工具 **12 tests OK**。獨立原型需其專用 pypdf／ReportLab 環境，本機一般 Python 未安裝 ReportLab；[PR #76](https://github.com/mina201199/secure-multi-tenant-rag/pull/76) 的 offline CI 已通過獨立原型驗證，最新 commit 的完整 CI 狀態以 PR checks 為準。

新增 116 項案例涵蓋實際中文／數值／頁次抽取、錯誤 PDF、CPU／記憶體／wall／輸出限制、無憑證與網路繼承、資料庫能力與升級、原子發布、舊權限撤回、跨公司／CSRF、失聯恢復與停止後不發布。測試全部使用虛構內容。

```powershell
docker compose --profile test run --rm tests pytest -q -p no:cacheprovider --tb=short
python -m unittest tools.test_check_rls_readiness tools.ci.test_run_tests -q
```

先寫測試時確認缺少 PDF SQL、parser 與上傳支援會失敗。首次整合發現 Docker 中合法父程序 PID 1 被誤拒，改以實際父 PID 比對；兩個 HTTP 測試切換帳號未清除 Cookie，改為清除並確認登入成功後再驗證權限。修正後完整回歸通過。既有 M1／M2 與 TXT 驗收仍見 [前輪整合紀錄](pr-stack-acceptance-2026-10-21.md)。

獨立程式複核發現並修正白色／零字級、裁切／越界、行重疊及 PDF 原文被 TXT signature 誤拒四類問題。新增六個回歸案例先觀察失敗，再修正；從空 test-db 套用 migration 並用重建映像先通過 366 項；追加文字操作限制後，唯讀掛載最終 app／tests 重跑，得到上述 369 項結果。解析器單獨案例共 40 項（包含在完整回歸）通過。

追加複核重現同一區段內重定位重疊與 TJ 累積位移越界／重疊；三個回歸案例先失敗，再限制文字物件繪製操作與非零位移，防止 visitor 合併掩蓋幾何資料。
