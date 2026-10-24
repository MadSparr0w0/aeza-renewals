FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/invoice_keeper.db
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --no-log-init app \
    && mkdir -p /app/data \
    && chown app:app /app/data
COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app licenses/ ./licenses/
COPY --chown=app:app assets/ ./assets/
COPY --from=frontend-build --chown=app:app /build/frontend/dist/ ./frontend/dist/
USER app
EXPOSE 8000
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
