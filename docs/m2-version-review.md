# M2 TXT 版本寫入獨立審查

日期：2026-10-20。審查範圍為 migration 004、版本服務、HTTP／HTML 路由、模板、驗收測試與相關說明。審查者只做離線檢查，未啟動 Docker 或資料庫；真實 PostgreSQL、HTTP、並行及升級的實際結果以協調者驗收紀錄為準。

## 發現與修正追蹤

1. **P2，已修正：非同步路由直接等待同步資料庫。** 建立與 HTML 提交路由原先在事件迴圈直接執行同步 psycopg 呼叫與 commit，建立流程等待 outgoing version 列鎖會阻塞同一 worker 的其他請求。現在解析上傳後，把建立及 commit 一起交給 run_in_threadpool。已覆核兩個寫入路由與共用 create_and_commit 實作；協調者已觀察修正前事件迴圈測試失敗，修正後執行結果以驗收紀錄為準。
2. **P2，已修正：新路由的文件識別未限制 bigint。** Python int 可超過 SQL 函式 bigint 範圍，原先可能在正常驗證前成為資料庫函式解析／範圍錯誤。三個新增文件路由現在共用限定正 bigint 的 DocumentId。已覆核實作；協調者已觀察修正前兩個超範圍案例失敗，修正後執行結果以驗收紀錄為準。

曾懷疑直接 multipart parser 的低層格式例外會成為 500，但鎖定相依版本的兩個壞 boundary／header 案例在修改前已回安全 422，因此撤回這項未重現疑慮，沒有把它列為缺陷，也不要求額外例外捕捉。

協調者另外發現並修正 UTF-8 逐字 substr 的平方時間驗證，API 實作者補上 multipart 實際接收總量限制；本審查已讀取相應實作。容量限制不依賴 Content-Length，並保留檔案與字元上限。

## 已檢查的邊界

- writer／processor 為 NOLOGIN、NOINHERIT、非 superuser、無 BYPASSRLS；writer 非資料表 owner。PUBLIC 函式執行權已撤回，search_path 固定為 pg_catalog、app_core、pg_temp；業務資料表均明確指定 schema。
- rag_app 只有 create／management／status 的函式執行權，沒有版本新增、正文／狀態／指標或來源表寫入權，也沒有 processor membership。processor 只有 claim／complete／fail 的執行能力。
- 建立權依目前 documents.owner_id，分享權依有效 document_versions.owner_id；兩者不混用。建立、重播與狀態查詢重新驗證當下文件管理權。
- 來源 bytes 與 generated SHA-256 同交易保存，來源 UPDATE 有不可變 trigger；發布從保存來源產生正文，不接受呼叫者提供正文。source／job 有 tenant、document、version 複合外鍵。
- create 與 finish 使用 outgoing → document 鎖序及鎖後新 statement 重查；finish 再鎖 target → job。claim 只鎖 job，文件要求先 commit claim，再開始完成交易。
- pending 唯一索引、鎖內版本號分配與冪等 fingerprint 一致；合法發布後重播先於新 base／pending 檢查。鎖等待期間 pointer 改變會回 409 並要求 rollback，符合已記錄鎖協議。
- 完成時檢查 lease、到期、actor active、文件與版本 owner、文件／outgoing ready、expected base 與來源完整性；失敗保留有效指標，成功同交易切換正文／ready／pointer／job。錯誤 token 不會使合法工作失敗。
- NULL current、非 ready 或 archived 文件無法建立新版。新版私人且空 ACL；讀取仍只走 current pointer，無舊版 fallback。
- 頁面與說明明確揭露目前只排隊，沒有執行中 worker、PDF、重試 API、lease 自動回收、RLS 或向量搜尋完成的宣稱。RLS readiness 工具維持 NOT_READY。

## 驗證限制

離線 AST 已成功解析 app/version_service.py、app/version_routes.py、app/main.py 與兩份版本測試。主機 Python 套件與鎖定容器相依版本不同，不能以主機匯入結果代替正式測試。帳號停用只承諾 READ COMMITTED 重驗邊界；可信後端傳入的 actor／tenant 並非已完成 RLS 或防遭入侵 rag_app 偽造身分的保證。
