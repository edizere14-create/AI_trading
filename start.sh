#!/bin/bash

# 1. Start FastAPI in the background on Port 8000
uvicorn main:app --host 0.0.0.0 --port 8000 &

# 2. Wait 5 seconds for the backend to wake up
sleep 5

# 3. Start Streamlit in the foreground on Render's public Port ($PORT)
streamlit run dashboard/streamlit_app.py --server.port $PORT --server.address 0.0.0.0
