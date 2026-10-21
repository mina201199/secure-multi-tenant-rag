# M2-11 / M2-12：RLS 設計與未來驗收契約

日期：2026-10-20。**這是設計交付，沒有啟用 RLS、建立 DB 角色或執行資料庫驗收。** 對應 README 的 M2-11 / M2-12，並以 M2-02 分享功能的契約為銜接點。本文件中的 SQL 是待實作草圖，不是可直接套用的完整 migration；後面的 Python 是未來 migration 完成後可存成測試執行的驗收起點。

## 1. 現況與範圍

來源為 `README.md`、`docs/secure-multi-tenant-rag-decision-draft.md`、`docs/m2-versions.md`、`app/migrations/001_m1_core.sql`、`002_m2_versions.sql`、`app/db.py`、`security.py`、`repository.py`、`bootstrap.py` 與現有 `tests/`。M2-02 正在同一工作階段實作，以下明確區分其契約與既有 M2-01。

| 項目 | 既有 M2-01 | 本次 M2-02 契約 / 後續 RLS |
| --- | --- | --- |
| 資料表 | tenants、users、sessions、login_limits、documents、document_versions、version_readers、schema_migrations | RLS 不另建第二套正文或 ACL；003 分享 migration 增加最小寫入 GRANT |
| 文件閱讀 | repository 手動 tenant 條件、有效指標、雙 ready 與版本 ACL | 保留 API 條件，增加 DB 同等政策 |
| 資料庫角色 | bootstrap 建立 rag_app：LOGIN、NOSUPERUSER、NOBYPASSRLS、NOCREATEROLE、NOCREATEDB | 新增非 owner 的政策輔助、認證與未來 worker 角色；不要把這些名稱當作現有角色 |
| 分享 | 002 後 versions / readers 僅 SELECT | 同階段新增 `003_m2_sharing.sql`：UPDATE(visibility) versions、INSERT / DELETE readers；此文件未套用 DB，沒有 RLS 時仍依賴 API 授權 |
| 歷史 / 交接 / jobs / chunks / embeddings | 無對應授權、工作或向量資料表 | 未來另行 migration；不能假裝目前已可實作這些政策 |

002 已刪除 `document_readers` 和 documents 的 content / visibility。一般閱讀依 `document_versions.owner_id`、visibility 與 `version_readers`，不是 documents.owner_id。documents.owner_id 是目前文件管理責任，不能代替版本責任；交接不得默認移轉未列入的歷史版本。schema_migrations 僅 bootstrap 可存取。

## 2. 授權規則與威脅邊界

身分須為有效帳號，且帳號的 tenant 與交易 tenant 相同。一般內容讀取同時要求：文件 tenant 相符、documents.status = ready、有效指標非空且指向該文件的版本、版本 status = ready，最後符合版本擁有者、company 或該版本的個別 ACL 之一。沒有任何一條規則依 admin 或 manager 自動開放內容、標題、分類或文件存在性。

private 沒有 ACL 時僅版本擁有者可讀；private 有 ACL 是指定員工閱讀；company 是同公司所有有效帳號可讀。三者都受有效版本與 ready 狀態限制。撤掉個別 ACL **不會**撤掉 company 帶來的權限；要限制 company 文件須由擁有者改為 private 並明確設定 ACL。帳號停用不等於刪掉既有授權；重新啟用時依當下 ACL / 狀態判定。擁有者停用也不自動撤掉其他有效讀者的權限，更不讓管理員自動接管。

舊版即使 company、仍有舊 ACL、或呼叫者是其擁有者，也不得經一般查詢讀出。新版未發布 / 失敗時，舊有效版本仍可讀；指標切換後沒有新版權限就拒絕，不能退回舊版。歷史調閱必須等獨立歷史 ACL、端點與稽核能力建立後再開放；不能用 `app.history=true` 這種可自行設定的旗標開啟。

RLS 防禦應用查詢漏 tenant / ACL 條件及不合法資料寫入；tenant 複合外鍵另外防止跨公司與錯文件關聯。**自訂 GUC（app.user_id 等）不是驗證憑證。** 持有 rag_app SQL 執行能力的程式能自行 set_config，故此方案信任經驗證身分的後端與其參數化 SQL；不承諾抵抗已控制 web 程式、取得 runtime 憑證或任意 SQL 注入的攻擊者。不能把「偽造 GUC 可切身分」測成不可能。若未來要求抵抗此威脅，需另設 DB 驗證 Session / 簽章斷言或每個身分受約束的 DB 能力，並重設政策與認證邊界。平台最高權限維運者也不在租戶隔離承諾內。

## 3. 角色與 GRANT

