#!/bin/bash
# Production profile for Render Pro (4 GB RAM / 2 CPU)
echo "[startup] Starting Gunicorn server..."
exec gunicorn --bind 0.0.0.0:7860 --workers 1 --threads 2 --timeout 1800 --graceful-timeout 60 --keep-alive 5 --access-logfile - app:app
