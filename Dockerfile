# One image: build the frontend, then run FastAPI, which serves frontend/dist from the same origin.

FROM node:22-slim AS web
RUN npm install -g pnpm@10.33.0
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY --from=web /app/frontend/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH" \
    DATABASE_URL="sqlite+aiosqlite:////data/onboarding.db" \
    DATA_DIR="/data/files"
EXPOSE 8000
# One worker: live calls, timers and sockets live in the process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