角色名稱是建議契約；實作時須更新 bootstrap、Compose secret 與測試，一次部署。

| 角色 | 登入與用途 | 權限邊界 |
| --- | --- | --- |
| migration / setup owner | 僅 setup 持有管理 DSN；未來可拆 NOLOGIN schema owner 與部署 login | 建表、政策、受審函式、seed。web / worker 不持有憑證或 owner membership |
| rag_app | web 文件交易 LOGIN；保留既有 runtime 名稱 | 非 owner、NOINHERIT、NOSUPERUSER、NOBYPASSRLS、NOCREATEROLE、NOCREATEDB、NOREPLICATION；最小欄位 SELECT / UPDATE 與 ACL INSERT / DELETE；不得 TRUNCATE、DDL、SET ROLE 至輔助角色 |
| rag_auth | 未來獨立認證 pool 的 LOGIN | 僅 users 認證必要欄位、tenants 名稱、sessions / login_limits 必要 DML；無 documents / versions / ACL 權限。其 tenant 跨域能力只用於未登入認證，是受信任認證程式，不是一般業務連線 |
| rag_policy_reader | NOLOGIN、非 owner、無 BYPASSRLS | 僅 SELECT 身分 / 文件 / 版本 / ACL，供受審布林 helper 使用；無寫入、無 membership 授予 rag_app。透過明確 TO 此角色的內部 SELECT 政策閱讀，不依 owner bypass |
| rag_share_writer | 未來若分享改走限定函式才建立的 NOLOGIN 角色 | 僅 UPDATE(visibility) 與 ACL INSERT / DELETE，受獨立政策限制；不授 rag_app membership |
| rag_worker | M3A 才建立的 LOGIN | 無一般使用者任意閱讀權；依持久化工作 lease / 版本限定處理，見第 9 節 |

users 不得繼續向業務角色授整表 SELECT，否則同公司政策仍會洩漏 password_hash。改成必要欄位清單，前端讀者選單只回 id / display_name 等已決定欄位。users.role = admin 只影響未來帳號啟停能力；沒有直接 UPDATE(users) 的一般 grant。帳號啟停需另行受限函式或欄位 grant + admin 政策 + 身分欄位不可修改約束；不能趁 RLS 順便開放 role、tenant_id、manager_id、password_hash 寫入。

REVOKE PUBLIC 的 schema CREATE / 函式 EXECUTE，對自有函式明確 GRANT EXECUTE。禁止 runtime 建立 schema、function、view 或接管表；新表預設不授 runtime。檢查直接 / 間接 membership，不只 pg_roles 的 rolsuper / rolbypassrls。identity sequence 僅在未來確實開放建立文件 / 版本時給必要 USAGE。

## 4. 可信交易情境與連線池

一般受保護交易用同一條業務連線完成設定與查詢，設定值一律來自經 Session 驗證得到的 DB user / tenant，不能取自 query string、表單、header、路徑或任務 payload。

```python
# 未來流程草圖；auth_resolve 使用隔離的 rag_auth pool，且重查有效期 / idle / is_active。
identity = auth_resolve(raw_session)
if identity is None:
    raise Unauthorized()
with business_pool.connection() as conn:
    with conn.transaction():
        conn.execute("SELECT set_config('app.user_id', %s, true)", (str(identity['id']),))
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(identity['tenant_id']),))
        # true = SET LOCAL，只對目前 transaction 有效；SQL 值用 bind parameters。
        result = repository_call(conn, identity)
```

auth pool 不提供給業務 repository；業務 pool 不查 password_hash / 任意 Session。auth 的 Session touch 可獨立提交，其失敗不能建立業務情境。現有 `get_db` 借用 connection context 而非明確 `transaction()`，psycopg 預設會開始交易；重構時要處理 dependency 共用同一 connection 與 auth 分池順序，並防止已開 implicit transaction 後錯把 nested transaction 當成頂層。登入限流已有刻意的 commit，登入與登出也有 commit：它們會結束 LOCAL 情境，所以不得原封不動套「request 起始設定一次」後再做業務 SQL。應將登入 / 限流與受保護業務交易分離；任何中途 commit / rollback 後的業務操作都要重新驗證並設定完整情境。

只允許 LOCAL；禁止 session SET、autocommit 中單獨 set_config(..., true)、把 conn 傳出交易，或在交易內切換身分。缺一個 context、空字串、非正整數、溢位、user 不存在 / 停用 / tenant 不符皆 deny。解析 helper 可將無效格式轉 NULL，或拋受控錯誤並 rollback；不能 fallback 預設 tenant / 前一位身分。不能只依賴 AND 短路避免 bigint cast 失敗。

