#!/usr/bin/env bash
# SCOPE-X Dashboard launcher
# Usage: bash dashboard/run.sh [--port 8501]
set -e

PORT="${1:-8501}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ""
echo "  ⚡ SCOPE-X DASHBOARD"
echo "  ───────────────────────────────────"
echo "  Retro Funk Neo-Brutalist UI · Task 19"
echo ""
echo "  URL : http://localhost:${PORT}"
echo "  Root: ${REPO_ROOT}"
echo ""

cd "${REPO_ROOT}"
"${REPO_ROOT}/.venv/bin/streamlit" run dashboard/app.py \
    --server.port "${PORT}" \
    --server.headless true \
    --theme.base "light" \
    --theme.backgroundColor "#EDE8DF" \
    --theme.secondaryBackgroundColor "#F5F0E8" \
    --theme.textColor "#111111" \
    --theme.font "monospace"
