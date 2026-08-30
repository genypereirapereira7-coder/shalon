FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        "fastapi>=0.115" "uvicorn[standard]>=0.32" "sqlalchemy[asyncio]>=2.0.36" \
        "asyncpg>=0.30" "alembic>=1.14" "pydantic>=2.9" "pydantic-settings>=2.6" \
        "pyjwt>=2.10" "bcrypt>=4.2" "python-multipart>=0.0.17" "tzdata>=2024.2"

COPY . .

EXPOSE 8000

# Um worker só: o gerenciador de WebSocket guarda as conexões em memória (seção 9).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
