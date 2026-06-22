#!/bin/bash
# Start Gunicorn application directly (Tor disabled for Hugging Face policy compliance)
echo "[startup] Starting Gunicorn server..."
exec gunicorn --bind 0.0.0.0:7860 --workers 1 --threads 4 --timeout 300 --access-logfile - app:app
