# RoleMatch API / worker image
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 rolematch
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

# Model weights are fetched at build time from the official Hugging Face repository.
# Override with --build-arg MODEL_REVISION=<commit> and pin ROLEMATCH_MODEL_SHA256 for reproducible builds.
ARG MODEL_REVISION=main
ARG ROLEMATCH_MODEL_SHA256=""
COPY backend/scripts/fetch_model.py /app/backend/scripts/fetch_model.py
RUN ROLEMATCH_MODEL_SHA256="$ROLEMATCH_MODEL_SHA256" python /app/backend/scripts/fetch_model.py --dest /app/models/all-MiniLM-L6-v2 --revision "$MODEL_REVISION"

COPY backend /app/backend
COPY config /app/config
COPY fixtures /app/fixtures
RUN mkdir -p /app/var/uploads && chown -R rolematch /app/var
USER rolematch
WORKDIR /app/backend
ENV ROLEMATCH_EMBEDDING_MODEL_DIR=/app/models/all-MiniLM-L6-v2 \
    ROLEMATCH_CONFIG_DIR=/app/config \
    ROLEMATCH_FIXTURES_DIR=/app/fixtures \
    ROLEMATCH_UPLOAD_DIR=/app/var/uploads
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
