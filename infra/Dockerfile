# Mark 4 backend — a long-running container, NOT a serverless function.
# That is a hard requirement, not a preference: the chat path streams NDJSON and
# research/browse jobs run for minutes. Serverless adapters buffer responses and
# enforce request timeouts, which turns streaming into a dead-feeling lump and
# kills long jobs outright.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so the layer caches across code changes.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
WORKDIR /srv/backend

# Railway injects $PORT. Default keeps local `docker run` working.
ENV PORT=8000
EXPOSE 8000

# One worker: streaming plus in-process state does not want to be forked yet.
# Scale by adding container instances, not workers, once there is load to scale.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