連線歸還須已 commit / rollback 且 idle；異常、取消、逾時與串接重試都走 finally，無法 reset 的連線直接丟棄。若偵測 session 層級 app.* 汙染，清除並記錄不含身分內容的安全事件，不能只依賴 SET LOCAL 遮住它：LOCAL 結束後 session 舊值會恢復。新增 pool reset / checkout 檢查，確保無業務情境；不要用 health SELECT 1 當隔離驗證。未來若用 PgBouncer transaction pooling，同樣在同一 transaction 內設定 / 使用，禁止依賴 connection 常駐狀態。

用 READ COMMITTED 短交易重查狀態。停用 / 撤權在其他交易 commit 後，下一個 statement 的 snapshot 才可見；已開始的 statement 不會被 RLS 倒追回收。長時間 REPEATABLE READ 交易不適用「下一次查詢看見撤權」。模型前與交付前重新開短交易驗權，不能將一次 HTTP request 的舊 snapshot 一直沿用。已在合法授權時交付或送出的資料不能收回。

## 5. 避免政策循環的 helper 契約

documents、versions、readers 的政策不能互相無限制子查詢：例如 documents 查 versions，versions 查 documents，readers 再查 versions，會形成 recursive RLS。建議用第 3 節專用 rag_policy_reader 及明確角色政策，將內部查詢收斂成少數 SECURITY DEFINER 布林函式。該角色在 FORCE RLS 下仍靠其 **明確 SELECT policy**，不是隱藏的 BYPASSRLS。

```sql
-- 草圖：對 users / documents / document_versions / version_readers 各建立一次。
CREATE POLICY internal_policy_read ON app_core.document_versions
  FOR SELECT TO rag_policy_reader USING (true);
-- rag_app 不得繼承 rag_policy_reader，因此這條 true 不適用一般業務查詢。

-- 每個 helper 都由 rag_policy_reader 擁有；其 schema 與物件名全限定。
-- SECURITY DEFINER SET search_path = pg_catalog, pg_temp
-- REVOKE ALL ON FUNCTION ... FROM PUBLIC; GRANT EXECUTE ... TO rag_app;
```

helper 不回傳資料列、內容、名稱、ACL 名單或任意 SQL，不接受 caller-supplied user_id / tenant_id 作授權身分，必須從有效 context 取得。必要的傳入 row tenant / version ID 只是待判定資源，不能直接信任。以 STABLE（同一 statement 的授權 snapshot）而非 IMMUTABLE 定義；不宣告 LEAKPROOF。固定 search_path，所有 app_core 物件全限定，pg_temp 放最後，禁止動態 SQL、呼叫者可替換的函式與可寫入 search_path schema。函式 owner 不可與表 owner 相同，不可擁有 BYPASSRLS；web 不得 ALTER / REPLACE 函式。建立、REVOKE PUBLIC、GRANT、變更 owner 在同一 migration 交易完成。未來有任何一般 view，採 PG16 security_invoker 並做越權測試，避免 view owner 偷帶權限。

| 建議函式 | 必須實現的布林條件 |
| --- | --- |
| `actor_ok()` | context IDs 合法，users.id / tenant_id 相符且 is_active；缺 context = false |
| `actor_id()` / `tenant_id()` | 安全解析的 context ID；無效 = NULL，不查資料、不提供 fallback |
| `can_read_document(t,d)` | actor_ok + 同 tenant，documents d 與其 current version v 正確關聯，d / v 都 ready，v owner / company / 自己 ACL 之一 |
| `can_read_version(t,v)` | 與上一條完全相同，額外指定 v 就是 current version；直接 SELECT 舊版不例外 |
| `can_manage_sharing(t,v)` | actor_ok + 同 tenant + v 是文件 current version + d / v ready + v.owner_id = actor_id；不使用 d.owner_id 代替 |
| `can_see_reader(t,v,u)` | can_manage_sharing，或 u = actor_id 且 can_read_version；reader 不得列出其他讀者 |
| `can_grant_reader(t,v,u)` | can_manage_sharing + 目標 user 同 tenant、is_active 且 u <> v.owner_id |
| `can_list_user(t,u)` | actor_ok + 同 tenant；限制 SELECT 欄位，不把管理員或主管變成文件特權 |

所有 helper 內部先驗證 actor；即使直接呼叫 can_read_version(其他公司,猜測 ID) 也只回 false。內部 SELECT true 政策僅套專用角色，且這些 helper 執行期間的查詢不再套 rag_app 的互查政策。不得授予 PUBLIC / rag_auth 任意執行 document helper；worker 需其限定工作 helper。

## 6. 每個現有資料表的政策草圖

