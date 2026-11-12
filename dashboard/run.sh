#!/usr/bin/env bash
# SCOPE-X Dashboard launcher (React + FastAPI)
# Usage: bash dashboard/run.sh [PORT]
set -e

PORT="${1:-8000}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ""
echo "  ⚡ SCOPE-X DASHBOARD (React + FastAPI)"
echo "  ────────────────────────────────────────────────────────"
echo "  Modern Soft-UI (Jobgio Aesthetic) · Task 19"
echo ""
echo "  Server: http://localhost:${PORT}"
echo "  API:    http://localhost:${PORT}/docs"
echo "  Root:   ${REPO_ROOT}"
echo ""

cd "${REPO_ROOT}"

# Build frontend if dist doesn't exist
if [ ! -d "${REPO_ROOT}/dashboard/frontend/dist" ]; then
    echo "  [1/2] Building React frontend..."
    cd "${REPO_ROOT}/dashboard/frontend"
    npm run build
    cd "${REPO_ROOT}"
fi

echo "  [2/2] Launching FastAPI backend + React static server..."
exec "${REPO_ROOT}/.venv/bin/python" -m uvicorn dashboard.backend.main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --reload
