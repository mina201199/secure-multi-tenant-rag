# 檔案交接：提出、主管審核與原子移轉

對應 #21（M2-07）、#22（M2-08）、#23（M2-09）。本功能沿用版本、封存與歷史後台的權限邊界；不提供主管自收、上層主管代審、跨公司交接或停用帳號例外交接。

## 操作

1. 目前擁有者登入，從檔案的「建立新版／版本管理」頁選「提出交接」。
2. 目前有效版本必須移轉；可另外勾選由自己擁有、已完成的歷史版本。每張申請只涵蓋一份檔案，最多 100 個版本。若仍有待處理／處理中版本，先等處理結束。
3. 選擇同公司有效接收人並填交接原因。接收人不能是自己或審核主管；原因不會自動填入檔名或正文，請勿自行貼入機密內容。
4. 直屬主管登入「檔案交接」查看自己目前負責的待審案件，只看得到申請編號、提出人、接收人、原因、期限及狀態；看不到檔案／版本 ID、檔名、摘要或正文。
5. 主管核准或拒絕。核准成功後，檔案及選定版本一起移轉；提出人可查自己的範圍與結果。主管清單不保留已結案案件，接收人不會只因被提名就取得申請存取權。

申請有效期為 7 天，提出後不能編輯範圍或接收人。404 表示沒有操作權或找不到資源；409 表示資料已變更、申請已結案／過期，或歷史列暫時忙碌，請重新載入確認；422 表示輸入或接收人／版本範圍無效。歷史鎖暫時忙碌可稍後重試，已失效的申請則須重新提出。

2026-10-21 使用者確認：**申請提出後，原擁有者若被停用，舊申請也不得核准；恢復帳號後必須重新提出。** DB 修訂計數會保留狀態變更事實，不會因恢復成原值就接受舊申請。主管／接收人狀態與主管關係變更、擁有權移轉往返也會讓既有申請失效。

## 移轉與閱讀權限

- 核准時重新驗證同公司、目前直屬主管、三位帳號有效性、檔案擁有者、有效版本、選定版本擁有者與狀態、封存限制、申請狀態、期限及修訂。
- 一般 `version_readers`、版本 visibility、獨立 `history_readers` 都不修改。原擁有者失去範圍內的管理權；若仍有指定閱讀 ACL 或 company 可見性，仍可依原規則閱讀。
- 未選歷史版本的擁有者不變；新擁有者不會自動取得歷史調閱權。歷史後台仍需要明確的獨立授權，封存後仍一律拒絕調閱。
- 不改版本內容、來源、有效版本指標或封存狀態；核准前若新增了 queued／processing 工作，交接拒絕執行。

## API 與交易

| 介面 | 用途 |
| --- | --- |
| `GET /api/documents/{id}/transfer-options` | 擁有者取得可選版本及有效接收人 |
| `POST /api/transfers` | 提出申請：document_id、expected_version_id、version_ids、recipient_id、reason |
| `GET /api/transfers?after_id=0` | 自己的申請與主管待審清單；最多 100 筆，依 request_id 接續 |
| `POST /api/transfers/{id}/decision` | `approve: true` 核准／`false` 拒絕 |

寫入需要可信 Session、CSRF token 與 Origin；不接受客戶端指定 tenant、actor 或 manager。ID／bool 型別嚴格驗證，原因 1～500 字。HTML 與 API 共用 service 與 DB 能力，變更成功先 commit 才送出回應，交接回應皆為 no-store。

migration 009 建立 `transfer_requests`、`transfer_request_versions`、`transfer_audit_events` 與受限 NOLOGIN `rag_transfer_access`。web 無交接表直接讀寫權，worker／processor 無交接能力。複合外鍵綁定公司、申請、檔案、版本與稽核操作者。成功提出／核准／拒絕的事件與狀態在同一交易提交，稽核不自動複製正文或原因。

核准先鎖申請，再依 outgoing → document → selected history → users 鎖定並重查。歷史列使用 NOWAIT，避免與把舊版當 outgoing 的舊交易反向等待；衝突回 409。所有擁有者更新、申請狀態及稽核全部成功或全部回滾；同一申請並行決策只能有一筆成功。

## 部署與驗收

既有 migrations 001～008 不變，setup 會安裝 009；部署時重新建立映像、執行 setup，再啟動 web。沒有新增套件，不替既有帳號自動移轉或授權。本次開發只更新隔離 test-db，日常資料庫尚未套用 migration。

```powershell
docker compose --profile test run --build --rm tests pytest -q -p no:cacheprovider --tb=short tests/test_transfer_db.py tests/test_transfers.py
docker compose --profile test run --rm tests pytest -q -p no:cacheprovider --tb=short
python -m unittest tools.test_check_rls_readiness tools.ci.test_run_tests -q
```

測試先行證據：實作前交接建立 HTTP 測試得到 404（預期 200），DB 能力檢查不存在；擴充 HTTP 測試共 19 項失敗，均由缺少交接路由引起。首輪整合 55 項通過，包含 API／HTML 的延後稽核 commit 失敗回滾。

完整回歸曾出現 52 項失敗、429 項通過：新增稽核測試重複進入共用 app 的 lifespan，結束時關閉全域連線池，導致後續測試 PoolClosed。以「一個稽核測試接一個既有版本路由測試」穩定重現 1 passed／1 failed；改用不重啟 lifespan 的 TestClient 並自行 close 後，同組 2 passed。失敗源於測試生命週期，不修改正式 app 來規避。

最終完整回歸：**481 passed、120 subtests passed（50.56 秒）**；交接新增 41 項 DB 與 25 項 HTTP 案例，共 66 項。工具 **12 tests OK**，git diff --check 通過。只有既有 Starlette HTTPX deprecation warning。獨立安全／交易複核及測試清理修正再次複核均無待處理問題；遠端 CI 狀態以 PR checks 為準。

| Issue | 本次交付與後續 |
| --- | --- |
| #21 | 明確版本範圍、候選接收人／原因、待審與結果、有限主管資訊 |
| #22 | 核准當下重驗主管／接收人／擁有者／版本與帳號修訂，拒絕跨公司、自收與失效申請 |
| #23 | 原子移轉、重複／並行決策防護、既有一般／歷史 ACL 保留 |
| #25／#26 | 本次是受限 DB 能力與交易測試；RLS、連線重用及情境隔離驗收仍未完成 |
| #27 | 交接 API／頁面／教學已加入；帳號管理與完整 M2 驗收仍待後續 |

RLS 尚未啟用；runtime 仍有既有版本／使用者 SELECT 信任邊界。申請原因由使用者自行填寫，不做內容分類。無撤回／通知、跨檔案批次、主管自收或例外轉移流程；失效 pending 記錄保留作為提出人的紀錄，不由背景工作自動批准。
