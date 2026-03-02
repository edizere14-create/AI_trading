# AI_trading

## Streamlit All-in-One (No FastAPI)

Run Streamlit directly with PostgreSQL + Kraken Futures:

```powershell
$env:STREAMLIT_APP_MODE="all-in-one"
streamlit run .\streamlit_app.py
```

Required `.env` keys:

- `DATABASE_URL`
- `KRAKEN_API_KEY`
- `KRAKEN_API_SECRET`
- `KRAKEN_FUTURES_DEMO=true` (recommended)

Optional:

- `KRAKEN_FUTURES_SYMBOL=BTC/USD:USD`
- `KRAKEN_TRADING_ENABLED=true` (only if you want emergency close-all to submit live orders)

## Quick Start (Live + Demo)

### 1) Start and verify local app (API + Dashboard)

Run:

```powershell
npm run local:smoke
```

Expected output includes:

- `SMOKE_API_STATUS=200`
- `SMOKE_UI_STATUS=200`
- `SMOKE_LOCAL_RESULT=OK`

Open:

- Dashboard: `http://127.0.0.1:8501`
- API docs: `http://127.0.0.1:8000/docs`

### 2) Verify Kraken Futures demo connectivity + safe order lifecycle

Set in `.env`:

- `KRAKEN_API_KEY=...`
- `KRAKEN_API_SECRET=...`

Run:

```powershell
npm run kraken:demo:smoke
```

Expected output includes:

- `DEMO_SMOKE_RESULT=OK`

### 3) One-command full verification (local + Kraken demo)

Run:

```powershell
npm run local:smoke:all
```

Expected output includes:

- `SMOKE_LOCAL_RESULT=OK`
- `DEMO_SMOKE_RESULT=OK`
- `SMOKE_LOCAL_ALL_RESULT=OK`

## Render deploy (Backend + Dashboard)

This repo includes `render.yaml` so you can deploy both services from one blueprint:

- `ai-trading-backend` (FastAPI API)
- `ai-trading-dashboard` (Streamlit UI with AI insight panel, risk gates, dynamic sizing controls, emergency controls)

### Deploy steps

1. In Render, choose **New +** → **Blueprint**.
2. Select this GitHub repo and branch `streamlit-deploy`.
3. Render will detect `render.yaml` and create both services.
4. For `ai-trading-backend`, set secrets in Render env vars:
	- `KRAKEN_API_KEY`
	- `KRAKEN_API_SECRET`
	- `JWT_SECRET`
	- `DATABASE_URL` (Render Postgres recommended)
5. Trigger deploy for both services.

### Dashboard service requirements (Render)

Set this env var in the Streamlit service:

- `STREAMLIT_APP_MODE=all-in-one` for standalone mode
- `STREAMLIT_APP_MODE=backend-api` when the dashboard talks to FastAPI

For `all-in-one`, also set:

- `DATABASE_URL`
- `KRAKEN_API_KEY`
- `KRAKEN_API_SECRET`

Repository structure expected by `streamlit_app.py`:

```text
.
├── streamlit_app.py
├── requirements.txt
├── .gitignore
└── app/
	├── core/
	├── services/
	└── ui/
```

`requirements.txt` should include at least:

- `streamlit`
- `streamlit-autorefresh`
- `requests`
- `httpx`

`.gitignore` should exclude deployment-unsafe local files:

- `.env`
- `.venv/`
- `*.db`
- `logs/`

### Connect Kraken Futures Demo

Use these backend environment values:

- `TRADING_PAPER_MODE=true`
- `EXECUTION_EXCHANGE_ID=krakenfutures`
- `MARKET_DATA_EXCHANGE_ID=krakenfutures`
- `KRAKEN_FUTURES_DEMO=true`
- `MOMENTUM_DEFAULT_SYMBOL=PI_XBTUSD`
- `MOMENTUM_AUTO_START=true`

Optional for live close-all/orders in all-in-one mode only:

- `KRAKEN_TRADING_ENABLED=true`

### Post-deploy smoke checks

Run against your backend URL:

```powershell
$base='https://ai-trading-1-dwvg.onrender.com'
Invoke-RestMethod "$base/health"
Invoke-RestMethod "$base/momentum/start?symbol=PI_XBTUSD" -Method Post
Invoke-RestMethod "$base/momentum/status"
Invoke-RestMethod "$base/risk/close-all" -Method Post
```

### Render free-tier spin-down note

This dashboard uses `st_autorefresh` (default every 2 seconds), but browser-side refresh does not prevent Render free-tier sleep.
Use an external monitor (for example UptimeRobot) to ping a backend health endpoint like `/health` if you need to reduce cold starts.