以下 `app_guard` 是**未建立**的 helper schema。實作時必須先完成上節函式、GRANT、recursion 測試，再啟用政策；不能只貼上這幾段 SQL 就宣稱安全。

```sql
-- 對本節每張受保護表都 ENABLE + FORCE；schema_migrations 不授 runtime。
ALTER TABLE app_core.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_core.documents FORCE ROW LEVEL SECURITY;

CREATE POLICY documents_read ON app_core.documents FOR SELECT TO rag_app
  USING (app_guard.can_read_document(tenant_id, id));

CREATE POLICY versions_read ON app_core.document_versions FOR SELECT TO rag_app
  USING (app_guard.can_read_version(tenant_id, id));

CREATE POLICY versions_share_update ON app_core.document_versions FOR UPDATE TO rag_app
  USING (app_guard.can_manage_sharing(tenant_id, id))
  WITH CHECK (app_guard.can_manage_sharing(tenant_id, id));
-- 只有 UPDATE(visibility) GRANT；上述 WITH CHECK 本身不能比較 OLD / NEW 來保護 content。

CREATE POLICY readers_read ON app_core.version_readers FOR SELECT TO rag_app
  USING (app_guard.can_see_reader(tenant_id, version_id, user_id));
CREATE POLICY readers_add ON app_core.version_readers FOR INSERT TO rag_app
  WITH CHECK (app_guard.can_grant_reader(tenant_id, version_id, user_id));
CREATE POLICY readers_remove ON app_core.version_readers FOR DELETE TO rag_app
  USING (app_guard.can_manage_sharing(tenant_id, version_id));
-- DELETE 不要求目標仍 active，否則停用帳號的舊 ACL 將無法清掉。

CREATE POLICY users_directory ON app_core.users FOR SELECT TO rag_app
  USING (app_guard.can_list_user(tenant_id, id));
CREATE POLICY tenants_self ON app_core.tenants FOR SELECT TO rag_app
  USING (app_guard.actor_ok() AND id = app_guard.tenant_id());
```

| 表 | web SELECT / 寫入 | 其餘角色 / 保護方式 |
| --- | --- | --- |
| tenants | 只自己的公司；不 INSERT / UPDATE / DELETE | rag_auth 僅 SELECT，供驗證身分的 tenant_name；setup 管理 |
| users | 同公司最小欄位讀者目錄；現階段不寫 | rag_policy_reader SELECT true（供 actor_ok）；rag_auth 只認證欄位 SELECT；後續帳號啟停獨立授權，不直接照抄整表 UPDATE |
| documents | 上述有效且有權文件；目前不寫 | rag_policy_reader SELECT true；未來建立 / 封存 / 指標切換透過獨立受限能力 |
| document_versions | 僅目前有權 ready 版本；M2-02 只 UPDATE visibility | rag_policy_reader SELECT true；不開 INSERT / DELETE、content / tenant / document / owner / status 更新 |
| version_readers | 擁有者管理目前版 ACL，讀者最多自己的一列；INSERT / DELETE 如上；不 UPDATE | rag_policy_reader SELECT true；複合 FK 保留；ACL 元組改指向另一版本須刪增，不能 UPDATE |
| sessions | rag_app 無直接 SELECT / DML | rag_auth 有 SELECT / INSERT / UPDATE / DELETE 的明確 TO rag_auth true 政策及 grant；token hash 不離開認證邊界。這是全平台認證能力，須記錄而非稱其 tenant 隔離 |
| login_limits | rag_app 無直接讀寫，無租戶概念 | rag_auth 有 rate-limit 所需 DML 的明確政策；只存雜湊鍵，限流提交獨立於業務交易 |
| schema_migrations | 無 grant、無政策；不需以 tenant RLS 假裝保護 | 僅 setup，runtime SELECT / ALTER 拒絕；未來新增表不能預設開權 |

對 tenants / users / sessions / login_limits / document_versions / version_readers 也逐表 ENABLE / FORCE。rag_auth 的政策只對認證所需 users / tenants / sessions / login_limits，且透過 SQL GRANT 限欄位、限操作；不得加入 documents 的 true 政策。rag_policy_reader 對 tenants 若不需要就不 grant。rag_app 沒有政策的 INSERT / DELETE / UPDATE 一律不開 grant，雙重拒絕。RLS 不保護 TRUNCATE / DDL；這些由 GRANT / owner 邊界禁止。外鍵 / 唯一約束可能造成存在性旁路，API 須先授權並映射統一錯誤，不能直接將 DB constraint 名稱、detail 或 row value 回給使用者。

若為將來新增較寬 policy，建議另設 `AS RESTRICTIVE` tenant / actor guard 供所有業務命令共用，再保留命令專屬 permissive policy；須逐表驗證與 helper 相容，不讓 helper role 套到遞迴的業務 guard。多個 permissive policy 是 OR，不能誤以為加一條 tenant policy 就會 AND 限制其他 policy。

