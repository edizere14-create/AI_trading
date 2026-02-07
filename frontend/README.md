# AI Trading Frontend

This frontend is designed to interact with the FastAPI backend for live trading, backtesting, and monitoring.

## Getting Started

1. Choose a frontend framework (React, Vue, Svelte, etc.)
2. Install dependencies (e.g., `npm install` for React)
3. Connect to backend endpoints:
   - `/strategies` for listing strategies
   - `/strategies/<name>/signal` for running strategies
   - `/strategies/backtest` for backtesting
   - `/trade/kraken/order` for live trading
   - `/metrics` for monitoring

## Example Features
- Strategy selection and signal visualization
- Backtest results display
- Live trading order form
- Monitoring dashboard (Prometheus metrics)

## Development
- Use `.env` for API URLs
- Add authentication if needed

## Deployment
- Build static files and serve via Docker or a web server

---
For detailed integration, create API service files and UI components for each feature. Reach out for code samples or starter templates!
