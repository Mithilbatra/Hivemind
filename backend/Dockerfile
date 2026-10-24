FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
WORKDIR /srv
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install --no-cache-dir . && rm -rf app
COPY . .
RUN mkdir -p /data && chmod 777 /data
ENV PYTHONPATH=/srv PYTHONUNBUFFERED=1 HOME=/tmp
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