## 7. M2-02 分享銜接與寫入限制

M2-02 模式為 private / company / specified；specified 寫入 visibility = private 加明確 version_readers，無第三個 visibility enum。請求帶 `version_id`，其語意是 expected version；查詢確認文件 / 版本兩者 ready、版本擁有者是 actor，並以 `FOR UPDATE OF v` 只鎖目前版本列，驗證同 tenant 有效接收者。自我 owner ACL 拒絕；撤權清掉既有 ACL 不要求目標仍啟用。RLS helper 應鏡像這些條件，不能改成 documents.owner_id、admin 或 manager 可分享。此契約已與 M2-02 實作者對齊。

003 的欄位 grant 能限制可改的欄位，**沒有 RLS 時無法限制更新哪一版本或替誰加 ACL**。RLS migration 必須是 003 之後的新檔，不覆寫已套用 migration。未來 tests/test_database.py 原本所有業務資料完全唯讀的案例須改為欄位不可寫、跨 tenant / 非擁有者不可寫、擁有者合法分享可寫；不能為過舊測試把整表 UPDATE 放寬。

本節直接 DML + RLS 能保護每列授權，但不能單靠 policy 保證「模式切換 + 整組 ACL 替換」是同一原子操作，也不能強制每個直接 SQL 呼叫者都取得正確的資源鎖。API 保留目前版本鎖、expected version 檢查、一次交易替換 ACL；批次任何目標無效都 rollback，不能部分成功。若要對 runtime 任意 DML 也強制相同業務原子性，後續將寫入改為單一受限分享函式，撤回 rag_app 的直接寫 grant，由 rag_share_writer 執行，函式內從 context 取 actor、依統一協定鎖定目前版本 / 文件、重查 expected_version / owner / ready、驗證全部接收者後寫入。其 helper 和角色政策要單獨設計，不能僅把 current_user 改成 writer 就失去 actor 檢查。這是比 003 更強的後續選項，不是本次已完成機制。

目前沒有 pointer / status / 帳號狀態的 runtime 寫入能力，003 不為 row lock 額外開放 UPDATE users.is_active 或 documents.current_version_id；接收者狀態驗證是 statement snapshot，沒有 FOR SHARE 帳號鎖。若維運者同時停用接收者，ACL 可能仍被加入，但停用帳號無法登入 / 閱讀；不能宣稱「提交瞬間保證接收者仍 active」。未來帳號管理 API 若要求這項保證，採受限函式與明確鎖協定，不能放寬業務角色欄位更新權限。

並行測試須覆蓋分享 vs 新版切換 / 封存 / 交接 / 接收人停用。未來生命周期寫入者須先鎖定 outgoing current version，再依共同協定鎖文件 / 其他版本；多列按固定 id 順序。**同階段 M2-02 已加入取得版本鎖後，另發新的 READ COMMITTED 查詢重讀文件 current pointer / status 與版本 owner / status**，避免僅依第一個 JOIN 的舊 snapshot：若發布者先取得版本鎖、改了 documents 指標但沒有更新舊版本列，等待中的分享 JOIN 仍可能使用舊 documents snapshot。失效 expected version 拒絕。`tests/test_sharing.py` 已新增兩條 PostgreSQL 連線的等鎖切換回歸案例；實際執行紀錄由 M2-02 文件提供，本設計工作未執行 DB 測試。RLS subquery snapshot 不能取代此鎖契約。未來新版本 / 封存 / 交接還未有 API，實作時必須採相同鎖協定並增加各生命周期的並行測試；不能聲稱目前已解決所有生命周期競態。

## 8. 未來文件寫入、歷史與交接

M2-03 起建立文件 / 版本需明確新 policy 或受限函式：actor 必須 active / 同 tenant，文件管理者可建立下一版；新版本 owner 來源可信，visibility 固定 private、status 固定 queued、無 ACL，不接受任意 content 覆寫既有版本。建立文件與第一版的循環關聯以 current_version_id 初始 NULL、插入版本、完整性確認後設定處理；NULL 期間一般 SELECT 不可見。

版本發布只能讓持久化工作完成且完整的 ready 版本原子切換；WITH CHECK 限 tenant / document 關聯，三欄 FK 防錯文件，但兩者都不證明工作完整。封存不是 DELETE，必須同時阻止一般閱讀、下載、檢索、發布和重試復活。內容不可變性、不可自行改 owner / tenant / version_number、合法狀態轉移需欄位 grant、觸發器或受限函式，不能只寫 USING tenant_id = context。分享能力絕不能更新這些欄位。

