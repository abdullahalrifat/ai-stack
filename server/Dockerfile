FROM python:3.12-slim

WORKDIR /app

# Prevent Python buffering in Docker logs
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
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

RUN pip install --no-cache-dir \
    -r requirements.lock && \
    pip install --no-cache-dir "jarvis-agent-core>=0.2,<0.3"


COPY app ./app
COPY evals ./evals

# -----------------------------
# Build validation
# -----------------------------

# Check Python syntax
RUN python -m compileall app

# Create workspace mount point
RUN mkdir -p /workspace

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
