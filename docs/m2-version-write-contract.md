# M2-03／M2-04 新版建立與原子切換契約草案

日期：2026-10-20。**本輪交付設計，不新增 endpoint、migration、工作、檔案寫入或發布權限。** 本文件把 README 已定規則轉成可實作／驗收的介面，補充的端點與欄位是建議契約，實作 PR 必須同步 OpenAPI、schema 與教學。不能把本文件或現有直接管理 SQL 測試當作使用者已可建立新版。

## 既有能力與責任

- `documents.owner_id`：文件目前管理責任，決定誰可建立下一版；`document_versions.owner_id`：該版本責任，決定目前有效版的分享管理。兩者可能不同，不可以用「目前能讀」推導「能建立下一版」。
- `documents.current_version_id`：唯一有效指標，複合外鍵限制同 tenant／同文件；一般閱讀只看此指標與雙 ready，不查舊版 fallback。
- 現有 `queued/processing/ready/failed/archived` 狀態與版本號唯一約束已存在；但 jobs、原檔保存、完整性憑證及版本寫 API 都不存在。
- 003 只開 visibility 與 ACL 寫入。不得為了方便先給 web 整表 UPDATE／INSERT 或 owner 連線。RLS 規劃中的 auth/business split 與最低權限須一起考量。

角色矩陣：

| 行為 | 唯一可發起的身分 | 不可自動取得權限的身分 |
| --- | --- | --- |
| 建立新版本 | 同公司、有效、當下 `documents.owner_id` 的使用者 | 讀者、只有舊／有效版本 owner 關係的人、管理員、主管 |
| 分享目前版本 | 同公司、有效、目前 ready 版本 owner | 文件管理責任 owner 若不是該版 owner，仍不能分享該版 |
| 執行處理／發布 | 受限 worker／內部服務，持可信 job lease 與版本範圍 | 一般瀏覽器、管理員、可猜到 job ID 的使用者 |
| 讀取發布後正文 | 當下有效版的 owner／company／個別 ACL | 只有舊版 ACL 的讀者，不能回退讀舊版 |

新版本 owner 由後端設為建立當下的文件管理者，預設 private，沒有 reader ACL；不複製舊 owner、visibility 或 readers。後續交接是否覆蓋該版須依明列版本範圍處理，不在此自動推定。

## 建議 API

### 建立新版

`POST /api/documents/{document_id}/versions`，Cookie Session、CSRF 與 Origin/Referer 檢查。

建議正式接收 multipart：`file`（文字 PDF／UTF-8 TXT）、`expected_current_version_id`（正 bigint）、`csrf_token`；另要求 `Idempotency-Key`（16–128 個 ASCII 字母／數字／`-`／`_`，不得含帳密或正文）。`tenant_id`、`owner_id`、`version_number`、`status`、`visibility`、`reader_ids`、檔案路徑與費用均不接受使用者指定。實際容量、頁數、逾時與工作並行上限在 M3A 實作時定案；不可直接沿用原型值並宣稱已驗證。

所有資源都先驗證可信 Session tenant、文件管理責任及可處理狀態，再分配來源、版本與工作。授權後先依下方冪等規則處理既有請求，只有新請求才檢查 expected base 與 pending 限制。找不到／無管理權／封存一律相同 404；已授權新請求的有效指標不符回 409。未登入 401、CSRF/來源 403、格式／欄位錯誤 422、已核定容量超限 413、限流 429；不得把 DB detail 或他人檔名回傳。驗證失敗不可留下可見版本、可執行工作或付費呼叫。

首個 slice 建議限定「既有且有 ready 有效版的文件」，不在此處理第一份文件上傳或修復不合法 NULL 指標。後續新文件流程另定，不能藉 NULL 指標略過預期版本檢查。

成功 commit 後回 202：

```json
{
  "document_id": 10,
  "version_id": 28,
  "version_number": 2,
  "processing_status": "queued",
  "is_current": false,
  "job_id": "opaque-job-identifier"
}
```

這只是新工作排隊，不是已切換、已解析或可搜尋。未提交交易不回成功，也不派送工作。

### 狀態與重試

沿用規劃的 `GET /api/jobs/{job_id}`，只回該使用者當下有權管理的同公司文件／版本工作；工作建立者若已失去管理權，不能只憑建立者 ID 繼續存取。回應採白名單欄位（job_id、version_id、狀態、安全錯誤代碼、可重試與否），不回內部路徑、例外、正文或他人檔名。不在此開放一般歷史正文讀取。

