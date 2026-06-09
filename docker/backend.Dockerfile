FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY config ./config
COPY migrations ./migrations

EXPOSE 8000

CMD ["uvicorn", "municipality.api:app", "--host", "0.0.0.0", "--port", "8000"]
