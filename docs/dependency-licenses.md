# 相依套件授權盤點（2026-10-20）

這是版本與授權來源的工程盤點，**不是採用核准、完整 SBOM、法律意見或相容性結論**。本次沒有替專案選擇或新增 `LICENSE`；專案自身的授權方式與權利人決定仍待確認。

## 證據與重現方式

正式套件來源是 `requirements.txt`，測試額外來源是 `requirements-dev.txt`；兩者由 Dockerfile 透過 `-c requirements.lock` 約束。以下 32 個 pin 已與本機既有 test image 的 `importlib.metadata` 逐項核對版本及 License-Expression / License / classifier；它包含 runtime 與測試依賴，不能直接當成 production image 的精確清單。production image 不含 pytest / HTTPX 測試安裝步驟。metadata 是套件作者的聲明；不能取代下載 artifact 內完整 license / NOTICE / vendored 元件的檢查。

可在已建置的 test image 用 `docker compose --profile test run --rm --no-deps tests python -c ...` 執行下面的唯讀程式；它不執行 bootstrap 或測試，也不需要連 DB。Compose 本身仍要求 `.env`，不要輸出該檔或 `docker compose config`。在 image 內以專案 lock 清單迭代（或將檔案以唯讀 mount 提供），可避免把 pip 自身等額外 tooling 混入依賴清單：

```python
from importlib import metadata
from pathlib import Path
for line in Path('requirements.lock').read_text().splitlines():
    if '==' not in line or line.lstrip().startswith('#'):
        continue
    name, pinned = line.strip().split('==', 1)
    dist = metadata.distribution(name)
    print(name, pinned, dist.version,
          dist.metadata.get('License-Expression'), dist.metadata.get('License'),
          [x for x in dist.metadata.get_all('Classifier', []) if x.startswith('License ::')],
          dist.metadata.get_all('License-File', []))
```

