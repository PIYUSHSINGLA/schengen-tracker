FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Install Python deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser for Playwright
RUN playwright install chromium --with-deps

# Copy application source
COPY src/ ./src/
COPY config/ ./config/

# SQLite data directory — mount as volume in compose
RUN mkdir -p /app/data

# Non-root user for security
RUN useradd -m -u 1000 tracker && chown -R tracker:tracker /app
USER tracker

CMD ["python", "-m", "src.main"]
