#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
export TZ=Asia/Ho_Chi_Minh
export GRANIAN_WORKERS=1
export REFLEX_TELEMETRY_ENABLED=false
export REFLEX_SSR=false
export REFLEX_CHECK_LATEST_VERSION=false
export REFLEX_API_URL=http://localhost:3000
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
# Chỉ backend: không chạy build, Bun, Node hay tải công cụ frontend lúc cấp nguồn.
exec "$PROJECT_DIR/venv/bin/reflex" run --env prod --backend-only --backend-host 127.0.0.1 --backend-port 8000
