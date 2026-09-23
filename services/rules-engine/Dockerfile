FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && addgroup --system app \
    && adduser --system --ingroup app app

COPY src ./src
COPY tests ./tests
COPY pytest.ini .

USER app
EXPOSE 8000
CMD ["uvicorn", "rules_engine.main:app", "--host", "0.0.0.0", "--port", "8000"]
