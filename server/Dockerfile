# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

ARG JARVIS_CORE_REF=e1aac1e25f776b0cb329424b95a419ce7a42d415

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    git \
    make \
    nodejs \
    npm \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./

RUN pip install --no-cache-dir -r requirements.lock

# Core is private. BuildKit mounts the read-only GitHub token for this command
# only; it is not copied into the image, build arguments, or layer history.
RUN --mount=type=secret,id=core_repo_token,required=true \
    git -c "http.extraHeader=Authorization: Bearer $(cat /run/secrets/core_repo_token)" \
      clone --filter=blob:none --no-checkout \
      https://github.com/abdullahalrifat/jarvis-core.git /tmp/jarvis-core && \
    git -C /tmp/jarvis-core checkout "${JARVIS_CORE_REF}" && \
    pip install --no-cache-dir /tmp/jarvis-core && \
    rm -rf /tmp/jarvis-core

COPY app ./app
COPY evals ./evals

RUN python -m compileall app
RUN mkdir -p /workspace

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
