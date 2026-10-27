FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
WORKDIR /srv
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN mkdir -p /data && chmod 777 /data
ENV PYTHONPATH=/srv PYTHONUNBUFFERED=1 HOME=/tmp
EXPOSE 8000
# exec: uvicorn becomes PID 1 and gets SIGTERM, so `docker compose stop` shuts it down cleanly
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
