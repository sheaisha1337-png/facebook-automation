#!/bin/bash
# Start Tor daemon with custom data directory and PID file (writable by UID 1000)
echo "[startup] Starting Tor daemon..."
mkdir -p /tmp/tor-data
chmod 700 /tmp/tor-data
tor --RunAsDaemon 1 --SocksPort 127.0.0.1:9050 --DataDirectory /tmp/tor-data --PidFile /tmp/tor.pid --Log "notice file /tmp/tor.log"

# Wait for Tor to bootstrap
echo "[startup] Checking Tor connection..."
BOOTSTRAPPED=false
for i in {1..20}; do
    if curl --connect-timeout 2 -sI -x socks5h://127.0.0.1:9050 https://www.google.com >/dev/null; then
        echo "[startup] Tor proxy is ready and routing traffic!"
        BOOTSTRAPPED=true
        break
    fi
    echo "[startup] Tor is bootstrapping, waiting..."
    sleep 2
done

if [ "$BOOTSTRAPPED" = false ]; then
    echo "[startup] Tor failed to bootstrap within 40 seconds."
fi

# Print Tor log for debugging
echo "=== Tor Log Start ==="
cat /tmp/tor.log 2>/dev/null || echo "No Tor log found"
echo "=== Tor Log End ==="

# Start Gunicorn application
echo "[startup] Starting Gunicorn server..."
exec gunicorn --bind 0.0.0.0:7860 --workers 1 --threads 4 --timeout 300 --access-logfile - app:app
