# Production Deployment Guide

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