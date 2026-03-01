# AI Trading Frontend (Institutional Scaffold)

React + TypeScript + Vite frontend scaffold wired to the live backend endpoints.

## Modules in Sidebar

- Dashboard
- Live Trading
- Backtesting
- AI Models
- Portfolio
- Risk Management
- Analytics
- API Keys
- Settings

## Live Endpoints Wired

- `/health`
- `/momentum/status`
- `/momentum/start`
- `/momentum/stop`
- `/risk/limits`
- `/risk/close-all`
- `/backtest/summary`
- `/backtest/analytics`
- `/trade/orders`

## Local Run

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

## Build

```bash
npm run build
npm run preview
```
