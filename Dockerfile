FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create static directory
RUN mkdir -p static

EXPOSE 8000

# Default: seed then start (override in docker-compose if needed)
CMD ["sh", "-c", "python seed.py && uvicorn app:app --host 0.0.0.0 --port 8000"]
