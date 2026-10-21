# #20 歷史版本調閱與獨立授權

使用者於 2026-10-21 明確選擇：封存後，即使有歷史授權，後台也一律拒絕調閱。此規則優先於既有決策稿對後台調閱的較寬描述。

## 範圍與邊界

- 只開放未封存文件中，已被目前版本取代、status=ready 的版本；目前版本、未完成版本與封存文件均不可透過歷史入口讀取。
- 歷史 ACL 獨立存表。只有該版本擁有者可逐人授予／撤銷同公司帳號的歷史權；授予時帳號須有效，可撤銷已停用帳號。包含版本擁有者本人在內，都必須有明確歷史 ACL 才能調閱正文。
- 文件目前擁有者、一般閱讀 ACL、公司公開設定、管理員與主管身分均不產生歷史權。授權歷史版本也不改一般閱讀 ACL。
- 封存保留 ACL 與稽核，但隱藏歷史清單、授權設定、稽核頁與正文；不解除封存、不改有效版本、不開放下載與 RAG。
- Session、Origin／CSRF 與 bigint 嚴格驗證沿用既有方式。不接受 client tenant／actor。無權及不存在同樣 404。

## 資料與交易

新增 migration 008、history_readers、history_audit_events 與 NOLOGIN 受限 capability 角色。web／worker 不取得新表直接讀寫權限；固定 search_path 的 SECURITY DEFINER 能力驗證身分與授權。稽核只記 actor、tenant、document、version、事件、subject 與時間，不存正文或 Session。

調閱與授權變更鎖 outgoing current version → document，再以新 statement 重查指標、封存、版本、帳號與權限。歷史 ACL 操作以 document 鎖序列化。ACL／稽核外鍵會鎖歷史版本，因此先以 FOR SHARE NOWAIT 取得歷史版本鎖；遇到舊 outgoing 衝突立即回 409，不能持有 document 鎖反向等待。這也使版本擁有者在交易內保持一致。未來交接必須使用相同 document 鎖協定更新版本擁有者。

成功調閱／授權／撤權的稽核與動作同一交易；回傳內容前明確 commit，稽核失敗或 commit 失敗不交付正文。拒絕請求不算成功調閱，不寫入錯誤版本的稽核。授權與調閱鎖期間以 FOR SHARE 保持 actor／subject 有效性；後續請求重驗。已完成交付的內容無法撤回。

## 入口

- GET `/api/history/versions?after_id=0`：僅列可管理或有歷史 ACL 的版本摘要，一頁最多 100 筆，無正文。
- GET／PUT `/api/history/versions/{version_id}/access`：版本擁有者取得設定／逐人授權與撤權，PUT 接受 reader_id、granted、csrf_token。
- POST `/api/history/versions/{version_id}/read`：CSRF 保護的調閱動作，回傳內容與 audit_id；GET 不提供正文。
- `/history`、`/history/versions/{version_id}/access` 及 POST `/history/versions/{version_id}/read`：正式 Jinja2 後台，不使用 demo 資料。
- 授權頁顯示最近 50 筆版本稽核；只有版本擁有者可看。頁面與 API 回應使用 Cache-Control: no-store。

## 驗證

真實 PostgreSQL 測試涵蓋正向讀取、獨立 ACL、版本／文件擁有者差異、同公司／跨公司／角色、停用、封存、HTML escaping、CSRF、偽造欄位、資料保留、稽核與失敗回滾，以及撤權／封存／停用等待後重驗。先觀察新增測試失敗，再實作與重跑完整回歸；獨立複核後提交 PR。
