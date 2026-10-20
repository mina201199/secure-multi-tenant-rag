FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /workspace
COPY requirements.txt requirements.lock ./
# 以 lock 檔約束版本，讓每台機器建出相同的相依套件
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock
RUN useradd --create-home --uid 10001 rag
COPY --chown=rag:rag app ./app

# 測試映像才安裝 pytest / httpx 並複製 tests，正式映像不帶測試工具
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt -c requirements.lock
COPY --chown=rag:rag tests ./tests
COPY --chown=rag:rag evals ./evals
USER rag
CMD ["pytest", "-q", "--tb=short", "-p", "no:cacheprovider"]

# 放在最後，讓未指定 target 的 docker build 預設產生正式映像
FROM base AS runtime
USER rag
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