Dockerfile 的 image 未複製 lock 到另一個目錄：根目錄 `/workspace/requirements.lock` 可用。若版本不符、metadata 缺失或 license-file 不存在，標記待確認，不用最新 upstream 的 license 自動填補舊 artifact。此次 HTTPX 未宣告 License-File header，應另檢查其 wheel 內檔案；Jinja2 metadata 的泛稱 BSD 由其 [官方 BSD-3-Clause 文件](https://jinja.palletsprojects.com/en/stable/license/)補充。

## 正式與測試 lock 清單

同一列多個套件仍代表不同 artifact，發佈前應各自保留相應證據；license 名稱不代表所有 bundled 元件只有同一授權。

| 套件與實際 pin | metadata / 補充來源的授權名稱 | 上游來源 |
|---|---|---|
| fastapi 0.150.0 | MIT | [FastAPI](https://github.com/fastapi/fastapi/blob/master/pyproject.toml) |
| uvicorn 0.54.0 | BSD-3-Clause | [Uvicorn](https://github.com/encode/uvicorn/blob/main/LICENSE.md) |
| psycopg 3.3.6、psycopg-binary 3.3.6、psycopg-pool 3.3.3 | LGPL-3.0-only | [Psycopg](https://github.com/psycopg/psycopg)、[官方 binary 安裝說明](https://www.psycopg.org/psycopg3/docs/basic/install.html) |
| argon2-cffi 25.1.0 | MIT | [argon2-cffi](https://github.com/hynek/argon2-cffi/blob/main/pyproject.toml) |
| argon2-cffi-bindings 26.1.0 | MIT（binding 本身） | [bindings LICENSE](https://github.com/hynek/argon2-cffi-bindings/blob/main/LICENSE) |
| Jinja2 3.1.6 | BSD-3-Clause（官方文件補充） | [Jinja](https://jinja.palletsprojects.com/en/stable/license/) |
| python-multipart 0.0.32 | Apache-2.0 | [python-multipart](https://github.com/Kludex/python-multipart/blob/master/LICENSE.txt) |
| httpx 0.28.1、httpcore 1.0.9 | BSD-3-Clause | [HTTPX](https://github.com/encode/httpx/blob/master/LICENSE.md)、[HTTPCore](https://github.com/encode/httpcore/blob/master/LICENSE.md) |
| pytest 9.1.1 | MIT | [pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| annotated-doc 0.0.5、annotated-types 0.8.0 | MIT | [annotated-doc](https://github.com/fastapi/annotated-doc)、[annotated-types](https://github.com/annotated-types/annotated-types/blob/main/LICENSE) |
| anyio 4.15.1 | MIT | [AnyIO](https://github.com/agronholm/anyio/blob/master/LICENSE) |
| certifi 2026.10.18 | MPL-2.0 | [certifi](https://github.com/certifi/python-certifi/blob/master/LICENSE) |
| cffi 2.1.1 | MIT-0（依此 image metadata，不套用舊版假設） | [CFFI](https://foss.heptapod.net/pypy/cffi) |
| click 8.5.0、MarkupSafe 3.0.4 | BSD-3-Clause | [Click](https://github.com/pallets/click/blob/main/LICENSE.txt)、[MarkupSafe](https://github.com/pallets/markupsafe/blob/main/LICENSE.txt) |
| h11 0.16.0 | MIT | [h11](https://github.com/python-hyper/h11/blob/master/LICENSE.txt) |
| idna 3.20 | BSD-3-Clause | [idna](https://github.com/kjd/idna/blob/master/LICENSE.md) |
| iniconfig 2.3.1、pluggy 1.7.0 | MIT | [iniconfig](https://github.com/pytest-dev/iniconfig/blob/main/LICENSE)、[pluggy](https://github.com/pytest-dev/pluggy/blob/main/LICENSE) |
| opentelemetry-api 1.45.1 | Apache-2.0 | [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python/blob/main/LICENSE) |
| packaging 26.3 | Apache-2.0 OR BSD-2-Clause | [packaging](https://github.com/pypa/packaging/blob/main/LICENSE) |
| pycparser 3.11 | BSD-3-Clause | [pycparser](https://github.com/eliben/pycparser/blob/main/LICENSE) |
| pydantic 2.14.1、pydantic_core 2.50.1 | MIT | [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE)、[pydantic-core](https://github.com/pydantic/pydantic-core/blob/main/LICENSE) |
| Pygments 2.21.0 | BSD-2-Clause | [Pygments](https://github.com/pygments/pygments/blob/master/LICENSE) |
| starlette 1.9.0 | BSD-3-Clause | [Starlette](https://github.com/Kludex/starlette/blob/main/LICENSE.md) |
| typing-inspection 0.4.4 | MIT | [typing-inspection](https://github.com/pydantic/typing-inspection/blob/main/LICENSE) |
| typing_extensions 4.16.0 | PSF-2.0 | [typing_extensions](https://github.com/python/typing_extensions/blob/main/LICENSE) |

以上 upstream 連結供追查，部分指向可變的 main/master；**精確版本授權聲明以此次 image 內 metadata 為證據，不宣稱所有連結都已核對該版本 release 原始碼**。發佈前應保存精確 wheel / sdist hash、license / NOTICE 與對應 source commit，並重新檢查更新。

## Parser 原型（不在 production requirements）

在 Codex 隨附本機 Python 實際讀取到：

| 套件 | 此次觀察版本 | 宣告與來源 |
|---|---|---|
| pypdf | 6.10.0（原型 pin） | metadata BSD-3-Clause；[6.10.0 原始 LICENSE](https://github.com/py-pdf/pypdf/blob/6.10.0/LICENSE) |
| reportlab | 4.4.9（原型 pin） | metadata 為 BSD license 泛稱；安裝內 `reportlab-4.4.9.dist-info/licenses/LICENSE` 存在；[官方開源 toolkit FAQ](https://docs.reportlab.com/developerfaqs/) |
| pillow | 12.3.0（此環境的 ReportLab transitive） | metadata MIT-CMU；[官方說明](https://pillow.readthedocs.io/en/stable/about.html) |
| charset-normalizer | 3.5.2（此環境的 ReportLab transitive） | metadata MIT；[上游 LICENSE](https://github.com/jawah/charset_normalizer/blob/master/LICENSE) |

ReportLab Requires-Dist 指出 pillow>=9.0.0 與 charset-normalizer；未使用其 accel / renderpm / pycairo / bidi / shaping extras。pypdf 的 crypto / cryptodome / image extras 也未啟用。以上 transitive 版本是此次環境觀察值，**未寫入原型 lock**，乾淨 Linux CI 可能解析成其他版本；重現與正式採用前需另鎖定、盤點。原型安裝檔另有 `reportlab/fonts/bitstream-vera-license.txt`，不能僅用 ReportLab 的頂層 BSD 代表字型。

品質樣本新增實際嵌入中文字型的 PDF。Windows 本機使用系統 Microsoft JhengHei，字型檔未提交；這不構成該字型的再散佈授權結論。Linux CI 安裝系統 `fonts-wqy-microhei`，其 [Debian 官方 copyright](https://sources.debian.org/src/fonts-wqy-microhei/0.2.0-beta-2/debian/copyright) 對字型列 `Apache-2.0 or GPL-3+ with Font exception`，對 Debian packaging / fontconfig 設定另列 GPL-3+；不能省略 font exception 或把整個 package 都簡化成 Apache。參照的 source package 版本不代表 Ubuntu runner 已安裝版本，遠端執行後仍須核對 `/usr/share/doc/fonts-wqy-microhei/copyright`。目前 workflow 僅保存測試 counts，不上傳嵌入字型的 PDF 或 font binary。

## 尚待決定與未涵蓋範圍

- 專案權利人須決定自身授權、發佈方式與 notices 交付方式；本次不選 license、不宣稱可商用或已符合授權。
- Psycopg / binary wheel 的 LGPL、certifi 的 MPL、Apache NOTICE 等條件要依實際修改、連結、打包及散佈方式檢查；此清單不作條件適用的法律結論。psycopg-binary 自帶的 libpq / libssl 等原生元件需要另列版本與 notices；argon2 / cffi / Pillow wheel 的 vendored 元件亦同。
- `python:3.12-slim`、`pgvector/pgvector:pg16` 是可變 container tag，OS packages、Python runtime、PostgreSQL、pgvector、基底 layer 與其原生函式庫不在 Python requirements lock 內。發佈前記錄 image digest 並產生 container inventory；不可把本表當完整容器授權清單。
- 原有 parser 基礎測試用 ReportLab `MSung-Light` CID font reference 產生虛構 PDF；新增品質樣本則使用上節所述的嵌入 MSJhengHei／WQY 字型。CID reference 不等於 repository 擁有或可散佈實際字型；兩種測試證據應分開記錄。新增／嵌入字型、PDF 圖像與外部測試 corpus 時，要逐一保存來源、權利與授權；生成的文件也不能只憑生成器授權判定內容權利。
- 此 repository 沒有新增 OCR、模型、tokenizer、embedding、外部資料集或商用 ReportLab 套件。日後採用須另列依賴與服務條款，不能沿用原型結論。
