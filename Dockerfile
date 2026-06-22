# ──────────────────────────────────────────────────────────────────
#  Jigarzzz❤️ — Hugging Face Spaces Dockerfile
#  Python 3.11 slim + FFmpeg + all deps
# ──────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install system deps: FFmpeg + ImageMagick (for moviepy text clips) + fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    imagemagick \
    libmagic1 \
    fonts-dejavu-core \
    fonts-liberation \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ImageMagick policy fix — allow reading/writing all file types (needed by moviepy)
RUN sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml 2>/dev/null || true && \
    sed -i 's/<policy domain="path" rights="none" pattern="@\*"\/>//' /etc/ImageMagick-6/policy.xml 2>/dev/null || true

# Set working directory
WORKDIR /app

# Copy and install Python dependencies first (for layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn Pillow requests

# Copy application source
COPY . .

# Create writable directories for uploads and outputs
RUN mkdir -p uploads outputs && chmod 777 uploads outputs

# Hugging Face Spaces requires the app to listen on port 7860
ENV PORT=7860
ENV HOST=0.0.0.0

# Expose port
EXPOSE 7860

# Run with gunicorn — 1 worker, 4 threads, 300s timeout for long video jobs
CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "1", "--threads", "4", "--timeout", "300", "--access-logfile", "-", "app:app"]
