# 虛構 PDF／TXT 解析品質樣本

本次增加十個可重現的虛構樣本與離線品質檢查器，範圍限於 `prototypes/parsing/quality/`。不接正式應用、DB、Docker、模型或索引；執行不需網路。生成器只回傳記憶體 bytes，不提交 PDF、字型或其他生成二進位。

所有結果一律 `review_required=true`、`indexable=false`。`supported_basic` 只代表目前受控樣本的完整單行料號關聯與來源位置通過檢查，不表示正式文件可入庫，也不是人工審核完成。

## 執行與重現

從專案根目錄執行，依賴沿用 `prototypes/parsing/requirements.txt`（pypdf 6.10.0、reportlab 4.4.9）：

```powershell
python -m unittest discover -s prototypes/parsing/tests -v
python -m prototypes.parsing.quality
```

品質 CLI 向 stdout 輸出虛構 facts、逐項檢查及分類 JSON；所有分類與預期相符才回傳 0。複雜樣本被正確標成需要人工比較，也屬於「符合預期」，不是解析品質通過。

繁中 PDF 會嵌入本機 TrueType 字型，避免 CID-only 字型在未裝 CJK font pack 的 renderer 顯示空白。Windows 預設為 `C:/Windows/Fonts/msjh.ttc`，不散布該字型。其他環境須以 `PARSING_QUALITY_CJK_FONT` 指定具繁中字形的 `.ttf`／TrueType `.ttc`；沒有字型會明確失敗，不跳過測試。Noto CJK 的 PostScript OTF 不能直接視為本生成器可用的替代品。

Ubuntu CI 可在環境準備階段安裝 `fonts-wqy-microhei`，再設定：

```sh
export PARSING_QUALITY_CJK_FONT=/usr/share/fonts/truetype/wqy/wqy-microhei.ttc
python -m unittest discover -s prototypes/parsing/tests -v
```

套件的 [字型路徑清單](https://packages.debian.org/bookworm/all/fonts-wqy-microhei/filelist) 與 [來源授權宣告](https://sources.debian.org/src/fonts-wqy-microhei/0.2.0-beta-2/debian/copyright) 可供環境準備核對；來源宣告 Apache-2.0 或 GPL-3+ with Font exception。字型僅屬測試生成環境，不是正式 parser 依賴。這次本機驗證使用 Windows Microsoft JhengHei；Linux 字型相容性須由 Linux runner 實際驗證。

`invariant=1` 固定 PDF metadata／識別資訊；同一字型檔及相同 Python／套件版本下，重複生成的 bytes 相同。不同字型或套件版本不保證 SHA-256 相同，不能把跨環境 bytes 相等當成來源品質判斷。

## 樣本與預期分類

| Case ID | 來源與檢查重點 | 預期分類 |
|---|---|---|
| `txt_utf8` | UTF-8、LF、兩段相鄰料號 | `supported_basic` |
| `txt_bom_crlf` | 真實 BOM bytes、CRLF、前置空白與 source slice | `supported_basic` |
| `pdf_basic` | 繁中單欄，兩個料號分別在實體頁 1／2 | `supported_basic` |
| `pdf_columns` | 雙欄，繪製 stream 交錯兩個欄的標題與細節 | `needs_manual_quality_review` |
| `pdf_table` | 兩列表格；stream 按欄寫入，不能把 token 全在當成列關聯正確 | `needs_manual_quality_review` |
| `pdf_cross_page` | A-1001 的數量／單位承前頁；同頁另有 A-1002 | `needs_manual_quality_review` |
| `pdf_malformed` | 有 PDF magic 但結構毀損 | `rejected`／`invalid_pdf` |
| `pdf_no_text` | 只有圖形、沒有文字層 | `rejected`／`unreadable_page` |
| `pdf_mixed_blank` | 第一頁有文字、第二頁空白，整份拒絕 | `rejected`／`unreadable_page` |
| `txt_invalid_utf8` | 非有效 UTF-8 bytes | `rejected`／`invalid_utf8` |

樣本固定區分 `A-1001 / M8x30 / 12 / 支` 與 `A-1002 / M8x35 / 21 / 盒`。料號接近，但規格、數量及單位不同，交換其中任一關聯不能靠整份文件的 token presence 通過。繁中欄名與單位也必須保留。

## Gold 與判定限制

`quality/manifest.json` 是獨立編寫的 gold；不從 parser 輸出反推期待值。每個 fact 記錄 `locator`、來源段落或 PDF 頁面／座標描述，以及完整關聯文字。表格 gold 使用帶欄名的完整記錄表示視覺列關聯；這個記錄不是聲稱來源 PDF 具有該段 literal text，也不是宣稱 parser 已重建表格。

檢查器在指定 `page:N` 或 `paragraph:N` 中比對完整關聯，避免只比對全文件中的孤立數字或料號。TXT 另外驗證 `source_text[start:end] == segment.text`；BOM 只在 decode 時移除，CRLF 與段內原文仍保留，offset 是 Unicode codepoint，不是 byte。

雙欄與表格目前沒有可靠的閱讀順序／欄列重建，因此即使抽出非空文字，也不宣稱支援。表格例子中的八個主要 tokens 都能抽出，但完整列關聯檢查仍不通過。跨頁例子的局部 facts 全通過，仍維持需要人工比較，因為頁面片段不等於跨頁關聯完成。`automatic_structure_supported` 只會對通過完整關聯與來源 slice 檢查的 basic layout 回傳 true。

這些是小型合成 regression fixtures，不代表真實文件分布、掃描 OCR、合併儲存格、直排、字型編碼問題或對抗性 PDF 的品質。CPU／解壓縮資源隔離仍不在本原型範圍。異常／無文字頁面採整份拒絕，不以部分成功冒充完整結果。

## 人工比較與驗證記錄

所有 `human_review.reviewer`、`reviewed_at`、`decision` 都維持 null。自動檢查及 agent 對合成 fixture 的視覺檢視不是人工簽核；本次沒有填入任何人工同意、入索引或正式採用結論。

後續人工比較應先看原始頁面／TXT bytes 與定位描述，再逐筆確認料號、規格、數量、單位及閱讀順序。複雜表格需確認同列、同欄及跨頁關聯，不能只確認「每個詞都有出現」。即使人工確認這批樣本，也需另行決定正式系統規則，這個工具不會發布索引。

測試先寫再實作：初始七項測試因缺少品質模組失敗，實作後通過。視覺檢視另發現 CID 字型能抽字但渲染空白；新增嵌入字型 regression，觀察失敗後改為 TrueType 嵌入。最新八項品質測試涵蓋交換數量、正確 fact 放錯頁、BOM／CRLF 原文、複雜來源拒絕自動結構通過、固定錯誤碼、bytes 重現、字型嵌入及空白人工審核欄位。

本機將六種有效 PDF 生成於暫存目錄，用 Poppler `pdftoppm` 渲染全部九頁並檢視：繁中文字可見、雙欄分開、表格列位置正確、承前頁文字可見，另保留預期的圖形頁與空白頁。這是合成樣本作者的版面驗證，不是輸入文件的人工品質核准。
