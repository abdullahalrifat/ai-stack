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
COPY render_config.py /app/render_config.py
COPY start.sh /app/start.sh
RUN chmod 0755 /app/start.sh

EXPOSE 4000

ENTRYPOINT ["/app/start.sh"]
CMD ["--host=0.0.0.0", "--port=4000"]
