FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.110" "uvicorn[standard]>=0.29" "sqlalchemy>=2.0" "alembic>=1.13" \
    "pydantic>=2.6" "pydantic-settings>=2.2" "psycopg[binary]>=3.1" "httpx>=0.27" "pytest>=8.0"

COPY . .
RUN pip install --no-cache-dir -e . && useradd -m nexus && chown -R nexus /app
USER nexus

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
