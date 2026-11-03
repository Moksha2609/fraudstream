# fraudstream backend image: API, queue consumer and replay producer share it.
#
# Build context is ./backend. The model directory /models is populated in one
# of two ways:
#   * local/dev: docker-compose mounts ../models (where `ml/train.py` writes)
#     read-only over /models, so nothing is baked in.
#   * AWS: the deploy workflow syncs the approved model from S3 into
#     backend/models/ before `docker build`, and the COPY below bakes it in.
#     backend/models/ is normally empty (a .gitkeep keeps it present so the
#     COPY never fails).
#
#   docker build -t fraudstream/backend backend/
#   docker run --rm -p 8000:8000 -v $PWD/models:/models:ro fraudstream/backend

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp1: OpenMP runtime required by xgboost wheels; curl: container healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# install dependencies first so source edits do not invalidate the pip layer
COPY pyproject.toml ./
COPY fraudstream ./fraudstream
COPY ml ./ml
RUN pip install ".[postgres]"

# optional baked-in model (see header). Runtime mounts may shadow this directory.
COPY models/ /models/

RUN useradd --system --uid 1000 --create-home app \
    && chown -R app:app /app /models
USER app

ENV FS_MODEL_DIR=/models \
    FS_MODEL_NAME=fraud_xgb \
    FS_API_HOST=0.0.0.0 \
    FS_API_PORT=8000

EXPOSE 8000

# only meaningful for the API; compose/ECS disable it for consumer/replay containers
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${FS_API_PORT}/health" || exit 1

CMD ["fraudstream-api"]
