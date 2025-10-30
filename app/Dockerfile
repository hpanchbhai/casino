# Backend: FastAPI + Uvicorn
FROM python:3.11-slim

WORKDIR /app

# System deps (psycopg/asyncpg often need these)
RUN apt-get update && apt-get install -y build-essential libpq-dev curl && rm -rf /var/lib/apt/lists/*

COPY app/ app/
COPY etl/ etl/
COPY pyproject.toml* requirements.txt* .env* ./ 2>/dev/null || true

RUN pip install --upgrade pip && \
    ( [ -f requirements.txt ] && pip install -r requirements.txt || \
      pip install fastapi uvicorn[standard] sqlalchemy[asyncio] asyncpg pydantic yfinance pandas python-dotenv )

ENV PYTHONPATH=/app
ENV PORT=8000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
