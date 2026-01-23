# Production Deployment (Docker)

This guide shows a simple, reproducible Docker-based deployment for production. It assumes a single Linux server and no CI/CD.

## 1) Prerequisites

- Docker + Docker Compose installed on the server
- A domain name (optional but recommended)
- Open ports: 80/443 (if using Nginx), 8093 (if exposing app directly)

## 2) Files to Prepare

Create these files on the server (or copy from your repo):
- `Dockerfile`
- `docker-compose.yml`
- `.env.prod` (environment variables)

## 3) Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8093"]
```

## 4) docker-compose.yml

```yaml
version: "3.9"
services:
  db:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:6
    ports:
      - "6379:6379"

  api:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
      ZHIPU_API_KEY: ${ZHIPU_API_KEY}
      DOUBAO_API_KEY: ${DOUBAO_API_KEY}
      # OSS settings
      OSS_ACCESS_KEY_ID: ${OSS_ACCESS_KEY_ID}
      OSS_ACCESS_KEY_SECRET: ${OSS_ACCESS_KEY_SECRET}
      OSS_ENDPOINT: ${OSS_ENDPOINT}
      OSS_BUCKET_NAME: ${OSS_BUCKET_NAME}
      OSS_CDN_DOMAIN: ${OSS_CDN_DOMAIN}
      OSS_ROLE_ARN: ${OSS_ROLE_ARN}
      OSS_REGION_ID: ${OSS_REGION_ID}
    ports:
      - "8093:8093"
    depends_on:
      - db
      - redis

volumes:
  pgdata:
```

## 5) .env.prod Example

```bash
# Postgres
POSTGRES_PASSWORD=change_me
POSTGRES_DB=ai_learning_tablet

# App DB + Redis
DATABASE_URL=postgresql+asyncpg://postgres:change_me@db:5432/ai_learning_tablet
REDIS_URL=redis://redis:6379/0

# AI services (optional)
ZHIPU_API_KEY=
DOUBAO_API_KEY=

# OSS (optional)
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
OSS_ENDPOINT=
OSS_BUCKET_NAME=
OSS_CDN_DOMAIN=
OSS_ROLE_ARN=
OSS_REGION_ID=
```

## 6) Build and Run

```bash
docker compose --env-file .env.prod up -d --build
```

## 7) Initialize Database

If you use Alembic migrations:
```bash
docker compose exec api alembic upgrade head
```

If you use the provided script for initial tables and test users:
```bash
docker compose exec api python -m scripts.create_test_user
```

## 8) Health Check

```
http://<server-ip>:8093/health
```

## 9) (Optional) Nginx Reverse Proxy

For HTTPS and a friendly domain, run Nginx on the host and proxy to `localhost:8093`.

Example server block:
```
server {
  listen 80;
  server_name your-domain.com;

  location / {
    proxy_pass http://127.0.0.1:8093;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

For TLS, use Certbot or your preferred certificate manager.

