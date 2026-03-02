# Production Deployment Guide

## Quick Start (Live + Demo)

### 1) Start and verify local app (API + Dashboard)

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

```powershell
npm run kraken:demo:smoke
```

Expected output includes:

- `DEMO_SMOKE_RESULT=OK`

### 3) One-command full verification (local + Kraken demo)

```powershell
npm run local:smoke:all
```

Expected output includes:

- `SMOKE_LOCAL_RESULT=OK`
- `DEMO_SMOKE_RESULT=OK`
- `SMOKE_LOCAL_ALL_RESULT=OK`

## Streamlit Community Cloud (Monitor)

Deploy the monitor UI from `cloud_streamlit/streamlit_app.py`.

### 1) Push repo to GitHub

Ensure these files are in your branch:

- `cloud_streamlit/streamlit_app.py`
- `cloud_streamlit/requirements.txt`

### 2) Create app in Streamlit Cloud

In Streamlit Community Cloud app setup:

- Repository: this repository
- Branch: your deployment branch
- Main file path: `cloud_streamlit/streamlit_app.py`

### 3) Set app secrets

In **Settings → Secrets**:

```toml
API_BASE_URL = "https://<your-backend-domain>"
```

Notes:

- Do not use a `streamlit.app` URL as `API_BASE_URL`.
- Do not use `127.0.0.1` in cloud deployment.
- Backend must expose `/health`, `/momentum/status`, `/momentum/history` publicly.

### 4) Verify deployment

- Open the deployed Streamlit URL
- Confirm health/status/history return without 4xx/5xx errors

## Render Deployment Requirements (Dashboard)

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

### Render free-tier spin-down note

This dashboard uses `st_autorefresh` (default every 2 seconds), but browser-side refresh does not prevent Render free-tier sleep.
Use an external monitor (for example UptimeRobot) to ping a backend health endpoint like `/health` if you need to reduce cold starts.

## Docker Deployment

### 1. Build & Run Locally


```bash
docker-compose up -d
```

This starts:
- **API** (FastAPI) on http://localhost:8000
- **PostgreSQL** on localhost:5432
- **Prometheus** on http://localhost:9090
- **Grafana** on http://localhost:3000 (admin/admin)

### 2. Run Tests

```bash
docker exec ai_trading_api pytest tests/ -v
```

### 3. Cloud Deployment (AWS ECS)

#### 3.1 Create ECR Repository
```bash
aws ecr create-repository --repository-name ai-trading-api
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
```

#### 3.2 Build & Push Image
```bash
docker build -t ai-trading-api .
docker tag ai-trading-api:latest <account>.dkr.ecr.us-east-1.amazonaws.com/ai-trading-api:latest
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/ai-trading-api:latest
```

#### 3.3 Deploy to ECS
```bash
aws ecs create-service \
  --cluster ai-trading \
  --service-name api \
  --task-definition ai-trading-api:1 \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxxxx],securityGroups=[sg-xxxxx]}"
```

### 4. GCP Cloud Run

```bash
gcloud run deploy ai-trading-api \
  --source . \
  --platform managed \
  --region us-central1 \
  --set-env-vars DATABASE_URL=postgresql://... \
  --set-env-vars KRAKEN_API_KEY=... \
  --set-env-vars KRAKEN_API_SECRET=...
```

### 5. Kubernetes (K8s)

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml
```

## Environment Variables

```bash
# .env
DATABASE_URL=postgresql://user:password@host:5432/ai_trading
KRAKEN_API_KEY=your_key
KRAKEN_API_SECRET=your_secret
ENVIRONMENT=production
LOG_LEVEL=INFO
```

## Monitoring

- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000
- **API Logs**: `docker logs ai_trading_api`

## Database Migrations

```bash
# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## Scaling

For production:
1. **Use PostgreSQL** (not SQLite)
2. **Enable HTTPS** with SSL certificates
3. **Add rate limiting** (fastapi-limiter)
4. **Use reverse proxy** (Nginx)
5. **Enable caching** (Redis)