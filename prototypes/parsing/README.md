# PDF／TXT 解析原型

這是獨立、可丟棄的可行性原型，**僅對可信的虛構測試輸入執行**；不接 FastAPI、Session、上傳、資料庫、worker、索引或付費 API。不代表完成 M3A，也不接受使用者上傳檔案。即使解析成功，結果永遠 `requires_review=true`、`indexable=false`。

## 執行

在專案根目錄，以已安裝下列套件的 Python 執行；它們不是正式 requirements 的依賴。本次使用 Codex 隨附 Python，pypdf 6.10.0、reportlab 4.4.9；版本記錄用於重現本次原型，正式採用前仍需另評套件、安全更新及授權。

```powershell
python -m pip install -r prototypes/parsing/requirements.txt
python -m unittest discover -s prototypes/parsing/tests -v
python -m prototypes.parsing.parser prototypes/parsing/fixtures/sample.txt
```

CLI 向 stdout 輸出解析 JSON（包含文件文字），不寫結果檔。僅用虛構資料；不要把 JSON 當成一般日誌。失敗輸出穩定 error code，結束碼 2；成功結束碼 0 不表示可進索引。PDF 測試在記憶體建立兩頁繁體中文文字 PDF，沒有把生成檔放進 repository。

## 介面與來源定位

`parse_document(data: bytes, filename: str, *, limits: Limits) -> ParsedDocument`。`filename` 僅用副檔名判斷格式，不能當檔案授權或儲存路徑；SHA-256 針對原始 bytes。CLI 讀檔最多 max_bytes + 1，函式則接受呼叫者已載入的 bytes。

- TXT：只接受嚴格 UTF-8／UTF-8 BOM；拒絕二進位控制字元、replacement character 與純空白。LF／CRLF 的空白行分段，保留段內文字、金額、日期、料號、單位與換行；不做 Unicode 正規化、改寫或斷行合併。
- `paragraph:1` 起算；start／end 是移除 UTF-8 BOM 後 `source_text` 的 Unicode codepoint slice，不是 byte offset，也不是原始檔 line number。段頭尾空白不進 segment，但 source_text 原樣保留。只含 CR 的舊式換行目前不作為段落分隔規格。
- PDF：用 pypdf plain extraction，`page:1` 是實際檔案頁次，不是頁面印刷頁碼。每頁獨立 segment，跨頁內容保留相鄰實體頁，不在此原型合成跨頁 chunk；start／end 為 null。
- 任一頁無可讀文字則整份失敗，不回傳前面成功頁面。加密、錯誤 magic、毀損與不支援的 PDF filter 都回受控錯誤。沒有 OCR、表格重建、下載或外連。

## 限制與下一步

本原型預設檔案上限 2 MiB、PDF 20 頁、整份 200,000 字元；這是待評估的**原型值，不是已核定的產品輸入政策**。超限拒絕，不截斷後當完整文件使用。輸出文字限制在解碼／解析後檢查，不能因此宣稱 CPU／記憶體已受保護。

[pypdf 官方說明](https://pypdf.readthedocs.io/en/6.21.1/user/extract-text.html) 指出文字抽取的閱讀順序、表格、圖片與記憶體限制。小型壓縮 PDF 仍可能耗盡記憶體或長時間運算；本原型沒有程序隔離、硬記憶體限額或逾時 kill，不能直接放進 web request 或開放不可信檔案。其例外映射涵蓋目前測試過的錯誤，並不保證處理所有惡意 PDF。

抽到非空文字也不等於正確：多欄、跨頁表格、錯誤字型映射及複雜料件對應都未自動驗證。初版繁中測試只代表特定 CID 字型文字可抽取，不能代表任意中文字型；後續品質樣本另以嵌入字型產生並完成九頁代理視覺檢查，見品質文件。尚未完成人工品質簽核。正式流程須保存原始檔，人工或品質閘門確認閱讀順序及金額／料號／單位關係，無法確認就失敗，不進索引。

整合前待辦：選定解析器／授權、受限 worker 與硬資源限制、原始檔保存及 tenant/version 契約、解析品質指標與視覺來源對照、tokenizer／chunk 來源映射、狀態／去重／重試／封存防復活。這些前置完成前維持 `indexable=false`。

## 驗證紀錄（2026-10-20）

先建立測試與未實作介面，觀察缺少解析與限制檢查造成失敗；實作後 15 項通過。獨立審查以未知 PDF filter 重現 raw NotImplementedError，新增回歸先失敗，再映射 `unsupported_pdf_feature`。連同 CLI 成功／失敗案例，初版 **18 項 unittest 通過**；它們不連 DB 或模型。

後續新增 [10 個解析品質樣本](../../docs/parsing-quality.md) 與 8 項驗證，目前完整原型／品質測試為 **26 項**，在 Windows 與一次性 Linux 容器均通過；新增品質樣本需要可嵌入的 CJK 字型，依品質文件準備環境。正式應用程式的 112 項回歸及 120 子測試另行執行，不混稱原型驗證為產品驗收。
