# 文件交接實作計畫

**Goal:** 完成 #21～#23 的提出、有限主管審核與原子移轉，保留 ACL。
**Architecture:** migration 009 封裝交易能力，service 固定 SQL 呼叫，API 與 HTML 共用權限判斷。
**Tech Stack:** PostgreSQL 16、psycopg、FastAPI、Jinja2、pytest、Docker Compose。
**Spec:** `docs/superpowers/specs/2026-10-21-document-transfers-design.md`

## 約束

僅修改交接相關檔案；2 空白縮排、新註解正體中文。保留 migrations 001～008。不改一般／歷史 ACL。測試使用隔離 test-db；不更新日常資料庫。已獲使用者授權實作、測試、推送與建立 PR。

## 任務

- [x] 1. 測試先行：新增 `tests/test_transfers.py`，POST 新申請應為 200，對缺少路由的現況先確認失敗。完整交接測試需涵蓋 spec 列出的拒絕、原子性與並行案例。
- [x] 2. DB／service：新增 migration 009 與 transfer_service，實作 spec 的四個公開能力與修訂 trigger，執行 DB 專項測試。新表只有 NOLOGIN 能力角色可操作，web 只可 EXECUTE，worker 無交接能力。
- [x] 3. API／HTML：新增 transfer_routes、兩份模板，接上 main 與 base 導覽。對建立、決策、列表、CSRF／Origin、escaping、有限資訊補 HTTP 測試，確認 commit 前不交付成功。
- [x] 4. 複核整合：完整 481 項與 120 子測試、工具 12 項通過；獨立安全審查與測試生命週期修正複核完成。失敗與重測紀錄見 `docs/document-transfers.md`。
- [x] 5. 文件與 PR：操作驗收、README 與決策稿已同步；`e5fbaa1` 已推送至 codex/document-transfers，建立並附加 [PR #79](https://github.com/mina201199/secure-multi-tenant-rag/pull/79)，Closes #21/#22/#23、Related #25/#26/#27。遠端 CI 結果見 PR checks。

## 決策紀錄

- 使用目前乾淨 checkout 的獨立功能分支；不另外建立需要外部路徑的 worktree。
- 以使用者已核准的 Issue／決策稿為設計依據；停用後必須重新提出的新增決策已獲明確確認。
- 過期採 7 天；單申請 1 檔案、最多 100 版本，限制交易與表單大小；日後可另行擴充跨檔案交接。
