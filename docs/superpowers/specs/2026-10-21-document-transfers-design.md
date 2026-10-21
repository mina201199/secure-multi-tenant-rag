# 文件交接設計（#21～#23）

沿用決策稿第八節與使用者已授權的交接功能範圍。2026-10-21 使用者另確認：申請後原擁有者被停用，也必須拒絕核准；恢復有效後須重新提出。

## 範圍與決策

一張申請涵蓋一份檔案，必須包含提出時的目前版本，可明確加入同一擁有者的 ready 歷史版本（最多 100 版）。未選歷史版本及所有一般／歷史閱讀 ACL 保持原樣。核准更新檔案擁有者與選定版本擁有者，不自動授予歷史閱讀權。原擁有者依既有 ACL／company 可見性繼續閱讀，不再具有所移轉範圍的管理權。

僅目前檔案擁有者能提出；原擁有者、固定直屬主管、接收人都須同公司且有效。接收人不得是原擁有者或審核主管。ready、未封存檔案才可交接；有 queued／processing 版本時先等待處理結束。申請範圍、接收人、原因提出後不可編輯；有效期 7 天，過期重新提出。

以申請列鎖防重複核准／拒絕。核准在單一交易內鎖定 outgoing → document → selected history（NOWAIT）→ users，重查固定主管、帳號有效性、公司、擁有者、版本狀態、有效版本與申請到期。無法取得歷史鎖回 409，避免與既有歷史／發布流程死結。任何項目無效、更新或稽核失敗，一律全部回滾。

保存申請時的檔案／版本擁有權修訂與三位帳號狀態修訂；資料庫 trigger 在 owner／is_active／manager_id 改變時遞增，避免移轉往返或停用後再啟用讓舊申請復活。核准前比較修訂。不得因讀取待審頁就移轉。

主管僅能看目前由自己負責的 pending 申請：申請 ID、提出人、候選接收人、原因、期限與狀態。不得回傳檔案 ID、版本 ID、檔名、摘要或正文。提出人能看自己的範圍與結果；接收人不因被提名取得案件或內容存取權。原因由人填寫且不預填內容。主管完成操作只收到申請 ID／結果，已結案不再出現在主管清單。

## 檔案與契約

- 新增 `app/migrations/009_document_transfers.sql`：新表、修訂 trigger、受限 NOLOGIN 能力角色與原子操作；001～008 不改。
- 新增 `app/transfer_service.py`：固定 SQL wrapper，映射 P0002→404、40001→409、22023→422。
- 公開 DB 函式：`get_transfer_options(tenant,actor,document)`、`create_transfer(tenant,actor,document,expected,versions bigint[],recipient,reason)`、`list_transfers(tenant,actor,after)`、`decide_transfer(tenant,actor,request,approve boolean)`，皆回 jsonb。
- service：`getOptions(connection,identity,documentId)`、`createTransfer(connection,identity,documentId,expectedVersionId,versionIds,recipientId,reason)`、`listTransfers(connection,identity,afterId=0)`、`decideTransfer(connection,identity,requestId,approve)`。
- options：`document_id,title,current_version_id,versions:[{version_id,version_number}],recipients:[{id,username,display_name}]`。
- create／decide：`request_id,status`。list 回陣列（最多 100），每列 `request_id,status,requester_id,requester_username,recipient_id,recipient_username,reason,created_at,expires_at,can_decide`；提出人另有 `document_id,version_ids`。主管不回範圍欄位。
- 新增 `app/transfer_routes.py`、`app/templates/transfers.html`、`app/templates/transfer_new.html`；修改 main 與 base 導覽。
- GET `/api/transfers`、GET `/api/documents/{id}/transfer-options`、POST `/api/transfers`、POST `/api/transfers/{id}/decision`；HTML `/transfers`、`/documents/{id}/transfer`、`/transfers/{id}/decision`。
- API 建立 payload：document_id, expected_version_id, version_ids（1～100、嚴格正整數）, recipient_id, reason（去空白後 1～500 字）, csrf_token。decision：approve（嚴格 bool）, csrf_token。禁止額外欄位；Session／CSRF／Origin；所有交接回應 no-store，變更 commit 後才回應成功。

## 驗證與限制

先以 HTTP 測試證明缺少路由失敗，再新增 SQL 能力／service／路由。真實 PostgreSQL 測試覆蓋角色／tenant／版本邊界、封存、停用與恢復、主管異動、重複／並行決策、過期、部分更新回滾、ACL 不變、舊擁有者管理權失效及 worker 競態。HTML 驗證有限主管資訊與 escaping。完整既有測試與工具測試通過後獨立複核、提交／推送、建立 PR；不合併、不更新日常資料庫。

RLS、帳號管理、例外交接、主管自收、跨檔案批次、通知及歷史內容下載不在本次範圍。DB 能力使用可信 Session 的 tenant／actor，既有 runtime 信任邊界仍待 #25／#26。
