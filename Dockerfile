FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN test -d /app/app/templates \
    && test -d /app/app/static \
    && test -f /app/app/templates/base.html \
    && test -f /app/app/templates/index.html \
    && test -f /app/app/templates/_cycle_panels.html \
    && test -f /app/app/static/styles.css

RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
