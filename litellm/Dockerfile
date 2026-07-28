FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

RUN pip install --no-cache-dir \
    "litellm[proxy]" \
    async_generator

COPY direct_client_guard.py /app/direct_client_guard.py

EXPOSE 4000

CMD ["litellm", "--config", "/app/config.yaml", "--port", "4000"]