歷史讀取不擴張目前 versions_read；新建歷史 ACL 與稽核資料後，透過獨立後台能力驗證該版本 owner 授予的歷史權限，同公司 active、每次調閱留紀錄。當前閱讀 ACL / company / 新 owner / admin / manager 都不直接等於歷史權限。交接審核端只可讀有限申請欄位，不能讓主管藉 JOIN 取得文件標題 / 正文；核准函式重查所有關係，鎖定並原子更新明列範圍的 owner，保留閱讀 ACL，拒絕主管自收、跨公司、停用接收人與重複核准。這些表、grant、policy、測試在現有 schema 尚不存在，預設 deny。

## 9. M3A workers / jobs / chunks / embeddings 的依賴

先建持久化 jobs：至少 tenant_id、document_id、version_id、狀態、不可猜測 claim / lease token、有效期限、嘗試次數、去重鍵與取消 / generation 資訊。具體表名與欄位留在 M3A 設計；這裡不建立虛構現有表。jobs 以及未來 chunks / embeddings 必須帶 tenant 複合 FK 指到正確 version；若帶 document_id，使用三欄 FK 防同 tenant 錯文件。chunks 去重綁 version + 位置；embedding 綁 chunk + 模型規格，不能跨 tenant / version 混接。

worker 不能把外部 payload 的 tenant / owner 直接 set_config 後讀全公司。用專用 DB claim 能力從可信 job 記錄取得租戶 / 版本 / lease，worker 每個短交易 SET LOCAL job_id、lease、tenant，policy helper 驗證 DB lease 未過期且匹配當前工作。全域派工需要跨 tenant 的 claim 函式，只回必要 job 識別 / lease，不能授 worker 全表 SELECT。此能力由專用非 owner definer / 明確派工政策提供，需獨立威脅審查。

worker 讀取範圍是被 claim 的待處理版本，不能套一般 current-ready-user policy，否則 queued 新版永遠不能處理；也不能因此給所有版本 true 政策。worker 寫入只允許該 lease 的 chunks / embeddings / 工作狀態；禁止改 ACL、owner、tenant、任意文件 current pointer。發布走獨立限定函式，鎖定文件 / 版本 / job，檢查未封存、lease / generation、授權或管理責任有效、完整性與狀態，原子發布。外部付費呼叫不持有長 DB 交易，呼叫前及結果寫回 / 發布前重驗 lease / 狀態；過期結果丟棄，不能復活封存文件。

job 建立時須驗證當前擁有者權限，執行時重查可信授權依據；停用發起人或交接時，需明確取消或由受授權的新責任者重新核准，不自行提升為系統權限。讀者 ACL 撤銷至少立即阻止該讀者查詢 / 模型交付；不必因其他讀者撤權就刪除整個公司版本，但若某工作是為該使用者發起的外部資料交付，則依其身分重新驗權。worker 取消條件與公司文件既有讀者權限要分開測。

一般 chunks / embeddings SELECT 由 actor_ok + parent version 的 can_read_version 決定，再要求處理完整 / 可檢索；向量查詢必須在授權範圍內取 Top-K，不能先全平台查候選再過濾，或把未授權候選送 reranker / 模型。模型前與回覆前用新短交易重驗取得的 chunk / version IDs；發現撤權、停用、封存、切換版本則拒絕交付。

## 10. 未來可執行的驗收起點

本次只做文件靜態檢查，**未執行以下 DB 程式，也沒有 RLS 通過數字**。未來先在隔離測試庫套用 001→002→003→RLS migration，沿用 tests/conftest.py 的真實 PostgreSQL fixtures；不得在正式資料庫測。以下可存為 `tests/test_rls_contract.py`，使用既有 seed，不依賴 helper schema 的實作細節。角色重整後維持 runtime_db 指向 rag_app；admin_db 僅準備 fixture / 查預期，不能拿它當被測連線。

