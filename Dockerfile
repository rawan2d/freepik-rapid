# Use Python 3.9 slim with better Playwright support
FROM python:3.9-slim

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system dependencies required by Playwright / Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    ca-certificates \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libxss1 \
    libasound2 \
    libx11-xcb1 \
    libxtst6 \
    libatk1.0-0 \
    libcairo2 \
    libpango-1.0-0 \
    libgtk-3-0 \
    xvfb \
    fonts-liberation \
    fonts-noto-color-emoji \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/*

WORKDIR /app

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN pip install playwright==1.40.0 && \
    python -m playwright install chromium

# Copy application into image
COPY . .

# Create directories
RUN mkdir -p /app/logs /app/screenshots

# Startup script for Railway/RapidAPI
RUN echo '#!/bin/bash\n\
echo "=== Freepik Downloader API Starting ==="\n\
echo "Checking Playwright installation..."\n\
python -c "from playwright.sync_api import sync_playwright; print(\"Playwright OK\")" || echo \"Playwright check failed\"\n\
echo "Starting virtual display..."\n\
Xvfb :99 -screen 0 1280x720x24 > /dev/null 2>&1 &\n\
export DISPLAY=:99\n\
sleep 2\n\
if [ -f /app/.env ]; then\n\
  echo \"Loading environment variables from /app/.env\"\n\
  set -o allexport\n\
  . /app/.env\n\
  set +o allexport\n\
fi\n\
echo \"Starting FastAPI server on port ${PORT:-8000}...\"\n\
python -m uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}' > /app/start.sh && chmod +x /app/start.sh

EXPOSE 8000

CMD ["/app/start.sh"]
