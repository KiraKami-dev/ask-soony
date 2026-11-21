# One service: FastAPI serves the built UI and the API, and runs the watcher + pipeline loop.
# Portal data is never baked in: the loop fetches it at start with SOONY_PORTAL_TOKEN.
FROM node:22.11.0-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY scripts ./scripts
COPY --from=web /web/dist ./web/dist
ENV ASK_WEB_DIST=/app/web/dist PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "ask-soony run --host 0.0.0.0 --port ${PORT}"]
