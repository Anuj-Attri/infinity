# Infinity — a long-running container, NOT a serverless function.
# That is a hard requirement, not a preference: the chat path streams NDJSON and
# research/browse jobs run for minutes. Serverless adapters buffer responses and
# enforce request timeouts, which turns streaming into a dead-feeling lump and
# kills long jobs outright.
#
# One image serves both halves. The API mounts the built SPA at "/" AFTER every
# API route, so the catch-all cannot shadow /health or any endpoint. Same origin
# means no CORS surface and one deploy instead of two — worth more than a CDN
# while the user count is in single digits. Splitting the frontend onto
# Cloudflare Pages later is a config change: that deploy sets VITE_API_BASE_URL
# and this stage stops being used.

# ---- build the SPA ---------------------------------------------------------
FROM node:22-slim AS web

WORKDIR /web
# Manifests first so the dependency layer caches across source changes.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY frontend/ ./
# VITE_API_BASE_URL is deliberately NOT set: with no explicit value a production
# build talks to its own origin, which is exactly right when this image serves
# the page. See frontend/src/api.ts.
#
# The heap bump is not superstition. Bundling Monaco (the editor is served from
# our own origin rather than a CDN — see frontend/src/lib/monacoSetup.ts) pushed
# the Rollup pass past node's default old-space limit IN THIS IMAGE: the build
# climbed to 2000 MB against a 2080 MB ceiling and died with "Ineffective
# mark-compacts near heap limit", exit 134.
#
# It passed locally, where the default ceiling is higher — so this is a failure
# that only ever appears in the container, which is exactly the class of thing
# the image job exists to catch.
ENV NODE_OPTIONS=--max-old-space-size=4096
RUN npm run build


# ---- runtime ---------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so the layer caches across code changes.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
# integration.mount_frontend() looks for <parents[2] of app/main.py>/dist,
# which is /srv/dist here.
COPY --from=web /web/dist ./dist

WORKDIR /srv/backend

# Railway injects $PORT. The default keeps a local `docker run` working.
ENV PORT=8000
EXPOSE 8000

# One worker: streaming plus in-process state does not want to be forked yet.
# Scale by adding container instances, not workers, once there is load to scale.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
