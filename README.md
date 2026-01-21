# Backend

FastAPI backend for the AI learning tablet.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create tables and seed a test user:
```bash
python -m scripts.create_test_user
```

Run the server:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:
```
http://localhost:8000/health
```

## Tests

```bash
export RUN_INTEGRATION=1
export BASE_URL=http://localhost:8000/api/v1/student
pytest -q tests
```

For Zhipu integration:
```bash
export RUN_ZHIPU_TESTS=1
export TEST_IMAGE_URL="https://your-image-url.jpg"
pytest -q tests
```

## Docs

- API: `API.md` (repo root)
- Startup: `STARTUP.md` (repo root)
- Production: `PRODUCTION_DEPLOY.md` (repo root)
