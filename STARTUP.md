# Backend Startup Guide

This document explains how to run the backend locally, including PostgreSQL and Redis.

## 1) Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Redis 6+

If you use Homebrew, you can install dependencies as needed.

## 2) Start PostgreSQL

### Option A: Homebrew (macOS)
```bash
brew install postgresql@14
brew services start postgresql@14
```

Create a database:
```bash
createdb ai_learning_tablet
```

### Option B: Docker
```bash
docker run --name learning-postgres -e POSTGRES_PASSWORD=password -e POSTGRES_DB=ai_learning_tablet -p 5432:5432 -d postgres:14
```

## 3) Start Redis

### Option A: Homebrew (macOS)
```bash
brew install redis
brew services start redis
```

### Option B: Docker
```bash
docker run --name learning-redis -p 6379:6379 -d redis:6
```

## 3.1) Ubuntu Install (Postgres + Redis + Python)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib redis-server
sudo systemctl enable postgresql redis-server
sudo systemctl start postgresql redis-server
```

Create DB:
```bash
sudo -u postgres createdb ai_learning_tablet
```

## 4) Backend Setup

```bash
cd /Users/houyuxin/08Coding/zrobot/learning-lamp-v2/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5) Environment Variables

Copy `.env.example` to `.env` and update as needed:

```bash
cp .env.example .env
```

Recommended defaults:
```
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/ai_learning_tablet
REDIS_URL=redis://localhost:6379/0
```

Optional (for AI features):
```
ZHIPU_API_KEY=...
DOUBAO_API_KEY=...
```

## 6) Create Tables + Test User

```bash
python -m scripts.create_test_user
```

This creates a default test student:
- phone: 13800138000
- password: 123456

## 7) Run Backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:
```
http://localhost:8000/health
```

## 8) Run Integration Tests (Optional)

```bash
export RUN_INTEGRATION=1
export BASE_URL=http://localhost:8000/api/v1/student
pytest -q tests
```

For Zhipu tests:
```bash
export RUN_ZHIPU_TESTS=1
export TEST_IMAGE_URL="https://your-image-url.jpg"
pytest -q tests
```

## 9) Ubuntu Server Deployment Flow (Dev → Prod)

This is a simple, non-CI/CD workflow for a first deployment.

### 9.1 Create GitHub repo + push
```bash
git init
git remote add origin <your-github-repo-url>
git add .
git commit -m "initial backend"
git push -u origin main
```

### 9.2 Dev server (Ubuntu)
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git
sudo systemctl enable docker
sudo systemctl start docker
```

Clone and run:
```bash
git clone <your-github-repo-url>
cd learning-lamp-v2/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.create_test_user
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run tests:
```bash
export RUN_INTEGRATION=1
export BASE_URL=http://<dev-server-ip>:8000/api/v1/student
pytest -q tests
```

### 9.3 Build Docker image on dev server
From repo root (where Dockerfile is located):
```bash
docker build -t <your-registry>/<image-name>:<tag> .
docker push <your-registry>/<image-name>:<tag>
```

### 9.4 Prod server (Ubuntu)
```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
```

Pull and run:
```bash
docker pull <your-registry>/<image-name>:<tag>
docker run -d --name learning-api \
  -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://postgres:password@<db-host>:5432/ai_learning_tablet \
  -e REDIS_URL=redis://<redis-host>:6379/0 \
  -e ZHIPU_API_KEY=... \
  -e DOUBAO_API_KEY=... \
  <your-registry>/<image-name>:<tag>
```
