#!/usr/bin/env bash

set -euo pipefail

resolve_project_root() {
  local candidate
  local checks=()

  if [ -n "${RENDER_PROJECT_ROOT:-}" ]; then
    checks+=("${RENDER_PROJECT_ROOT}")
  fi
  checks+=("$(pwd)")
  checks+=("$(cd "$(dirname "$0")" && pwd)")

  for candidate in "${checks[@]}"; do
    local current="$candidate"
    for _ in 1 2 3 4; do
      if [ -f "${current}/app/main.py" ] && [ -f "${current}/dashboard/streamlit_app.py" ]; then
        echo "${current}"
        return 0
      fi
      current="$(cd "${current}/.." && pwd)"
    done
  done

  return 1
}

PROJECT_ROOT="$(resolve_project_root || true)"
if [ -z "${PROJECT_ROOT}" ]; then
  echo "Unable to locate repository root containing app/main.py and dashboard/streamlit_app.py"
  exit 1
fi

cd "${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

BACKEND_PORT="${BACKEND_PORT:-8000}"

# 1. Start FastAPI in the background on an internal port.
python -m uvicorn app.main:app --host 0.0.0.0 --port "${BACKEND_PORT}" &

# 2. Wait briefly for backend startup.
sleep 5

# 3. Start Streamlit on Render's public port.
streamlit run "${PROJECT_ROOT}/dashboard/streamlit_app.py" \
  --server.port "${PORT:-10000}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
