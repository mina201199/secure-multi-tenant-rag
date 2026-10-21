# 第三階段：PDF、向量檢索與文件問答

日期：2026-10-21。這一階段把 M2 的版本工作擴充為「原始檔 → 解析 → 切塊 → 向量 → 有權限的證據回答」。實際驗收結果與尚未完成的外部模型驗證請看 [驗收紀錄](milestone-3-verification.md)。

## 目前怎麼操作

1. 開啟 http://127.0.0.1:8008/login，使用 `a.owner` 或 `b.owner`；示範密碼仍是 `Demo-Rag-2026!`。
2. 選「上傳文件」，輸入名稱、分類並選 PDF／TXT。預設私人。
3. 在「我管理的文件」重新整理查看處理狀態、指紋與段落數。來源原始 bytes 存於受保護的 PostgreSQL 資料表，沒有公開檔案路徑。
4. 索引完成後設定公司或指定人員分享。管理頁可貼上文字或上傳檔案建立新版；新版仍預設私人。
5. 「文件問答」輸入問題。回答以原文引句及來源顯示，引用包含文件、實際版本、PDF 頁次或 TXT 段落。
6. 引用連結每次重新驗權。撤權、封存或新版本取代後，舊引用不能繼續開啟。

範例放在 `output/pdf/company-a-policy.pdf`、`output/pdf/company-b-policy.pdf`。兩家公司每日補助分別為 NT$1,500 與 NT$3,000；第二頁為料號、規格與單位。全部是虛構資料，已檢查四頁的視覺輸出。

## 啟用真實模型

本機沒有金鑰時，PDF 仍可上傳及解析，但向量階段會明確失敗，舊版本維持可用；問答回傳 503，不能冒充模型已成功回答。一般測試只使用可控替身。

在專案根目錄建立 `.env`，參考 `.env.example`：

```dotenv
OPENAI_API_KEY=在本機填入你的金鑰
OPENAI_ENABLED=true
OPENAI_PRICE_REVISION=2026-10-21
```

不要把真實金鑰提交 Git 或貼到對話。Compose 把這三個欄位提供給 web／worker，沒有提供給瀏覽器、測試服務或 PDF 解析子程序。請只使用可分享的虛構文件：啟用後，文件段落會送到 OpenAI Embeddings，問題及授權候選會送到 Responses API。

```powershell
docker compose up --build -d --wait
```

啟用後，在管理頁重試先前失敗的工作。最多三次工作嘗試；沒有無限自動重試。已有 M2 文件會在 migration 建立一次索引工作，原文與閱讀權不變；後續初始化不重複新增工作。

本階段使用固定模型，不自動升級或改用更貴模型：

| 用途 | 設定 |
| --- | --- |
| Embedding | `text-embedding-3-small`、1536 維、L2 正規化 |
| 回答 | `gpt-5.4-mini`、Responses API、低 reasoning、最多 1024 輸出 tokens |
| 回答形式 | 結構化選取直接支持問題的原文引句，後端逐字驗證後組成回答 |
| Tokenizer | `tiktoken` 的 `cl100k_base` |
| 切塊 | 每段最多 600 tokens，約 60 tokens 重疊；調整邊界避免拆壞中文字 |
| 檢索 | pgvector 精確 cosine 搜尋，最多 Top-5，cosine distance < 0.85 |

距離門檻是初始保守設定，尚非經 M4 題庫調校的語意品質保證。模型可能選不到足夠證據；系統會拒答，而不是補寫沒有來源的結論。

