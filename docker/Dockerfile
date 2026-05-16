# syntax=docker/dockerfile:1.7

# ---------- go-hnsw build ----------
FROM golang:1.22-bookworm AS hnsw-builder
WORKDIR /src
COPY go-hnsw/ ./go-hnsw/
WORKDIR /src/go-hnsw
RUN go mod download && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /out/hnsw-server ./cmd/server

# ---------- python runtime ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY reposage ./reposage
RUN pip install --upgrade pip && pip install -e .

COPY --from=hnsw-builder /out/hnsw-server /usr/local/bin/hnsw-server

EXPOSE 8000 50051
CMD ["uvicorn", "reposage.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