```python
import pytest
import psycopg


def bind_actor(conn, user):
    conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user[0]),))
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(user[1]),))


def actor(admin_db, name):
    return admin_db.execute(
        "SELECT id, tenant_id FROM app_core.users WHERE username = %s", (name,)
    ).fetchone()


def test_no_context_denies_content(runtime_db):
    for table in ('documents', 'document_versions', 'version_readers'):
        assert runtime_db.execute(f'SELECT * FROM app_core.{table}').fetchall() == []


@pytest.mark.parametrize('suffix, expected', [
    ('owner', 3), ('reader', 2), ('staff', 1), ('admin', 1), ('manager', 1),
    ('inactive', 0),
])
def test_direct_sql_seed_matrix(runtime_db, admin_db, suffix, expected):
    who = actor(admin_db, 'a.' + suffix)
    bind_actor(runtime_db, who)
    rows = runtime_db.execute('SELECT id, tenant_id FROM app_core.documents').fetchall()
    assert len(rows) == expected
    assert all(row['tenant_id'] == who[1] for row in rows)
    versions = runtime_db.execute('SELECT id FROM app_core.document_versions').fetchall()
    assert len(versions) == expected


def test_mismatched_context_denies(runtime_db, admin_db):
    alice = actor(admin_db, 'a.reader')
    bob = actor(admin_db, 'b.reader')
    bind_actor(runtime_db, (alice[0], bob[1]))
    assert runtime_db.execute('SELECT * FROM app_core.documents').fetchall() == []


@pytest.mark.parametrize('finish', ['commit', 'rollback'])
def test_same_connection_drops_local_context(runtime_db, admin_db, finish):
    bind_actor(runtime_db, actor(admin_db, 'a.reader'))
    assert len(runtime_db.execute('SELECT id FROM app_core.documents').fetchall()) == 2
    getattr(runtime_db, finish)()
    assert runtime_db.execute('SELECT id FROM app_core.documents').fetchall() == []
    bind_actor(runtime_db, actor(admin_db, 'b.staff'))
    assert len(runtime_db.execute('SELECT id FROM app_core.documents').fetchall()) == 1


def test_nonowner_share_write_denied(runtime_db, admin_db):
    owner = actor(admin_db, 'a.owner')
    staff = actor(admin_db, 'a.staff')
    version = admin_db.execute(
        "SELECT id FROM app_core.document_versions WHERE tenant_id = %s "
        "AND visibility = 'private' AND status = 'ready' ORDER BY id LIMIT 1",
        (owner[1],),
    ).fetchone()[0]
    bind_actor(runtime_db, staff)
    assert runtime_db.execute(
        "UPDATE app_core.document_versions SET visibility = 'company' WHERE id = %s",
        (version,),
    ).rowcount == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        runtime_db.execute(
            'INSERT INTO app_core.version_readers (tenant_id, version_id, user_id) '
            'VALUES (%s, %s, %s)', (owner[1], version, staff[0]),
        )
    runtime_db.rollback()
```

執行入口（未來，並非本次執行紀錄）：

```powershell
docker compose --profile test run --build --rm tests pytest -q tests/test_rls_contract.py
docker compose --profile test run --build --rm tests
```

上述 smoke cases 不取代下列完整矩陣。測試須使用獨立 fixture，所有變更 finally 還原 / rollback，避免修改共用 seed 造成測試依賴；並行測試使用兩條真實連線與明確同步事件，不用 sleep 猜時序。

| 編號 | 必須落地的案例 | 驗收結果 |
| --- | --- | --- |
| R01 | 真正 rag_app LOGIN、membership、owner、schema ACL、pg_class relrowsecurity / relforcerowsecurity、pg_policies 角色 | 不是 owner / superuser / BYPASSRLS，無角色提升；全部目標表 ENABLE + FORCE；無 PUBLIC 寬政策 |
| R02 | 缺失 / 只有 user / 只有 tenant / 空白 / 非數字 / 負數 / bigint overflow / 不存在 / tenant-user 不符 | 0 內容、寫入拒絕或受控錯誤回滾；無預設身分 |
| R03 | 無 API tenant 條件直接掃三張內容 / ACL 表，猜另一公司的 document / version ID、同公司未授權 | 不洩漏列 / 標題 / ACL；admin / manager 無額外內容權 |
| R04 | owner、private reader、未授權 staff、company reader、inactive | 精確符合授權矩陣；不能列出其他 private ACL；owner 可管理自己版本 ACL |
| R05 | 舊版 company / ACL / owner、新版 queued / failed、current NULL / 指向非 ready、document archived | 只可讀 ready current；未切換舊有效版持續可讀，切換後無舊版回退 |
| R06 | owner 合法 visibility 切換、同公司 active 非 owner grant、重複 grant / revoke、specified 替換 | 合法操作成功且原子；重複行為符合 M2-02 API 契約 |
| R07 | 非 owner、admin、manager、跨 tenant INSERT ACL，UPDATE ACL 指標，寫 content / owner / status / tenant / current pointer | rowcount 0 或受控 InsufficientPrivilege；資料未改，grant 與 policy 都受測 |
| R08 | owner grant 跨公司 / inactive / owner-self；DELETE 已停用讀者 ACL；FK 由 setup 直接造錯關聯 | grant 拒絕，revocation 成功；FK 仍拒跨 tenant，三欄 FK 拒同公司錯文件 |
| R09 | A 身分 commit / rollback / exception / timeout 後同一 pool 連線給 B；pool max_size=1；中途 commit；session SET 汙染 | 無情境時 deny，B 只看 B；偵測 / 清除 session 汙染；未重新 bind 的業務查詢不得成功 |
| R10 | 另一連線 commit 撤權 / 停用，原連線新 READ COMMITTED statement；登出 / Session 到期 | 下一請求驗證拒絕，下一 snapshot 符合新權限；舊 snapshot 侷限如第 4 節揭露 |
| R11 | sharing 與 version switch / archive / transfer / reader disable 並行，失效 expected_version，混合合法 / 不合法批次 | 鎖後再驗、失敗全 rollback，沒有舊版本改寫或部分 ACL；未實作生命周期先標依賴，不標通過 |
| R12 | definer 直接呼叫跨公司參數、temp object / search_path 注入、遞迴政策、普通 view、row_security=off | 無內容 / 權限提升；無 recursion；off 不可繞過；新 view 仍限 actor |
| R13 | auth 未登入 / 限流持久化 / Session fixation / touch / logout / expiry；rag_app 讀 hash / sessions，rag_auth 讀文件 | 登入回歸正常；認證與業務能力分開，越界 SQL 拒絕；不印 token / hash |
| R14 | 未來 job 偽 payload / expired lease / claim 重啟 / archive / actor inactive / transfer / stale generation | worker 只能處理可信 claim；不能發佈失效工作，無資料復活 |
| R15 | 未來 chunks / embeddings 跨公司關聯、未完成索引、舊引用、模型前 / 交付前撤權 | 不進候選、不送外部服務、不交付；不以其他未授權段落補足 Top-K |

