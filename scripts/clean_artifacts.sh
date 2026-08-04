#!/usr/bin/env bash
# 런타임 산출물·캐시 정리 (소스/.gitkeep 유지)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Cleaning exports/charts (keep .gitkeep)..."
find exports/charts -type f ! -name '.gitkeep' -delete 2>/dev/null || true

echo "Cleaning exports/merges (keep .gitkeep)..."
find exports/merges -type f ! -name '.gitkeep' -delete 2>/dev/null || true

echo "Cleaning __pycache__..."
find . -type d -name '__pycache__' \
  -not -path './.venv/*' \
  -exec rm -rf {} + 2>/dev/null || true

echo "Cleaning .pytest_cache..."
rm -rf .pytest_cache

echo "Done."