`POST /api/jobs/{job_id}/retry` 必須重驗權限、封存、expected base、有限重試與預算。重試同一版本／相同來源，不重建 version_number，也不覆寫來源；新的 lease／attempt 必須使舊結果無法提交。舊工作失效不得被重新發布。發布本身是內部能力，**不提供任意指定 status=ready 或 current pointer 的公開 API**。

## 狀態與原始資料不變性

| 轉移 | 允許條件 | 前台效果 |
| --- | --- | --- |
| 建立 → queued | 有管理權、來源保存成功、同交易記錄版本與 job | 舊有效版持續可讀 |
| queued → processing | 合法可信 lease／attempt、未封存 | 舊有效版持續可讀 |
| processing → failed | 解析／品質／完整性／預算等失敗 | 舊有效版持續可讀，錯誤不揭露正文 |
| failed → queued | 同來源、授權仍有效、未過重試上限、base 未變 | 只重新排工作，不切換指標 |
| processing → ready 並切換 | 完整性通過、最新 lease、權責與 base 未變 | 同交易才可見新版；沒有新版 ACL 者拒絕，不回舊版 |
| 任意未發布工作遇到封存／權責失效／base 衝突 | 拒絕提交並使工作失效 | 不能重新啟用文件或覆蓋新有效指標 |

建議 jobs 以獨立 terminal reason 記錄 `base_conflict/archived/actor_inactive/ownership_changed/lease_expired` 等安全代碼，不用「重新把文件改 ready」表示重試。現有版本狀態 enum 沒有 cancelled；若需要新增，須 migration 與 API 同步，不能把草案欄位當現有 schema。

原始 bytes 與 SHA-256 保存後不可改；使用隨機儲存 key，不用使用者檔名組路徑。解析輸出只有未發布 attempt 能建立；ready／舊版正文不得覆寫。現有 content NOT NULL 不能假裝 queued 空字串是成功正文：未來 migration 應明定未完成輸出表示法及「ready 必須有完整正文」約束。若採獨立 processing output 表，就在發布交易複製一次到版本正文；若採 nullable content，就加對應 CHECK。實作 PR 應選一種並證明升級相容，本契約不預建兩套正文來源。

原型 `indexable=false`、未人工品質驗證的抽字結果不能作為發布成功憑證。M2 文字閱讀與 M3 向量索引應明確分階段：M2 測試可用受控內部處理替身驗證發布交易，但不得宣稱 PDF 上傳、worker 或搜尋已完成；M3 發布必須另外要求該版本所需 chunks／embeddings 完整，不能只因有文字就進一般搜尋。

## 冪等、版本號與並行策略

建議建立請求表／job 中保存 `(tenant, actor, document, operation, idempotency_key)` 唯一約束及 request fingerprint。Fingerprint 包含 source SHA-256、格式、expected base 與所有影響處理的設定；相同 bytes 但不同預期版本不算同一請求。

- 同 key／同 fingerprint：先重新授權文件管理權與可處理狀態，再查本 actor／document 的冪等紀錄；找到後直接回原本版本與 job 的相同 202，不額外排程或付費，**先於新請求的 expected base／pending 檢查**。即使原工作已成功發布、pointer 已前進，合法重播也不因此回 409；目前狀態由 GET 查詢。封存／停用／失去管理責任後不能用重播拿舊回應。
- 同 key／不同 fingerprint：授權通過後 409，不覆寫舊請求。
- 不同 key／相同來源：不自動等同同一個合法更新；須依同文件 pending-work 規則處理。建議首版同文件最多一個未完成更新；鎖內發現 pending 即 409，不接受多個更新互相覆蓋。
- version_number 在同文件鎖內分配，保留 `(document_id, version_number)` 唯一約束作最後防線；失敗版本不刪除／重用號碼，不能用無鎖 MAX()+1。

同文件最多一個 pending、重播回原 202、來源保存時序是本草案的實作建議；若採不同策略，須更新理由與對應並行驗收，不能以任意最後寫入者獲勝替代原子切換。

## 與分享及後續 RLS 共用的鎖定契約

使用短 READ COMMITTED 交易。處理文件的同一邏輯操作一次只鎖一份文件，避免批次跨文件鎖順序未定。建議如下：

