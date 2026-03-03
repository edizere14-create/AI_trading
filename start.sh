#!/bin/bash

set -e

PROJECT_ROOT="${RENDER_PROJECT_ROOT:-/opt/render/project/src}"
cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH}"

# 1. Start FastAPI in the background on Port 8000
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# 2. Wait 5 seconds for the backend to wake up
sleep 5

# 3. Start Streamlit in the foreground on Render's public Port ($PORT)
streamlit run dashboard/streamlit_app.py --server.port "${PORT:-10000}" --server.address 0.0.0.0
