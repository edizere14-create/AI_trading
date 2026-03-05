# AI Trading Architecture (Milestone 1)

## Components
- FastAPI backend (REST + WebSocket)
- Strategy workers (signal generation + execution)
- Market data normalizer/validator
- Streamlit operations dashboard

## Data flow
1. Ingest exchange data
2. Normalize and validate
3. Serve via API/WS
4. Workers generate signals and execute
5. Dashboard visualizes state/trades/analytics