1. 在不持有其他資源鎖時讀當前有效 ID，依 tenant／document 關聯取得 outgoing version 列鎖；不能直接鎖使用者傳入版本。
2. 再鎖同一文件列。此順序與分享先鎖 outgoing version 的方式一致；不能反過來先鎖文件再等待分享持有的版本列。
3. **鎖後另發新 statement** 重讀 pointer、文件狀態、文件／版本 owner、帳號有效性及 expected base。若 pointer 已變，放棄整交易／受限重試，不能持舊版本鎖繼續鎖另一個 current，避免兩個 publisher 互等。
4. 建立時在上述鎖內驗證／分配版本號與 pending job。發布時再鎖目標新版本與指定 job／lease，驗完整性；順序固定 outgoing → document → target → job。僅 worker 更新 job progress 的交易不得持 job 鎖再反向要求文件／版本鎖；發布須另開符合此順序的交易。
5. 所有前置成立才將目標 ready 與 document pointer 同交易更新，並完成 job。更新 pointer 仍加 expected outgoing 條件，要求 rowcount=1；任何一步失敗全部 rollback。

同一版本分享只鎖 v 並鎖後重讀文件，保持現有防護。封存／交接若新增寫入，須共同遵循 outgoing-first 契約及鎖後重查。帳號停用與目標授權仍有 READ COMMITTED snapshot 邊界；不能保證已讀內容追回。若要求與停用在提交瞬間嚴格序列化，須另設受限能力與帳號鎖協議，不能為鎖定就放寬 users UPDATE。

昂貴解析與任何外部服務都在交易外，不持鎖等待模型。來源檔與 PostgreSQL 不可視為同一原子交易：先寫隔離 staging／不可猜測 key，DB commit 後才使 job 可見；rollback 的未引用 staging 以獨立清理機制處理，不能刪已引用原始版本。crash window、重啟恢復、存儲成功但 DB 失敗及 DB 成功但 worker 尚未執行都必須測。

RLS 不取代鎖與流程驗證。新寫入應透過受限版本服務／發布能力，不把 INSERT versions、UPDATE documents.current_version_id 直接加給現在的 rag_app。與 [RLS 計畫](rls-implementation-plan.md) 協調下一個可用 migration 編號；不得修改已套的 001–003。身分來自驗證後的 backend context，transaction-local context 中途 commit 失效時不得再做業務 SQL。

## 實作前驗收矩陣

下列目前均為 **NOT_IMPLEMENTED／NOT_RUN** 的未來驗收，不是本次通過成績。

| 編號 | 情境 | 預期 |
| --- | --- | --- |
| V01 | owner 建立新版 | queued/private/空 ACL，原始指紋不變，舊版繼續可讀 |
| V02 | reader／admin／manager／僅版本 owner／跨 tenant | 統一拒絕，無版本／job／付費副作用 |
| V03 | 偽 tenant／owner／status／visibility／路徑；壞 CSRF | 拒絕，不改資料，不回他人存在性 |
| V04 | 新版處理中／失敗 | pointer 不變，舊內容與 ACL 保留 |
| V05 | 新版成功 private | 指標原子切換，沒有新版權限者 API／HTML／引用均拒絕，不回舊版 |
| V06 | 排隊中及發布後同 key 重播／改 fingerprint／兩次同時建立 | 合法同 fingerprint 重播在發布後仍回原 202；唯一版本／job／付費，衝突安全回 409，無重複版本號 |
| V07 | 完整性任一缺失／空正文／索引部分完成 | 不發布；M3 不出現在一般檢索 |
| V08 | 分享等待期間切版；發布等待期間分享 | 共享鎖與鎖後重驗，不能把新授權落在過期有效版 |
| V09 | 較舊 attempt 晚完成／重試後舊 lease 回傳 | 舊結果拒絕，不覆蓋新指標，不重複結算 |
| V10 | 處理期間停用／交接／封存／expected base 改變 | 發布重驗失敗，無資料復活或自動接管 |
| V11 | 正文／原檔／SHA 被覆寫；跨公司／錯文件目標 | DB／服務拒絕，原始與歷史資料保留 |
| V12 | 交易任一中間步驟崩潰、原檔與 DB 成敗不同 | 無半套 pointer；原檔不遺失，孤兒隔離可清理 |
| V13 | RLS missing context、pool 重用、直接越權 SQL | 依 RLS 矩陣拒絕，不沿用他公司身分 |
| V14 | 新裝／既有 M2-02 升級／初始化重跑 | 不損既有正文、ACL、有效指標，重跑無副作用 |

## 建議實作切片

先審查本契約的 owner 區分、pending 限制、冪等、鎖序及來源保存策略。接著在獨立測試庫先寫 V01–V06／V08 失敗案例，再建立最小版本服務與內部受控處理替身，證明交易與權限；此時不對外宣稱檔案處理完成。待受限儲存／jobs／品質閘門與預算能力準備好，再接正式 PDF／TXT worker 及 V07／V09–V14。每一切片同步教學與 RLS 政策，依實際驗收範圍更新 README，不預先勾完 M2 或 M3A。