## 11. 實作順序與交付判準

1. 先完成並驗收 M2-02（003、分享 API / 頁面、版本 owner / active target / expected version / 原子 ACL 及錯誤契約），保存其現有測試基準。這一步不標 RLS 完成。
2. 建立真實 PostgreSQL 受限角色驗收的失敗案例，先觀察目前漏 tenant 直接 SQL 會讀出資料，記錄失敗；不要以既有測試通過推論 RLS 存在。
3. 拆認證與業務能力，處理現有顯式 commit 與 pool context；更新 bootstrap 角色 / secrets、最小欄位 grant，保留 M1 Session / 限流回歸。受控切換不得部署「舊登入碼 + 已撤 auth grant」的中間版本。
4. 新 migration 建 helper schema / 受限角色 / 固定 search_path 函式 / 明確 ACL，建立 recursion-free policies，對目標表 ENABLE + FORCE，維持 FK 與 visibility 欄位限制。migration 不修改 001 / 002 / 003；新裝與升級皆測，失敗整交易 rollback。
5. 整合交易 SET LOCAL 與 API 既有授權，執行 R01–R13、M1 / M2 回歸、升級與 pool max_size=1；保留首次失敗與修正紀錄。版本 / 封存 / 交接尚未落地的 R11 子案例明列未驗收。
6. M2 後續生命週期按第 8 節逐能力加政策 / 欄位約束 / 鎖協定；歷史與交接不得為過測試直接放寬 versions_read。M3A jobs / chunks / worker 完成後執行 R14–R15；預留 worker 角色名稱不等於完成 worker RLS。
7. PR 交付新 migration、角色權限清單、policy / helper 清單、真實執行指令 / 環境 / 正反結果、並行測試與已知限制後，才能按已驗收範圍更新 README M2-11 / M2-12。本文件只完成設計，這兩項仍未完成。

## 12. 依據與設計限制

PostgreSQL 16 的 [Row Security Policies](https://www.postgresql.org/docs/16/ddl-rowsecurity.html) 說明 owner / BYPASSRLS 邊界、FORCE、預設拒絕、政策組合與跨表授權競態；[CREATE FUNCTION](https://www.postgresql.org/docs/16/sql-createfunction.html) 說明 SECURITY DEFINER 的安全 search_path 與預設 PUBLIC EXECUTE；[設定函式](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADMIN-SET) 說明 set_config 的 transaction-local 行為。草圖的政策、角色與函式仍須在專案實際 PostgreSQL 16 環境驗證。

仍有明確限制：此設計信任 web 設定身分，auth 角色持有跨平台認證資料能力，policy reader 是受審的跨 tenant 內部讀能力，RLS 無法收回已讀資料或取消既有 statement snapshot，直接 DML policy 無法獨自證明整體生命周期原子性。現有 schema 不能表達歷史 ACL / lease / chunk 完整性，因此本文件沒有宣稱這些未來能力已受保護。本次不修改應用程式、migration、測試或 DB；沒有執行 Docker / DB 寫入，也沒有提交 commit。
