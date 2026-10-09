FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    git \
    make \
    util-linux \
    nodejs \
    npm \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./

RUN pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY evals ./evals

RUN python -m compileall app
RUN python -c "import importlib.metadata as m; assert m.version('jarvis-agent-core') == '0.17.2'"
RUN mkdir -p /workspace

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