已於 2026-10-21 查閱 [OpenAI Embeddings](https://developers.openai.com/api/docs/guides/embeddings)、[結構化輸出](https://developers.openai.com/api/docs/guides/structured-outputs)、[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) 及 [Embedding 模型](https://developers.openai.com/api/docs/models/text-embedding-3-small)。官方模型存在不等於目前帳號具有權限；實際帳號可用性仍須以小額整合驗收確認。

## 解析與品質界線

| 項目 | 上限或行為 |
| --- | --- |
| PDF | 10 MiB、50 頁、文字型單欄 |
| TXT | UTF-8、1 MiB，不接受 NUL |
| 解析文字 | 60,000 Unicode 字元、24,000 tokens、最多 1,000 來源段落 |
| 切塊總量 | 最多 64 段 |
| 問題 | 2–1,000 字 |
| PDF 子程序 | 25 秒牆鐘、20 秒 CPU、768 MiB 位址空間；不繼承金鑰或 DB 連線 |
| 外部 API | 連線 5 秒、I/O 30 秒、無自動重送、無重新導向 |
| Worker | 每程序一次一份工作、300 秒租約、最多 3 次嘗試 |

Parser 使用 [pdfplumber](https://github.com/jsvine/pdfplumber)。清洗只處理換行與行末空白，保留數字、金額、日期、標點、料號及內部空格。PDF 以檔案實際頁次為來源單位，chunk 不跨頁；TXT 以空白行分段，該版本的段落編號固定。

加密、損壞、空白／掃描頁、缺少字元對應、偵測到表格、多欄大間隙或大幅影像會失敗。這些是明確的品質檢查，不能保證識別所有複雜排版；複雜表格、跨頁表格與 OCR 仍不在範圍。料件清冊請採逐筆文字，例如「料號：A-17；品名：固定螺栓；規格：10 mm；單位：個」。

原始檔、解析結果與來源位置分開保存。解析結果可在重試時沿用；段落與 manifest 在同一交易保存，只有完整數量符合時才能完成版本切換。解析／Embedding 失敗不能覆蓋舊版；封存取消租約，晚到的 worker 不得重新啟用文件。

## 權限的四道檢查

1. **檢索前**：有效 Session 與帳號；SQL 明確要求同公司、目前版本、ready、未封存與閱讀 ACL，資料表還有 RLS。
2. **送模型前**：用新的資料庫交易重新查全部候選，確認版本、文件 revision、內容與權限未變。
3. **答案核對**：來源 ID 必須是實際候選，quote 必須逐字存在且長度合規。任何一筆捏造就捨棄答案。回答只含驗證過的引句，模型自由文字不會原樣交付。
4. **交付前／點引用時**：再次查權限；所有送給模型的來源都要有效，包含未被引用的候選。變更時回傳通用錯誤，沒有部分答案。

管理員、主管、版本擁有者的歷史授權規則沿用 M2。歷史授權不會加入一般檢索；即使切換 history GUC，chunks 也只允許目前版本。模型沒有資料庫憑證、SQL、工具或瀏覽能力，文件指令只是資料；所有引句由 Jinja 跳脫。

新增 `source_files`、`parsed_sources`、`chunks`、`index_manifests`、`model_usage`，合計 17 張表強制 RLS。web／worker 仍沒有 BYPASSRLS、表擁有權或一般寫入權；變更只經檢查身分與範圍的固定 `SECURITY DEFINER` 函式。受信任 web、worker 程式及最高權限維運者仍是既有安全邊界。

## 預算與失敗計帳

平台所有公司共用每月 NT$300 上限，在估算達 **NT$270** 前停止新呼叫，留下餘額緩衝。月份以台北時間計算。

本版預留採 Embedding 每百萬 tokens USD0.02、回答輸入 USD0.75／輸出 USD4.50，以保守換算 USD1=NT$40，再乘 1.25 緩衝。這是保護性估算，不是即時匯率或實際帳單；後續變更費率要更新程式、測試及 price revision，不能只改環境值冒認已核價。

每次外呼前先以資料庫全域鎖預留最大成本並提交。所有 web／worker 共用同一帳務，最多 4 筆進行中的模型呼叫；每帳號每分鐘最多 20 次。成功依 API token 用量結算一次；逾時、未知是否扣款或程序中斷保留整筆預留，不自動釋放或無限重送。新的手動工作重試若真的再呼叫 API，會有新帳務，不會把舊的未知扣款抹掉。

保留 `model`、token、價格版本、估算／結算費、耗時與狀態，不保存問題或回答正文。`/api/usage` 只可看自己的問答用量；worker 用量留給維運稽核，不提供公司管理員全域帳務。殘留 reserved 的對帳須由可信維運者核對供應商紀錄，不能盲目釋放。

這個上限只涵蓋本應用經閘門發出的呼叫，不能控制相同 API key 被其他程式使用的支出。請使用專用 project/key，並在供應商後台另設預算。

## API

所有變更及可能付費的查詢都需要有效 Session、`X-CSRF-Token` 與正確 Origin。JSON 不接受多餘欄位。

| 方法與路徑 | 用途 |
| --- | --- |
| `POST /api/documents` | multipart `file`、`title`、`category`，新增私人文件 |
| `POST /api/documents/{id}/upload` | 上傳私人新版本；只有目前擁有者 |
| `POST /api/documents/{id}/index` | 為缺少索引且沒有既有工作的目前版本建立工作 |
| `GET /api/jobs/{id}` | 擁有者查看 `status`、`stage`、嘗試次數與安全錯誤 |
| `POST /api/jobs/{id}/retry` | 沿用 M2 的有界重試 |
| `GET /api/inventory` | 文件／版本管理者的來源、指紋、格式、分類及處理盤點；無原文 |
| `POST /api/search` | `{"question":"..."}`，最多五個授權段落 |
| `POST /api/ask` | `{"question":"..."}`，證據回答、refused、citations |
| `GET /api/citations/{id}` | 再次驗權的來源段落 |
| `GET /api/documents/{id}/download` | 下載當前版本原始檔；貼上文字則下載 TXT |
| `GET /api/usage` | 自己最近 50 筆問答／查詢用量 |

頁面入口是 `/upload`、`/ask`、`/citations/{id}`；版本狀態仍在 `/manage/{id}`。不串流、不共用答案快取、不保存可重開聊天記錄。

## 測試與小練習

```powershell
docker compose --profile test run --build --rm tests
```

測試只能連到 `test-db/rag_test`，不能刪除本機示範資料。模型替身只在 `APP_ENV=test` 且 `RAG_TEST_PROVIDER=true` 生效，本機服務無法只靠後一個變數啟用替身。

實際 HTTP 測試必須先完成上述資料庫初始化與完整測試，再啟動專用服務；不可在 pytest 正重設資料庫時同時啟動：

```powershell
docker compose --profile http-test up -d test-web test-worker
docker compose --profile test run --rm --no-deps tests python -m tests.smoke_milestone3
docker compose --profile http-test stop test-web test-worker
```

測試網站綁 `127.0.0.1:18008`，頁面有明確替身標示；正式本機入口仍是 8008。測試完成應停止 18008 的服務。

練習：將甲公司的 PDF 分享給 `a.reader`，用該帳號問補助，再撤權並重開剛才的引用。應得到 404；接著上傳私人新版，確認舊讀者無法退回舊版搜尋。能用自己的話解釋 API、RLS、模型前與交付前四個檢查點，即完成本階段教學回述。
