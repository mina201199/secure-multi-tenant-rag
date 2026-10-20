# Linux CI

`.github/workflows/ci.yml` 在推送及 PR 事件執行；本文件記錄工作流程與本機驗證，**遠端執行結果以對應 commit／PR 的 Actions checks 為準，未設定 branch protection 或 required checks**。本機測試紀錄與遠端 workflow 成功是兩種不同證據。

## 執行範圍

- 所有 branch push、所有 PR 的 opened / synchronize / reopened，以及手動 workflow_dispatch。PR 不設 base branch filter，因此堆疊 PR 指向 `codex/*` 時也會執行。PR 預設測試 GitHub 產生的 merge ref；push 測試該 push 的 commit。
- `postgres`：Ubuntu 24.04 的 Docker Compose，依 Dockerfile 的 lock constraints 建置 runtime / test image，`setup` 完成後啟動 web、等候 healthcheck 並檢查 HTTP `/health`，再用真實 PostgreSQL 執行整個 `tests/` 與 `evals/tests/`。
- `offline`：獨立 venv 安裝 `prototypes/parsing/requirements.txt` 的 pypdf / reportlab pins，執行 parser、品質樣本、readiness-tool 與 CI 輸出清理測試。另以 apt 安裝 `fonts-wqy-microhei`，設定 `PARSING_QUALITY_CJK_FONT=/usr/share/fonts/truetype/wqy/wqy-microhei.ttc`，並明確檢查檔案存在。不啟動 DB，也不呼叫模型或付費 API。預設執行；手動執行可取消 `run_offline`。這是額外的原型證據，不能替代 PostgreSQL job 或 RLS 驗收。Linux font path 參照 [Debian 官方檔案清單](https://packages.debian.org/bookworm/all/fonts-wqy-microhei/filelist)，Ubuntu runner 的實際安裝／字型處理仍待第一個遠端 run 確認。

應用程式測試使用 session bootstrap 與共享 seed / DB 狀態；**保持單一 serial pytest invocation，不加 xdist、不做資料庫測試 matrix 或 shard**。新增的 HTML 測試會由 `tests/` 自動收錄，不固定宣稱永遠只有 95 項。

## 憑證、隔離與清理

使用 `pull_request`，不使用 `pull_request_target`。workflow 僅授予 `contents: read`，checkout 不保留 Git credentials；不需要設定任何 repository secret、外部服務 token 或正式資料。每個 PostgreSQL job 產生六個不同的 64 字元隨機十六進位 fixture 值，先以 GitHub `add-mask` 註冊，再以 0600 `.env` 寫入。它們只用在當次虛構 seed 與容器，不得把本機 `.env` 上傳。

Compose project 使用 `ci-<run_id>-<run_attempt>`，隔離 network / volumes；各 job 使用新的 hosted runner。本機 compose 的固定 image tag / 8008 port 不適合多個 job 共用同一台長期 runner，因此此設定**限 GitHub-hosted runner**。不以共享 self-hosted runner 執行不可信 PR。

`always()` teardown 執行 `docker compose down --volumes --remove-orphans`，接著刪除 `.env`。一般失敗與取消會嘗試清理；強制終止或 runner 遺失仍依賴 hosted runner 的銷毀。workflow 不會刪除開發者本機 compose project。

## 可保存的證據

`tools/ci/run_tests.py` 丟棄測試 stdout / stderr，只輸出固定欄位的 JSON：schema、runner、count_unit、exit code、tests / failures / errors / skipped 數字。pytest 的 `count_unit=junit_reported_cases_including_subtests`：JUnit 會計入 subtests，不能把其 tests 值當成獨立 pytest 測試函式數；unittest 則為 unittest_cases。pytest 的原始 JUnit XML 只存在容器暫存目錄，讀取總數後移除；artifact 不含 testcase 名稱、failure body、stdout、文件文字、SQL、cookie、連線字串或環境變數。artifact 使用明確 JSON 路徑並保留七天；不收集 `.env`、完整 Compose config、容器 logs 或原始 JUnit。

因此 CI 失敗時只有總數與 exit code，需在本機以同一 commit 重現細節。若在產生 summary 前 build / startup 失敗，不會有測試 JSON；job 的失敗 step 仍是證據。此輸出策略不保證惡意 PR 能被限制為無輸出：PR 本身可修改 workflow / 測試，所以 job 不應接觸真實憑證或資料。

## 本機重現

在專案根目錄，依 README 建立自己的隨機 `.env`，不要覆寫既有開發憑證。Docker 指令需由開發者選定自己的隔離 project，再循序執行：

```bash
docker compose -p secure-ci-check --profile test build setup tests
docker compose -p secure-ci-check --profile test up -d --wait --wait-timeout 180 web
curl --fail http://127.0.0.1:8008/health
docker compose -p secure-ci-check --profile test run --rm -T tests
docker compose -p secure-ci-check --profile test down --volumes --remove-orphans
```

最後一行只清理選定的測試 project。若本機 8008 已被其他 project 使用，先安排測試環境，不要任意停止其他服務。Windows 可用 PowerShell 的 `Invoke-RestMethod` 檢查 `/health`。CI 的 aggregate runner 另外以唯讀 `/ci` bind mount 與可寫 `/results` bind mount 執行，沒有修改 Dockerfile 或 production image。

## 動作來源與維護

兩個 action 皆 pin 完整 SHA，並核對官方 release 的 commit：

- [checkout v4.2.2](https://github.com/actions/checkout/releases/tag/v4.2.2)：[`11bd71901bbe5b1630ceea73d27597364c9af683`](https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683)。
- [upload-artifact v4.6.2](https://github.com/actions/upload-artifact/releases/tag/v4.6.2)：[`ea165f8d65b6e75b540449e92b4886f43607fa02`](https://github.com/actions/upload-artifact/commit/ea165f8d65b6e75b540449e92b4886f43607fa02)。

這是明確選定的版本，不宣稱為最新版本；更新時重新核對 release、完整 SHA 與 runner 相容性。權限與 masking 依 [GitHub secure use](https://docs.github.com/en/actions/reference/security/secure-use)，事件與 branch filter 依 [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。此 workflow 不發布套件、不部署、不寫 PR comment。

## 2026-10-20 驗證邊界

CI helper 的六個測試先觀察失敗，實作後通過，涵蓋 XML 內敏感 body 不進 JSON、nested suite 不重複加總、非法數字拒絕，以及 child exit 0 但 report 缺失／零測試／含失敗時不能報成功。readiness-tool 四項與 parser／品質案例 26 項在本機通過。整合協調者另在一次性 Debian slim 容器安裝原型依賴與 WQY 字型後執行 26 項通過，確認 Linux 字型相容；這仍不是 GitHub Ubuntu runner 實際執行。PostgreSQL / startup 的最新本機結果見本次整合驗證紀錄。第一個遠端 run 仍需檢查 Actions 的 build、health、serial tests、artifact 與 cleanup。
