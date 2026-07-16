#!/usr/bin/env bash
# 사용: ./run.sh   또는   bash run.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

PORT="${STREAMLIT_PORT:-8502}"
URL="http://localhost:${PORT}"

# 서버가 뜨면 브라우저(또는 Cursor 포트포워딩 환경)에서 열기 시도
open_when_ready() {
  for _ in $(seq 1 40); do
    if curl -sf "$URL" >/dev/null 2>&1; then
      if [ -n "${DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 || true
      fi
      echo ""
      echo "앱이 실행 중입니다 → $URL"
      echo ""
      return 0
    fi
    sleep 0.25
  done
}

open_when_ready &
exec streamlit run app.py --server.port="$PORT"
