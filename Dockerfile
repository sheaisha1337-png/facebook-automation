# ──────────────────────────────────────────────────────────────────
# Facebook Automation — Python 3.11 + FFmpeg + Deno EJS runtime
# ──────────────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    imagemagick \
    libmagic1 \
    fonts-dejavu-core \
    fonts-liberation \
    curl \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp requires a supported JavaScript runtime for YouTube EJS challenges.
ENV DENO_INSTALL=/root/.deno
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/root/.deno/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
RUN deno --version

RUN sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml 2>/dev/null || true && \
    sed -i 's/<policy domain="path" rights="none" pattern="@\*"\/>//' /etc/ImageMagick-6/policy.xml 2>/dev/null || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn Pillow requests

COPY . .
RUN mkdir -p uploads outputs && chmod 777 uploads outputs

ENV PORT=7860
ENV HOST=0.0.0.0
EXPOSE 7860
RUN chmod +x start.sh
CMD ["/app/start.sh"]
