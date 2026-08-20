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

RUN pip install --no-cache-dir -r requirements.lock && \
    pip install --no-cache-dir \
      "jarvis-agent-core @ https://github.com/abdullahalrifat/jarvis-core/archive/${JARVIS_CORE_REF}.zip"

COPY app ./app
COPY evals ./evals

RUN python -m compileall app
RUN mkdir -p /workspace

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
